"""Dependency injection for FastAPI."""

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings, get_settings

# ---------------------------------------------------------------------------
# Lifespan helpers — called from main.py to create & destroy shared resources
# ---------------------------------------------------------------------------


def create_engine(settings: Settings) -> AsyncEngine:
    """Create the async database engine."""
    return create_async_engine(
        str(settings.database_url),
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        echo=settings.debug,
    )


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
