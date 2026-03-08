"""Dependency injection for FastAPI."""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings

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


def get_redis(request: Request) -> Redis:
    """Dependency that provides the Redis client from app.state."""
    return request.app.state.redis  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Type aliases for cleaner dependency injection
# ---------------------------------------------------------------------------
DBSession = Annotated[AsyncSession, Depends(get_db_session)]
RedisClient = Annotated[Redis, Depends(get_redis)]
AppSettings = Annotated[Settings, Depends(get_settings)]
