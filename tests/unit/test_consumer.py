"""Unit tests for TransactionConsumer message processing logic (M47)."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.consumers.transaction_consumer import TransactionConsumer
from app.core.evaluation_service import EvaluationResult


def _make_mock_settings():
    """Create a mock Settings object."""
    settings = MagicMock()
    settings.kafka_bootstrap_servers = "localhost:9092"
    settings.kafka_consumer_group = "test-group"
    settings.kafka_auto_offset_reset = "earliest"
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


def _make_valid_transaction_data(**overrides):
    """Build a valid transaction payload dict."""
    data = {
        "external_id": "TXN-test-001",
        "customer_id": "CUST-001",
        "amount": "150.00",
        "currency": "ZAR",
        "transaction_type": "purchase",
        "channel": "online",
        "merchant_id": "MERCH-001",
        "merchant_name": "Test Store",
        "merchant_category": "retail",
        "location_country": "ZA",
        "location_city": "Cape Town",
    }
    data.update(overrides)
    return data


def _make_evaluation_result(**overrides):
    """Create a mock EvaluationResult."""
    defaults = {
        "transaction_id": "txn-uuid-1",
        "external_id": "TXN-test-001",
        "risk_score": 25,
        "decision": "approve",
        "decision_tier": "APPROVE",
        "decision_tier_description": "Low risk - approve transaction",
        "triggered_rules": [],
        "processing_time_ms": 5.0,
        "alert_created": False,
        "alert_id": "",
    }
    defaults.update(overrides)
    return EvaluationResult(**defaults)


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

    async def test_skips_duplicate_message(self, consumer):
        """Already-processed messages should be detected by idempotency check."""
        consumer.redis.exists = AsyncMock(return_value=1)
        consumer.redis.get = AsyncMock(return_value=json.dumps({"risk_score": 50}))

        data = _make_valid_transaction_data()
        msg = _make_kafka_message(data)

        # Should not raise — message is silently skipped
        await consumer.process(msg)

    async def test_skips_when_lock_not_acquired(self, consumer):
        """When lock is held by another consumer, message should be skipped."""
        consumer.redis.exists = AsyncMock(return_value=0)
        consumer.redis.set = AsyncMock(return_value=False)  # Lock not acquired

        data = _make_valid_transaction_data()
        msg = _make_kafka_message(data)

        await consumer.process(msg)

    async def test_processes_valid_transaction(self, consumer):
        """A valid transaction should be processed via _process_transaction."""
        data = _make_valid_transaction_data()
        msg = _make_kafka_message(data)

        mock_result = {
            "transaction_id": "txn-1",
            "external_id": "TXN-test-001",
            "risk_score": 25,
            "decision": "approve",
            "triggered_rules": [],
            "processing_time_ms": 5.0,
        }

        with patch.object(consumer, "_process_transaction", new_callable=AsyncMock) as mock_proc:
            mock_proc.return_value = mock_result
            await consumer.process(msg)
            mock_proc.assert_called_once_with(data)

    async def test_empty_message_body(self, consumer):
        """Empty JSON object should be skipped (no external_id)."""
        msg = _make_kafka_message({})
        await consumer.process(msg)

    async def test_message_with_extra_fields(self, consumer):
        """Extra fields in message should not cause failures."""
        data = _make_valid_transaction_data(unknown_field="should-be-ignored")
        msg = _make_kafka_message(data)

        # Should not raise — extra fields are ignored by Pydantic
        with patch.object(consumer, "_process_transaction", new_callable=AsyncMock) as mock_proc:
            mock_proc.return_value = {"risk_score": 0, "decision": "approve"}
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

    def test_consumer_has_evaluation_service(self):
        settings = _make_mock_settings()
        consumer = TransactionConsumer(
            settings=settings,
            session_factory=AsyncMock(),
            redis=AsyncMock(),
        )
        assert consumer._evaluation_service is not None


class TestProcessTransaction:
    """Tests for the inner _process_transaction method."""

    @pytest.fixture()
    def consumer(self):
        settings = _make_mock_settings()
        session_factory = AsyncMock()
        redis = AsyncMock()
        consumer = TransactionConsumer(
            settings=settings,
            session_factory=session_factory,
            redis=redis,
        )
        return consumer

    async def test_raises_without_fraud_detector(self, consumer):
        """_process_transaction should raise RuntimeError if detector is None."""
        data = _make_valid_transaction_data()
        assert consumer._fraud_detector is None

        with pytest.raises(RuntimeError, match="FraudDetector not initialised"):
            await consumer._process_transaction(data)

    async def test_raises_on_invalid_data(self, consumer):
        """Invalid transaction data should raise ValueError."""
        consumer._fraud_detector = MagicMock()

        with pytest.raises(ValueError, match="Invalid transaction data"):
            await consumer._process_transaction({"bad_field": "no_amount"})

    async def test_returns_result_dict(self, consumer):
        """Successful processing returns a dict with expected keys."""
        consumer._fraud_detector = MagicMock()

        mock_result = _make_evaluation_result()
        consumer._evaluation_service.evaluate = AsyncMock(return_value=mock_result)

        # Mock session_factory context
        mock_session = AsyncMock()
        consumer.session_factory = MagicMock()
        consumer.session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        consumer.session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        data = _make_valid_transaction_data()
        result = await consumer._process_transaction(data)

        assert result["transaction_id"] == "txn-uuid-1"
        assert result["external_id"] == "TXN-test-001"
        assert result["risk_score"] == 25
        assert result["decision"] == "approve"
        assert "alert_id" not in result  # No alert for approve

    async def test_returns_alert_id_when_created(self, consumer):
        """Result should include alert_id when alert was created."""
        consumer._fraud_detector = MagicMock()

        mock_result = _make_evaluation_result(
            risk_score=85,
            decision="flag",
            alert_created=True,
            alert_id="alert-uuid-1",
        )
        consumer._evaluation_service.evaluate = AsyncMock(return_value=mock_result)

        mock_session = AsyncMock()
        consumer.session_factory = MagicMock()
        consumer.session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        consumer.session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        data = _make_valid_transaction_data()
        result = await consumer._process_transaction(data)

        assert result["alert_id"] == "alert-uuid-1"
        assert result["risk_score"] == 85
        assert result["decision"] == "flag"


class TestConsumerStart:
    """Tests for consumer start/stop lifecycle."""

    async def test_start_loads_rules(self):
        """start() should load rules into the fraud detector."""
        settings = _make_mock_settings()

        mock_rules = [
            SimpleNamespace(
                code="AMT_001",
                name="High Amount",
                description="High",
                category="amount",
                severity="high",
                score=50,
                enabled=True,
                conditions={"field": "amount", "operator": "greater_than", "value": 10000},
                effective_from=None,
                effective_to=None,
            )
        ]

        mock_session = AsyncMock()
        session_factory = MagicMock()
        session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        redis = AsyncMock()
        consumer = TransactionConsumer(
            settings=settings,
            session_factory=session_factory,
            redis=redis,
        )

        with (
            patch.object(consumer, "_consumer", create=True),
            patch("app.consumers.base.AIOKafkaConsumer", new_callable=MagicMock),
            patch("app.consumers.base.AIOKafkaProducer", new_callable=MagicMock),
            patch("app.consumers.transaction_consumer.RuleRepository") as MockRuleRepo,
        ):
            mock_repo_instance = AsyncMock()
            mock_repo_instance.get_enabled_rules = AsyncMock(return_value=mock_rules)
            MockRuleRepo.return_value = mock_repo_instance

            # Patch super().start() to avoid actual Kafka connection
            with patch("app.consumers.base.BaseConsumer.start", new_callable=AsyncMock):
                await consumer.start()

            assert consumer._fraud_detector is not None
            assert consumer._rules_count == 1
