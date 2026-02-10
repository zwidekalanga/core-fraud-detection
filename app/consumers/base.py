"""Base Kafka consumer with common functionality."""

import asyncio
import json
import logging
import signal
import time
from abc import ABC, abstractmethod
from typing import Any

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, TopicPartition
from aiokafka.errors import KafkaError

from app.config import Settings
from app.telemetry import kafka_span

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
        max_retries: int = 3,
    ):
        self.settings = settings
        self.topic = topic
        self.group_id = group_id or settings.kafka_consumer_group
        self.dlq_topic = dlq_topic
        self.max_retries = max_retries

        self._consumer: AIOKafkaConsumer | None = None
        self._producer: AIOKafkaProducer | None = None
        self._stop_event = asyncio.Event()
        self._healthy = False

    async def start(self) -> None:
        """Start the consumer."""
        logger.info("Starting consumer for topic: %s", self.topic)

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
        self._stop_event.clear()
        self._healthy = True

        # Setup signal handlers — set the event (thread-safe) instead of
        # creating a task from a signal handler (race-prone).
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._stop_event.set)

        logger.info("Consumer started for topic: %s", self.topic)

    async def stop(self) -> None:
        """Stop the consumer gracefully."""
        logger.info("Stopping consumer...")
        self._stop_event.set()
        self._healthy = False

        if self._producer:
            await self._producer.flush()  # H15: flush pending DLQ messages
            await self._producer.stop()

        if self._consumer:
            await self._consumer.stop()

        logger.info("Consumer stopped")

    async def run(self) -> None:
        """Main consumer loop."""
        await self.start()

        if self._consumer is None:
            raise RuntimeError("Consumer failed to initialise")

        last_lag_log = 0.0

        try:
            async for message in self._consumer:
                if self._stop_event.is_set():
                    break

                await self._process_with_retry(message)

                # Log consumer lag every 60 seconds
                now = time.monotonic()
                if now - last_lag_log >= 60:
                    last_lag_log = now
                    await self._log_consumer_lag()

        finally:
            await self.stop()

    async def _log_consumer_lag(self) -> None:
        """Log per-partition consumer lag (committed offset vs high watermark)."""
        if self._consumer is None:
            return
        try:
            partitions = self._consumer.assignment()
            for tp in partitions:
                committed = await self._consumer.committed(tp)
                end_offsets = await self._consumer.end_offsets([tp])
                high_watermark = end_offsets.get(tp, 0)
                lag = high_watermark - (committed or 0)
                logger.info(
                    "Consumer lag: topic=%s partition=%s lag=%d",
                    tp.topic,
                    tp.partition,
                    lag,
                )
        except Exception:
            logger.debug("Unable to fetch consumer lag", exc_info=True)

    async def _process_with_retry(self, message: Any) -> None:
        """Process a message with H15 retry-before-DLQ logic."""
        if self._consumer is None:
            return

        async with kafka_span(message.topic, message.partition, message.offset):
            await self._process_with_retry_inner(message)

    async def _process_with_retry_inner(self, message: Any) -> None:
        """Inner retry logic wrapped by the tracing span."""
        if self._consumer is None:
            return

        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                await self._process_message(message)
                await self._consumer.commit()
                return  # success
            except Exception as e:
                last_exc = e
                logger.warning(
                    "Attempt %d/%d failed for offset %s: %s",
                    attempt,
                    self.max_retries,
                    message.offset,
                    e,
                    extra={
                        "topic": message.topic,
                        "partition": message.partition,
                        "offset": message.offset,
                    },
                )
                if attempt < self.max_retries:
                    await asyncio.sleep(min(2**attempt, 30))  # exponential backoff

        # All retries exhausted — send to DLQ
        logger.error(
            "All %d retries exhausted for offset %s — sending to DLQ",
            self.max_retries,
            message.offset,
        )
        if self.dlq_topic and self._producer:
            await self._send_to_dlq(message, str(last_exc))

        # Commit to avoid infinite retry loop
        await self._consumer.commit()

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
            logger.info("Sent message to DLQ: %s", self.dlq_topic)
        except KafkaError as e:
            logger.error("Failed to send to DLQ: %s", e)

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
