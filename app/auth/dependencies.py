"""FastAPI dependencies for authentication and RBAC."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError

from app.auth.security import decode_token
from app.auth.token_revocation import is_token_revoked
from app.models.user import UserRole
from app.schemas.auth import TokenUser

# Token issuance (login/refresh) is owned by core-banking on port 8001.
# The tokenUrl below is used only for Swagger UI's "Authorize" dialog.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="http://localhost:8001/api/v1/auth/login")


async def get_current_user(
    request: Request,
    token: Annotated[str, Depends(oauth2_scheme)],
) -> TokenUser:
    """Decode JWT, check deny-list, and return user from token claims."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
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
                headers={"WWW-Authenticate": "Bearer"},
            )

    return TokenUser(
        id=user_id,
        username=payload.get("username", ""),
        role=payload.get("role", ""),
        email=payload.get("email", ""),
    )


# Convenience type alias
CurrentUser = Annotated[TokenUser, Depends(get_current_user)]


def require_role(*allowed_roles: str | UserRole):
    """Dependency factory that enforces role-based access.

    Usage:
        @router.post("/rules", dependencies=[Depends(require_role("admin"))])
    """
    allowed = {UserRole(r) if isinstance(r, str) else r for r in allowed_roles}

    async def _check_role(current_user: CurrentUser) -> TokenUser:
        if current_user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user

    return _check_role
