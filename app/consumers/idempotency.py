"""Idempotency service for preventing duplicate message processing."""

import json
from datetime import timedelta
from typing import Any

from redis.asyncio import Redis


class IdempotencyService:
    """
    Redis-based idempotency service.

    Uses Redis to track processed messages and prevent duplicates.
    Key format: idempotency:{message_type}:{external_id}
    TTL: Configurable (default 24 hours)
    """

    DEFAULT_TTL = timedelta(hours=24)
    KEY_PREFIX = "idempotency"

    def __init__(self, redis: Redis, ttl: timedelta | None = None):
        self.redis = redis
        self.ttl = ttl or self.DEFAULT_TTL

    def _make_key(self, message_type: str, external_id: str) -> str:
        """Create Redis key for idempotency check."""
        return f"{self.KEY_PREFIX}:{message_type}:{external_id}"

    async def is_processed(self, message_type: str, external_id: str) -> bool:
        """
        Check if a message has already been processed.

        Args:
            message_type: Type of message (e.g., "transaction")
            external_id: Unique identifier for the message

        Returns:
            True if message was already processed, False otherwise
        """
        key = self._make_key(message_type, external_id)
        return await self.redis.exists(key) > 0

    async def mark_processed(
        self,
        message_type: str,
        external_id: str,
        result: dict[str, Any] | None = None,
    ) -> None:
        """
        Mark a message as processed.

        Args:
            message_type: Type of message
            external_id: Unique identifier for the message
            result: Optional result data to store
        """
        key = self._make_key(message_type, external_id)
        value = json.dumps(result) if result else "1"
        await self.redis.setex(key, self.ttl, value)

    async def get_result(self, message_type: str, external_id: str) -> dict[str, Any] | None:
        """
        Get the stored result for a previously processed message.

        Returns None if message wasn't processed or result wasn't stored.
        """
        key = self._make_key(message_type, external_id)
        value = await self.redis.get(key)
        if value and value != "1":
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return None
        return None

    async def acquire_lock(
        self,
        message_type: str,
        external_id: str,
        lock_ttl: timedelta | None = None,
    ) -> bool:
        """
        Acquire a processing lock for a message.

        Uses Redis SETNX for atomic lock acquisition.
        This prevents race conditions when multiple consumers
        try to process the same message.

        Returns True if lock was acquired, False otherwise.
        """
        key = f"lock:{self._make_key(message_type, external_id)}"
        ttl = lock_ttl or timedelta(minutes=5)

        # SETNX - Set if Not eXists
        acquired = await self.redis.set(key, "1", nx=True, ex=int(ttl.total_seconds()))
        return bool(acquired)

    async def release_lock(self, message_type: str, external_id: str) -> None:
        """Release a processing lock."""
        key = f"lock:{self._make_key(message_type, external_id)}"
        await self.redis.delete(key)


class IdempotentProcessor:
    """
    Context manager for idempotent message processing.

    Usage:
        async with IdempotentProcessor(idempotency, "transaction", "TXN-123") as processor:
            if processor.should_process:
                result = await process_transaction(...)
                processor.set_result(result)
    """

    def __init__(
        self,
        service: IdempotencyService,
        message_type: str,
        external_id: str,
    ):
        self.service = service
        self.message_type = message_type
        self.external_id = external_id
        self.should_process = True
        self.cached_result: dict[str, Any] | None = None
        self._result: dict[str, Any] | None = None
        self._lock_acquired = False

    async def __aenter__(self) -> "IdempotentProcessor":
        # Check if already processed
        if await self.service.is_processed(self.message_type, self.external_id):
            self.should_process = False
            self.cached_result = await self.service.get_result(self.message_type, self.external_id)
            return self

        # Try to acquire lock — release on failure so __aexit__ doesn't
        # try to release a lock we never held (M42).
        try:
            self._lock_acquired = await self.service.acquire_lock(
                self.message_type, self.external_id
            )
        except Exception:
            self._lock_acquired = False
            raise

        if not self._lock_acquired:
            # Another consumer is processing this message
            self.should_process = False

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        try:
            if exc_type is None and self.should_process and self._result is not None:
                # Mark as processed if successful
                await self.service.mark_processed(self.message_type, self.external_id, self._result)
        finally:
            # Always release the lock, even if mark_processed() fails
            if self._lock_acquired:
                await self.service.release_lock(self.message_type, self.external_id)

    def set_result(self, result: dict[str, Any]) -> None:
        """Set the processing result to be stored."""
        self._result = result
