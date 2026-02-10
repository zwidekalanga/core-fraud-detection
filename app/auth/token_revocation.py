"""Lightweight Redis-backed JWT deny-list.

Allows individual tokens to be revoked before their natural expiry by
storing the token's ``jti`` (JWT ID) claim in Redis with a TTL matching
the token's remaining lifetime.

Usage from an admin endpoint or incident-response script::

    from app.auth.token_revocation import revoke_token, is_token_revoked

    await revoke_token(redis, jti, expires_in_seconds=1800)
    assert await is_token_revoked(redis, jti)
"""

from redis.asyncio import Redis

_DENY_PREFIX = "token:deny:"


async def revoke_token(redis: Redis, jti: str, expires_in_seconds: int) -> None:
    """Add *jti* to the deny-list with a TTL equal to the token's remaining lifetime."""
    if expires_in_seconds > 0:
        await redis.setex(f"{_DENY_PREFIX}{jti}", expires_in_seconds, "1")


async def is_token_revoked(redis: Redis, jti: str) -> bool:
    """Return ``True`` if *jti* has been revoked."""
    return await redis.exists(f"{_DENY_PREFIX}{jti}") > 0
