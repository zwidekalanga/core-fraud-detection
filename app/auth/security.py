"""JWT token utilities.

Token issuance (login / refresh) is owned by core-banking.
This module only handles token *decoding* for stateless validation.
Token creation helpers live in ``tests/helpers/token_factory.py``
and must never be imported from production code.

Token revocation
~~~~~~~~~~~~~~~~
This service uses **stateless JWT validation** (no DB or Redis lookup per
request).  As a consequence, individual tokens cannot be revoked before
their natural expiry.  This is an intentional trade-off:

* **Benefit**: Zero-latency auth check with no DB round-trip.
* **Mitigation**: Access tokens are short-lived (30 min).  If a user must
  be blocked immediately, rotate the shared ``JWT_SECRET_KEY`` to
  invalidate *all* tokens, or add a Redis-backed deny-list keyed on
  ``jti`` if per-token revocation becomes a requirement.
"""

from typing import Any

from jose import jwt

from app.config import get_settings


def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT token. Raises JWTError on failure."""
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
