"""FastAPI dependencies for authentication and RBAC."""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError

from app.auth.security import decode_token
from app.schemas.auth import TokenUser

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
) -> TokenUser:
    """Decode JWT and return user from token claims. No DB query needed."""
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

    return TokenUser(
        id=user_id,
        username=payload.get("username", ""),
        role=payload.get("role", ""),
        email=payload.get("email", ""),
    )


# Convenience type alias
CurrentUser = Annotated[TokenUser, Depends(get_current_user)]


def require_role(*allowed_roles: str):
    """Dependency factory that enforces role-based access.

    Usage:
        @router.post("/rules", dependencies=[Depends(require_role("admin"))])
    """

    async def _check_role(current_user: CurrentUser) -> TokenUser:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user

    return _check_role
