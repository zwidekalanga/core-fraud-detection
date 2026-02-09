"""Kafka consumer entry point."""

import asyncio
import logging
import sys

from app.config import get_settings
from app.consumers.transaction_consumer import TransactionConsumer
from app.dependencies import InfrastructureContainer
from app.utils.logging import setup_logging

logger = logging.getLogger(__name__)


async def main() -> None:
    """Main entry point for the Kafka consumer."""
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_format)

    logger.info("Starting Fraud Detection Service Consumer...")
    logger.info("Kafka servers: %s", settings.kafka_bootstrap_servers)
    logger.info("Consumer group: %s", settings.kafka_consumer_group)

    infra = InfrastructureContainer.from_settings(settings)

    try:
        consumer = TransactionConsumer(
            settings=settings,
            session_factory=infra.session_factory,
            redis=infra.redis,
        )

        await consumer.run()

    except KeyboardInterrupt:
        logger.info("Consumer interrupted by user")

    except Exception as e:
        logger.error("Consumer failed: %s", e)
        sys.exit(1)

    finally:
        await infra.close()
        logger.info("Consumer shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
