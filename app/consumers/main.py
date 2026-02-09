"""Kafka consumer entry point."""

import asyncio
import logging
import sys

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.consumers.transaction_consumer import TransactionConsumer
from app.utils.logging import setup_logging

logger = logging.getLogger(__name__)


async def main() -> None:
    """Main entry point for the Kafka consumer."""
    settings = get_settings()
    setup_logging(settings.log_level)

    logger.info("Starting Fraud Detection Service Consumer...")
    logger.info(f"Kafka servers: {settings.kafka_bootstrap_servers}")
    logger.info(f"Consumer group: {settings.kafka_consumer_group}")

    # Create database engine and session factory
    engine = create_async_engine(
        str(settings.database_url),
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # Create Redis client
    redis = Redis.from_url(str(settings.redis_url), decode_responses=True)

    try:
        # Create and run consumer
        consumer = TransactionConsumer(
            settings=settings,
            session_factory=session_factory,
            redis=redis,
        )

        await consumer.run()

    except KeyboardInterrupt:
        logger.info("Consumer interrupted by user")

    except Exception as e:
        logger.error(f"Consumer failed: {e}")
        sys.exit(1)

    finally:
        await redis.aclose()
        await engine.dispose()
        logger.info("Consumer shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
