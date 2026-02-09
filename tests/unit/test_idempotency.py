"""Unit tests for IdempotencyService and IdempotentProcessor."""

import json
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from app.consumers.idempotency import IdempotencyService, IdempotentProcessor


@pytest.fixture()
def mock_redis():
    """Create a mock Redis client."""
    redis = AsyncMock()
    redis.exists = AsyncMock(return_value=0)
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock()
    redis.set = AsyncMock(return_value=True)
    redis.delete = AsyncMock()
    return redis


@pytest.fixture()
def service(mock_redis):
    """Create IdempotencyService with mocked Redis."""
    return IdempotencyService(mock_redis)


# ======================================================================
# IdempotencyService
# ======================================================================


class TestIdempotencyServiceKeyFormat:
    """Tests for key generation."""

    def test_make_key(self, service):
        key = service._make_key("transaction", "TXN-001")
        assert key == "idempotency:transaction:TXN-001"

    def test_make_key_different_types(self, service):
        key1 = service._make_key("transaction", "ID-1")
        key2 = service._make_key("notification", "ID-1")
        assert key1 != key2


class TestIsProcessed:
    """Tests for checking if a message was already processed."""

    async def test_returns_false_when_not_processed(self, service, mock_redis):
        mock_redis.exists.return_value = 0

        result = await service.is_processed("transaction", "TXN-NEW")

        mock_redis.exists.assert_called_once_with("idempotency:transaction:TXN-NEW")
        assert result is False

    async def test_returns_true_when_already_processed(self, service, mock_redis):
        mock_redis.exists.return_value = 1

        result = await service.is_processed("transaction", "TXN-OLD")

        assert result is True


class TestMarkProcessed:
    """Tests for marking messages as processed."""

    async def test_stores_result_as_json(self, service, mock_redis):
        result_data = {"transaction_id": "tx-1", "risk_score": 42}

        await service.mark_processed("transaction", "TXN-001", result=result_data)

        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args
        assert call_args[0][0] == "idempotency:transaction:TXN-001"
        assert call_args[0][1] == service.ttl
        assert json.loads(call_args[0][2]) == result_data

    async def test_stores_1_when_no_result(self, service, mock_redis):
        await service.mark_processed("transaction", "TXN-002")

        call_args = mock_redis.setex.call_args
        assert call_args[0][2] == "1"

    async def test_uses_custom_ttl(self, mock_redis):
        custom_ttl = timedelta(hours=2)
        svc = IdempotencyService(mock_redis, ttl=custom_ttl)

        await svc.mark_processed("transaction", "TXN-003")

        call_args = mock_redis.setex.call_args
        assert call_args[0][1] == custom_ttl


class TestGetResult:
    """Tests for retrieving stored results."""

    async def test_returns_stored_result(self, service, mock_redis):
        stored = {"transaction_id": "tx-1", "risk_score": 55}
        mock_redis.get.return_value = json.dumps(stored)

        result = await service.get_result("transaction", "TXN-001")

        assert result == stored

    async def test_returns_none_when_not_found(self, service, mock_redis):
        mock_redis.get.return_value = None

        result = await service.get_result("transaction", "TXN-MISSING")

        assert result is None

    async def test_returns_none_when_value_is_just_1(self, service, mock_redis):
        mock_redis.get.return_value = "1"

        result = await service.get_result("transaction", "TXN-NO-RESULT")

        assert result is None

    async def test_returns_none_on_invalid_json(self, service, mock_redis):
        mock_redis.get.return_value = "not-json{{"

        result = await service.get_result("transaction", "TXN-BAD")

        assert result is None


class TestAcquireLock:
    """Tests for distributed lock acquisition."""

    async def test_acquires_lock_successfully(self, service, mock_redis):
        mock_redis.set.return_value = True

        acquired = await service.acquire_lock("transaction", "TXN-001")

        assert acquired is True
        mock_redis.set.assert_called_once()
        call_kwargs = mock_redis.set.call_args
        assert call_kwargs[1]["nx"] is True

    async def test_fails_to_acquire_when_locked(self, service, mock_redis):
        mock_redis.set.return_value = False

        acquired = await service.acquire_lock("transaction", "TXN-001")

        assert acquired is False

    async def test_custom_lock_ttl(self, service, mock_redis):
        mock_redis.set.return_value = True
        custom_ttl = timedelta(minutes=10)

        await service.acquire_lock("transaction", "TXN-001", lock_ttl=custom_ttl)

        call_kwargs = mock_redis.set.call_args
        assert call_kwargs[1]["ex"] == 600  # 10 minutes in seconds


class TestReleaseLock:
    """Tests for lock release."""

    async def test_releases_lock(self, service, mock_redis):
        await service.release_lock("transaction", "TXN-001")

        mock_redis.delete.assert_called_once_with("lock:idempotency:transaction:TXN-001")


# ======================================================================
# IdempotentProcessor (context manager)
# ======================================================================


class TestIdempotentProcessor:
    """Tests for the IdempotentProcessor context manager."""

    async def test_should_process_new_message(self, service, mock_redis):
        mock_redis.exists.return_value = 0
        mock_redis.set.return_value = True

        async with IdempotentProcessor(service, "transaction", "TXN-NEW") as proc:
            assert proc.should_process is True
            proc.set_result({"risk_score": 10})

        # Should mark as processed after successful exit
        mock_redis.setex.assert_called_once()

    async def test_skips_already_processed_message(self, service, mock_redis):
        mock_redis.exists.return_value = 1
        mock_redis.get.return_value = json.dumps({"risk_score": 50})

        async with IdempotentProcessor(service, "transaction", "TXN-OLD") as proc:
            assert proc.should_process is False
            assert proc.cached_result == {"risk_score": 50}

        # Should NOT mark processed again
        mock_redis.setex.assert_not_called()

    async def test_skips_when_lock_not_acquired(self, service, mock_redis):
        mock_redis.exists.return_value = 0
        mock_redis.set.return_value = False  # Lock not acquired

        async with IdempotentProcessor(service, "transaction", "TXN-LOCKED") as proc:
            assert proc.should_process is False

    async def test_releases_lock_on_exit(self, service, mock_redis):
        mock_redis.exists.return_value = 0
        mock_redis.set.return_value = True

        async with IdempotentProcessor(service, "transaction", "TXN-LOCK") as proc:
            proc.set_result({"done": True})

        mock_redis.delete.assert_called_once_with("lock:idempotency:transaction:TXN-LOCK")

    async def test_does_not_mark_processed_on_exception(self, service, mock_redis):
        mock_redis.exists.return_value = 0
        mock_redis.set.return_value = True

        with pytest.raises(RuntimeError):
            async with IdempotentProcessor(service, "transaction", "TXN-ERR") as proc:
                proc.set_result({"risk_score": 0})
                raise RuntimeError("Processing failed")

        # Should NOT mark as processed on exception
        mock_redis.setex.assert_not_called()
        # But should still release lock
        mock_redis.delete.assert_called_once()

    async def test_does_not_mark_processed_without_result(self, service, mock_redis):
        mock_redis.exists.return_value = 0
        mock_redis.set.return_value = True

        async with IdempotentProcessor(service, "transaction", "TXN-NO-RES") as proc:
            assert proc.should_process is True
            # Intentionally not calling proc.set_result()

        mock_redis.setex.assert_not_called()
