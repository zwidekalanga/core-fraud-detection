"""Dependency injection for FastAPI."""

import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import QueuePool

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lifespan helpers — called from main.py to create & destroy shared resources
# ---------------------------------------------------------------------------


def _register_pool_events(engine: AsyncEngine) -> None:
    """Attach pool event listeners for observability."""
    pool = engine.pool
    if not isinstance(pool, QueuePool):
        return

    @event.listens_for(pool, "checkout")
    def _on_checkout(_dbapi_conn, _conn_record, _conn_proxy):
        logger.debug("Pool checkout — size=%s checked_out=%s", pool.size(), pool.checkedout())

    @event.listens_for(pool, "checkin")
    def _on_checkin(_dbapi_conn, _conn_record):
        logger.debug("Pool checkin — size=%s checked_out=%s", pool.size(), pool.checkedout())

    @event.listens_for(pool, "overflow")
    def _on_overflow(_dbapi_conn):
        logger.warning("Pool overflow — size=%s overflow=%s", pool.size(), pool.overflow())


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
    return Redis.from_url(str(settings.redis_url), decode_responses=True)


# ---------------------------------------------------------------------------
# FastAPI dependencies — pull resources from app.state (set in lifespan)
# ---------------------------------------------------------------------------


async def get_db_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """Dependency that provides a database session from app.state.

    Owns the unit-of-work lifecycle: commits on success, rolls back on
    exception.  Repositories should call ``session.flush()`` (not
    ``session.commit()``) so that all writes within a single request
    are committed atomically.
    """
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_redis(request: Request) -> AsyncGenerator[Redis, None]:
    """Dependency that provides the Redis client from app.state."""
    yield request.app.state.redis


# ---------------------------------------------------------------------------
# Standalone infrastructure for gRPC / Kafka consumer (no FastAPI app)
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


# ---------------------------------------------------------------------------
# Type aliases for cleaner dependency injection
# ---------------------------------------------------------------------------
DBSession = Annotated[AsyncSession, Depends(get_db_session)]
RedisClient = Annotated[Redis, Depends(get_redis)]
AppSettings = Annotated[Settings, Depends(get_settings)]
