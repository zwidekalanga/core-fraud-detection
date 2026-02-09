"""Base Kafka consumer with common functionality."""

import asyncio
import json
import logging
import signal
from abc import ABC, abstractmethod
from typing import Any

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.errors import KafkaError

from app.config import Settings

logger = logging.getLogger(__name__)


class BaseConsumer(ABC):
    """
    Abstract base class for Kafka consumers.

    Provides:
    - Graceful startup/shutdown
    - Signal handling (SIGINT, SIGTERM)
    - Error handling with DLQ support
    - Health check endpoint
    """

    def __init__(
        self,
        settings: Settings,
        topic: str,
        group_id: str | None = None,
        dlq_topic: str | None = None,
    ):
        self.settings = settings
        self.topic = topic
        self.group_id = group_id or settings.kafka_consumer_group
        self.dlq_topic = dlq_topic

        self._consumer: AIOKafkaConsumer | None = None
        self._producer: AIOKafkaProducer | None = None
        self._running = False
        self._healthy = False

    async def start(self) -> None:
        """Start the consumer."""
        logger.info(f"Starting consumer for topic: {self.topic}")

        # Create consumer
        self._consumer = AIOKafkaConsumer(
            self.topic,
            bootstrap_servers=self.settings.kafka_bootstrap_servers,
            group_id=self.group_id,
            auto_offset_reset=self.settings.kafka_auto_offset_reset,
            enable_auto_commit=False,  # Manual commits for reliability
            value_deserializer=lambda m: m.decode("utf-8"),
        )

        # Create producer for DLQ if configured
        if self.dlq_topic:
            self._producer = AIOKafkaProducer(
                bootstrap_servers=self.settings.kafka_bootstrap_servers,
                value_serializer=lambda m: m.encode("utf-8"),
            )
            await self._producer.start()

        await self._consumer.start()
        self._running = True
        self._healthy = True

        # Setup signal handlers
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))

        logger.info(f"Consumer started for topic: {self.topic}")

    async def stop(self) -> None:
        """Stop the consumer gracefully."""
        logger.info("Stopping consumer...")
        self._running = False
        self._healthy = False

        if self._consumer:
            await self._consumer.stop()

        if self._producer:
            await self._producer.stop()

        logger.info("Consumer stopped")

    async def run(self) -> None:
        """Main consumer loop."""
        await self.start()

        if self._consumer is None:
            raise RuntimeError("Consumer failed to initialise")

        try:
            async for message in self._consumer:
                if not self._running:
                    break

                try:
                    await self._process_message(message)

                    # Commit offset after successful processing
                    await self._consumer.commit()

                except Exception as e:
                    logger.error(
                        f"Error processing message: {e}",
                        extra={
                            "topic": message.topic,
                            "partition": message.partition,
                            "offset": message.offset,
                        },
                    )

                    # Send to DLQ if configured
                    if self.dlq_topic and self._producer:
                        await self._send_to_dlq(message, str(e))

                    # Commit anyway to prevent infinite retry loop
                    await self._consumer.commit()

        finally:
            await self.stop()

    async def _process_message(self, message: Any) -> None:
        """Process a single message with error handling."""
        logger.debug(
            "Processing message",
            extra={
                "topic": message.topic,
                "partition": message.partition,
                "offset": message.offset,
            },
        )

        # Call the abstract method implemented by subclasses
        await self.process(message)

    async def _send_to_dlq(self, message: Any, error: str) -> None:
        """Send failed message to dead letter queue."""
        if not self._producer or not self.dlq_topic:
            return

        dlq_message = json.dumps(
            {
                "original_topic": message.topic,
                "original_partition": message.partition,
                "original_offset": message.offset,
                "original_timestamp": message.timestamp,
                "original_value": message.value,
                "error": error,
            }
        )

        try:
            await self._producer.send_and_wait(self.dlq_topic, dlq_message)
            logger.info(f"Sent message to DLQ: {self.dlq_topic}")
        except KafkaError as e:
            logger.error(f"Failed to send to DLQ: {e}")

    @property
    def is_healthy(self) -> bool:
        """Check if consumer is healthy."""
        return self._healthy

    @abstractmethod
    async def process(self, message: Any) -> None:
        """
        Process a Kafka message.

        Must be implemented by subclasses.

        Args:
            message: Kafka message to process
        """
        pass
