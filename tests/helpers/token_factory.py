"""JWT token creation helpers for tests.

These functions exist ONLY for test usage. Token issuance in production
is owned by core-banking. They were intentionally removed from
``app.auth.security`` to enforce the principle of least privilege.
"""

from datetime import UTC, datetime, timedelta

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
