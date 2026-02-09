"""JWT token utilities.

Password hashing has been removed — token issuance is owned by core-banking.
This module only handles token decoding for stateless validation.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from jose import jwt

from app.config import get_settings


def create_access_token(user_id: str, role: str, username: str = "", email: str = "") -> str:
    """Create a short-lived JWT access token."""
    settings = get_settings()
    expire = datetime.now(UTC) + timedelta(minutes=settings.jwt_access_token_expire_minutes)
    payload = {
        "sub": user_id,
        "role": role,
        "username": username,
        "email": email,
        "type": "access",
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: str, role: str, username: str = "", email: str = "") -> str:
    """Create a long-lived JWT refresh token."""
    settings = get_settings()
    expire = datetime.now(UTC) + timedelta(minutes=settings.jwt_refresh_token_expire_minutes)
    payload = {
        "sub": user_id,
        "role": role,
        "username": username,
        "email": email,
        "type": "refresh",
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT token. Raises JWTError on failure."""
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
