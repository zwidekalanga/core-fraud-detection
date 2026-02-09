"""Dependency injection for FastAPI."""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings, get_settings

# Database engine (lazy initialization)
_engine = None
_session_factory = None


def get_engine(settings: Settings):
    """Get or create async database engine."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            str(settings.database_url),
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            echo=settings.debug,
        )
    return _engine


def get_session_factory(settings: Settings) -> async_sessionmaker[AsyncSession]:
    """Get or create session factory."""
    global _session_factory
    if _session_factory is None:
        engine = get_engine(settings)
        _session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


async def get_db_session(
    settings: Annotated[Settings, Depends(get_settings)],
) -> AsyncGenerator[AsyncSession, None]:
    """Dependency that provides a database session."""
    session_factory = get_session_factory(settings)
    async with session_factory() as session:
        try:
            yield session
        finally:
            await session.close()


# Redis client (lazy initialization)
_redis_client = None


async def get_redis(
    settings: Annotated[Settings, Depends(get_settings)],
) -> AsyncGenerator[Redis, None]:
    """Dependency that provides a Redis client."""
    global _redis_client
    if _redis_client is None:
        _redis_client = Redis.from_url(
            str(settings.redis_url),
            decode_responses=True,
        )
    yield _redis_client


# Type aliases for cleaner dependency injection
DBSession = Annotated[AsyncSession, Depends(get_db_session)]
RedisClient = Annotated[Redis, Depends(get_redis)]
AppSettings = Annotated[Settings, Depends(get_settings)]
