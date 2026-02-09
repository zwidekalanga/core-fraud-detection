"""Unit tests for TransactionConsumer message processing logic."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.consumers.transaction_consumer import TransactionConsumer


def _make_mock_settings():
    """Create a mock Settings object."""
    settings = MagicMock()
    settings.kafka_bootstrap_servers = "localhost:9092"
    settings.kafka_consumer_group = "test-group"
    settings.kafka_auto_offset_reset = "earliest"
    settings.threshold_approve = 30
    settings.threshold_review = 70
    settings.scoring_strategy = "weighted"
    settings.jwt_secret_key = "test-secret"
    settings.jwt_algorithm = "HS256"
    settings.jwt_access_token_expire_minutes = 30
    settings.jwt_refresh_token_expire_minutes = 1440
    return settings


def _make_kafka_message(data, topic="transactions.raw"):
    """Create a mock Kafka message."""
    msg = MagicMock()
    msg.value = json.dumps(data) if isinstance(data, dict) else data
    msg.topic = topic
    msg.partition = 0
    msg.offset = 1
    msg.timestamp = 1700000000000
    return msg


class TestTransactionConsumerProcess:
    """Tests for TransactionConsumer.process() method."""

    @pytest.fixture()
    def consumer(self):
        """Create a TransactionConsumer with mocked dependencies."""
        settings = _make_mock_settings()
        session_factory = AsyncMock()
        redis = AsyncMock()
        redis.exists = AsyncMock(return_value=0)
        redis.set = AsyncMock(return_value=True)
        redis.get = AsyncMock(return_value=None)
        redis.setex = AsyncMock()
        redis.delete = AsyncMock()

        consumer = TransactionConsumer(
            settings=settings,
            session_factory=session_factory,
            redis=redis,
        )
        return consumer

    async def test_skips_message_without_external_id(self, consumer):
        """Messages missing external_id should be skipped without error."""
        msg = _make_kafka_message({"customer_id": "cust-1", "amount": "100"})

        # Should not raise
        await consumer.process(msg)

    async def test_rejects_invalid_json(self, consumer):
        """Invalid JSON should raise and be caught by the caller."""
        msg = MagicMock()
        msg.value = "not-valid-json{{{"
        msg.topic = "transactions.raw"
        msg.partition = 0
        msg.offset = 1
        msg.timestamp = 0

        with pytest.raises(json.JSONDecodeError):
            await consumer.process(msg)


class TestTransactionConsumerInit:
    """Tests for TransactionConsumer initialization."""

    def test_consumer_attributes(self):
        settings = _make_mock_settings()
        session_factory = AsyncMock()
        redis = AsyncMock()

        consumer = TransactionConsumer(
            settings=settings,
            session_factory=session_factory,
            redis=redis,
        )

        assert consumer.topic == "transactions.raw"
        assert consumer.dlq_topic == "transactions.dlq"
        assert consumer._fraud_detector is None
        assert consumer._rules_count == 0

    def test_consumer_has_idempotency_service(self):
        settings = _make_mock_settings()
        consumer = TransactionConsumer(
            settings=settings,
            session_factory=AsyncMock(),
            redis=AsyncMock(),
        )
        assert consumer.idempotency is not None
