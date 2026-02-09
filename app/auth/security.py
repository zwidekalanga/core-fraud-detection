"""JWT token utilities.

Token issuance (login / refresh) is owned by core-banking.
This module only handles token *decoding* for stateless validation.
Token creation helpers live in ``tests/helpers/token_factory.py``
and must never be imported from production code.
"""

from typing import Any

from jose import jwt

from app.config import get_settings


def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT token. Raises JWTError on failure."""
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
