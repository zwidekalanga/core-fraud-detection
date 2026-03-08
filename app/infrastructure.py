"""Shared infrastructure — engine, session factory, Redis, and container."""

import logging
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import QueuePool

from app.config import Settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pool observability
# ---------------------------------------------------------------------------


def _register_pool_events(engine: AsyncEngine) -> None:
    """Attach pool event listeners for observability."""
    pool = engine.pool
    if not isinstance(pool, QueuePool):
        return

    @event.listens_for(pool, "checkout")
    def _on_checkout(_dbapi_conn: Any, _conn_record: Any, _conn_proxy: Any) -> None:
        logger.debug("Pool checkout — size=%s checked_out=%s", pool.size(), pool.checkedout())

    @event.listens_for(pool, "checkin")
    def _on_checkin(_dbapi_conn: Any, _conn_record: Any) -> None:
        logger.debug("Pool checkin — size=%s checked_out=%s", pool.size(), pool.checkedout())


# ---------------------------------------------------------------------------
# Factory functions — used by lifespan (main.py) and InfrastructureContainer
# ---------------------------------------------------------------------------


def create_engine(settings: Settings) -> AsyncEngine:
    """Create the async database engine."""
    engine = create_async_engine(
        str(settings.database_url),
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        echo=settings.debug,
    )
    _register_pool_events(engine)
    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create session factory bound to *engine*."""
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def create_redis(settings: Settings) -> Redis:
    """Create the async Redis client."""
    return Redis.from_url(str(settings.redis_url), decode_responses=True)  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Standalone container for gRPC / Kafka consumer (no FastAPI app)
# ---------------------------------------------------------------------------


@dataclass
class InfrastructureContainer:
    """Holds shared async resources for non-FastAPI entry-points.

    Replaces the previous module-level global singletons with an explicit,
    immutable container that callers create and own.
    """

    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    redis: Redis

    @classmethod
    def from_settings(cls, settings: Settings) -> "InfrastructureContainer":
        """Factory that wires up engine, session factory, and Redis."""
        engine = create_engine(settings)
        return cls(
            engine=engine,
            session_factory=create_session_factory(engine),
            redis=create_redis(settings),
        )

    async def verify(self) -> None:
        """Verify DB and Redis connectivity. Call before accepting traffic."""
        async with self.session_factory() as session:
            await session.execute(text("SELECT 1"))
        logger.info("Database connectivity verified")
        await self.redis.ping()  # type: ignore[misc]  # redis.asyncio typing quirk
        logger.info("Redis connectivity verified")

    async def close(self) -> None:
        """Dispose of all managed resources."""
        await self.redis.aclose()
        await self.engine.dispose()
