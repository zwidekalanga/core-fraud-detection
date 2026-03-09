"""Dependency injection for FastAPI."""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer, SecurityScopes
from jose import JWTError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.security import decode_token
from app.auth.token_revocation import is_token_revoked
from app.config import Settings, get_settings
from app.schemas.auth import TokenUser

# ---------------------------------------------------------------------------
# Database
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


DBSession = Annotated[AsyncSession, Depends(get_db_session)]

# ---------------------------------------------------------------------------
# Redis
# ---------------------------------------------------------------------------


def get_redis(request: Request) -> Redis:
    """Dependency that provides the Redis client from app.state."""
    return request.app.state.redis  # type: ignore[no-any-return]


RedisClient = Annotated[Redis, Depends(get_redis)]

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

AppSettings = Annotated[Settings, Depends(get_settings)]

# ---------------------------------------------------------------------------
# Authentication & RBAC
# ---------------------------------------------------------------------------

# Token issuance (login/refresh) is owned by core-banking.
# The tokenUrl below is used only for Swagger UI's "Authorize" dialog.
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=get_settings().jwt_token_url,
    scopes={
        "admin": "Full administrative access",
        "analyst": "Review and investigate alerts",
        "viewer": "Read-only access",
    },
)


async def get_current_user(
    security_scopes: SecurityScopes,
    request: Request,
    token: Annotated[str, Depends(oauth2_scheme)],
) -> TokenUser:
    """Decode JWT, check deny-list, enforce required scopes, and return user.

    When called via ``Security(get_current_user, scopes=[...])``, the
    ``security_scopes`` parameter is populated automatically by FastAPI
    with the required scopes for the endpoint.  When called via plain
    ``Depends(get_current_user)`` (no scopes), any authenticated user
    is accepted.
    """
    authenticate_value = (
        f'Bearer scope="{security_scopes.scope_str}"' if security_scopes.scopes else "Bearer"
    )
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": authenticate_value},
    )
    try:
        payload = decode_token(token)
        user_id: str | None = payload.get("sub")
        token_type: str | None = payload.get("type")
        if user_id is None or token_type != "access":
            raise credentials_exception
    except JWTError as exc:
        raise credentials_exception from exc

    # Check Redis-backed deny-list for revoked tokens
    jti: str | None = payload.get("jti")
    if jti:
        redis = getattr(request.app.state, "redis", None)
        if redis and await is_token_revoked(redis, jti):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has been revoked",
                headers={"WWW-Authenticate": authenticate_value},
            )

    user = TokenUser(
        id=user_id,
        username=payload.get("username", ""),
        role=payload.get("role", ""),
        email=payload.get("email", ""),
    )

    # Enforce required scopes (roles) if specified
    if security_scopes.scopes and user.role not in security_scopes.scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions",
            headers={"WWW-Authenticate": authenticate_value},
        )

    return user


CurrentUser = Annotated[TokenUser, Depends(get_current_user)]
