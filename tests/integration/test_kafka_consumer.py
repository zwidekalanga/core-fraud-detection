"""Integration tests for fraud.inbound.kafka — the TransactionConsumer.

These tests verify the consumer's message processing pipeline (parse →
idempotency → store → evaluate → alert) with Kafka, Postgres, and Redis
mocked so that all tests run in isolation without devstack.
"""

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import get_settings
from app.consumers.transaction_consumer import TransactionConsumer
from app.models.alert import AlertStatus, Decision
from tests.conftest import make_high_risk_transaction, make_transaction

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(UTC)


def _kafka_message(data: dict | bytes, topic: str = "transactions.raw") -> SimpleNamespace:
    """Build a fake Kafka message that looks like an aiokafka ConsumerRecord."""
    if isinstance(data, bytes):
        value = data.decode("utf-8", errors="replace")
    else:
        value = json.dumps(data)
    return SimpleNamespace(
        topic=topic,
        partition=0,
        offset=1,
        timestamp=int(_NOW.timestamp() * 1000),
        value=value,
    )


def _make_transaction_model(external_id: str, **overrides):
    """Minimal transaction ORM-like object returned by TransactionRepository.upsert."""
    data = {
        "id": str(uuid.uuid4()),
        "external_id": external_id,
        "customer_id": str(uuid.uuid4()),
        "amount": Decimal("150.00"),
        "currency": "ZAR",
        "transaction_type": "purchase",
        "channel": "online",
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _make_alert_model(transaction_id: str, risk_score: int, decision: str, **overrides):
    """Minimal alert ORM-like object returned by AlertRepository.create."""
    data = {
        "id": str(uuid.uuid4()),
        "transaction_id": transaction_id,
        "risk_score": risk_score,
        "decision": decision,
        "status": AlertStatus.PENDING,
        "triggered_rules": [],
        "created_at": _NOW,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _make_assessment(score: int = 10, decision_value: str = "APPROVE", triggered_rules=None):
    """Minimal AssessmentResult-like object from pylitmus."""
    if triggered_rules is None:
        triggered_rules = []
    return SimpleNamespace(
        total_score=score,
        decision=SimpleNamespace(value=decision_value),
        triggered_rules=triggered_rules,
        processing_time_ms=5.2,
    )


def _make_triggered_rule(
    code="AMT_001", name="High Amount", category="amount", severity_value="high", score=85
):
    return SimpleNamespace(
        rule_code=code,
        rule_name=name,
        category=category,
        severity=SimpleNamespace(value=severity_value),
        score=score,
        explanation=f"{name} triggered",
    )


# ---------------------------------------------------------------------------
# Fixture: a consumer with mocked dependencies
# ---------------------------------------------------------------------------


@pytest.fixture()
def consumer():
    """Build a TransactionConsumer with mocked session_factory and redis."""
    settings = get_settings()
    mock_session = AsyncMock()
    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock(return_value=True)
    mock_redis.exists = AsyncMock(return_value=0)
    mock_redis.delete = AsyncMock(return_value=1)

    c = TransactionConsumer(
        settings=settings,
        session_factory=mock_session_factory,
        redis=mock_redis,
    )
    # Pre-initialise the fraud detector so we can mock it
    c._fraud_detector = MagicMock()
    c._rules_count = 5

    return c, mock_session, mock_redis


# ---------------------------------------------------------------------------
# Tests — Message Processing
# ---------------------------------------------------------------------------


class TestKafkaConsumerProcessing:
    """Tests that TransactionConsumer.process() handles messages correctly."""

    async def test_consumer_processes_transaction(self, consumer):
        """A valid transaction message is stored and evaluated."""
        c, mock_session, _ = consumer
        txn_data = make_transaction()
        msg = _kafka_message(txn_data)

        stored_txn = _make_transaction_model(txn_data["external_id"])
        assessment = _make_assessment(score=10, decision_value="APPROVE")

        with (
            patch("app.core.evaluation_service.TransactionRepository") as MockTxnRepo,
            patch("app.core.evaluation_service.AlertRepository"),
            patch("app.consumers.transaction_consumer.IdempotentProcessor") as MockIdempotent,
        ):
            # Idempotency says "process this"
            processor_mock = AsyncMock()
            processor_mock.should_process = True
            processor_mock.set_result = MagicMock()
            MockIdempotent.return_value.__aenter__ = AsyncMock(return_value=processor_mock)
            MockIdempotent.return_value.__aexit__ = AsyncMock(return_value=False)

            MockTxnRepo.return_value.upsert = AsyncMock(return_value=stored_txn)
            c._fraud_detector.evaluate.return_value = assessment
            c._fraud_detector.get_decision.return_value = Decision.APPROVE

            await c.process(msg)

            MockTxnRepo.return_value.upsert.assert_called_once()
            c._fraud_detector.evaluate.assert_called_once()
            processor_mock.set_result.assert_called_once()

    async def test_consumer_creates_alert_for_high_risk(self, consumer):
        """High-risk transaction triggers alert creation."""
        c, mock_session, _ = consumer
        txn_data = make_high_risk_transaction()
        msg = _kafka_message(txn_data)

        stored_txn = _make_transaction_model(txn_data["external_id"])
        triggered = [_make_triggered_rule()]
        assessment = _make_assessment(score=85, decision_value="FLAG", triggered_rules=triggered)
        alert = _make_alert_model(stored_txn.id, 85, "flag")

        with (
            patch("app.core.evaluation_service.TransactionRepository") as MockTxnRepo,
            patch("app.core.evaluation_service.AlertRepository") as MockAlertRepo,
            patch("app.core.evaluation_service.ConfigRepository") as MockConfigRepo,
            patch("app.consumers.transaction_consumer.IdempotentProcessor") as MockIdempotent,
        ):
            processor_mock = AsyncMock()
            processor_mock.should_process = True
            processor_mock.set_result = MagicMock()
            MockIdempotent.return_value.__aenter__ = AsyncMock(return_value=processor_mock)
            MockIdempotent.return_value.__aexit__ = AsyncMock(return_value=False)

            MockTxnRepo.return_value.upsert = AsyncMock(return_value=stored_txn)
            MockAlertRepo.return_value.create = AsyncMock(return_value=alert)
            MockAlertRepo.return_value.review = AsyncMock(return_value=alert)
            MockConfigRepo.return_value.get_int = AsyncMock(return_value=90)
            c._fraud_detector.evaluate.return_value = assessment
            c._fraud_detector.get_decision.return_value = Decision.FLAG

            await c.process(msg)

            MockAlertRepo.return_value.create.assert_called_once()
            call_kwargs = MockAlertRepo.return_value.create.call_args
            assert call_kwargs.kwargs["risk_score"] == 85
            assert call_kwargs.kwargs["decision"] == Decision.FLAG

    async def test_consumer_sets_idempotency_key(self, consumer):
        """After processing, the idempotency processor set_result is called."""
        c, _, mock_redis = consumer
        txn_data = make_transaction()
        msg = _kafka_message(txn_data)

        stored_txn = _make_transaction_model(txn_data["external_id"])
        assessment = _make_assessment(score=5, decision_value="APPROVE")

        with (
            patch("app.core.evaluation_service.TransactionRepository") as MockTxnRepo,
            patch("app.core.evaluation_service.AlertRepository"),
            patch("app.consumers.transaction_consumer.IdempotentProcessor") as MockIdempotent,
        ):
            processor_mock = AsyncMock()
            processor_mock.should_process = True
            processor_mock.set_result = MagicMock()
            MockIdempotent.return_value.__aenter__ = AsyncMock(return_value=processor_mock)
            MockIdempotent.return_value.__aexit__ = AsyncMock(return_value=False)

            MockTxnRepo.return_value.upsert = AsyncMock(return_value=stored_txn)
            c._fraud_detector.evaluate.return_value = assessment
            c._fraud_detector.get_decision.return_value = Decision.APPROVE

            await c.process(msg)

            # The processor marks the result so IdempotencyService persists it
            processor_mock.set_result.assert_called_once()
            result = processor_mock.set_result.call_args[0][0]
            assert result["external_id"] == txn_data["external_id"]


class TestKafkaConsumerIdempotency:
    """Tests for duplicate message handling."""

    async def test_duplicate_message_not_reprocessed(self, consumer):
        """When idempotency says already processed, transaction is skipped."""
        c, _, _ = consumer
        txn_data = make_transaction()
        msg = _kafka_message(txn_data)

        with patch("app.consumers.transaction_consumer.IdempotentProcessor") as MockIdempotent:
            processor_mock = AsyncMock()
            processor_mock.should_process = False  # Already processed
            MockIdempotent.return_value.__aenter__ = AsyncMock(return_value=processor_mock)
            MockIdempotent.return_value.__aexit__ = AsyncMock(return_value=False)

            await c.process(msg)

            # Fraud detector should NOT have been called
            c._fraud_detector.evaluate.assert_not_called()


class TestKafkaConsumerValidation:
    """Tests for invalid message handling."""

    async def test_invalid_json_raises(self, consumer):
        """Invalid JSON raises json.JSONDecodeError so BaseConsumer sends to DLQ."""
        c, _, _ = consumer
        msg = _kafka_message(b"not-valid-json{{{")

        with pytest.raises(json.JSONDecodeError):
            await c.process(msg)

    async def test_missing_external_id_skipped(self, consumer):
        """A message without external_id is skipped (no error raised)."""
        c, _, _ = consumer
        bad_msg = {
            "customer_id": str(uuid.uuid4()),
            "amount": "100.00",
            "currency": "ZAR",
            "transaction_type": "purchase",
            "channel": "online",
        }
        msg = _kafka_message(bad_msg)

        # Should return without raising — consumer stays alive
        await c.process(msg)

        # Fraud detector should NOT have been called
        c._fraud_detector.evaluate.assert_not_called()


class TestKafkaConsumerMetrics:
    """Tests that the consumer produces expected side effects."""

    async def test_alert_has_triggered_rules(self, consumer):
        """Alerts contain details of which rules triggered."""
        c, mock_session, _ = consumer
        txn_data = make_high_risk_transaction()
        msg = _kafka_message(txn_data)

        stored_txn = _make_transaction_model(txn_data["external_id"])
        triggered = [
            _make_triggered_rule("AMT_001", "High Amount", "amount", "high", 85),
            _make_triggered_rule("GEO_002", "Risky Country", "geographic", "critical", 90),
        ]
        assessment = _make_assessment(score=92, decision_value="FLAG", triggered_rules=triggered)
        alert = _make_alert_model(stored_txn.id, 92, "flag")

        with (
            patch("app.core.evaluation_service.TransactionRepository") as MockTxnRepo,
            patch("app.core.evaluation_service.AlertRepository") as MockAlertRepo,
            patch("app.core.evaluation_service.ConfigRepository") as MockConfigRepo,
            patch("app.consumers.transaction_consumer.IdempotentProcessor") as MockIdempotent,
        ):
            processor_mock = AsyncMock()
            processor_mock.should_process = True
            processor_mock.set_result = MagicMock()
            MockIdempotent.return_value.__aenter__ = AsyncMock(return_value=processor_mock)
            MockIdempotent.return_value.__aexit__ = AsyncMock(return_value=False)

            MockTxnRepo.return_value.upsert = AsyncMock(return_value=stored_txn)
            MockAlertRepo.return_value.create = AsyncMock(return_value=alert)
            MockAlertRepo.return_value.review = AsyncMock(return_value=alert)
            MockConfigRepo.return_value.get_int = AsyncMock(return_value=90)
            c._fraud_detector.evaluate.return_value = assessment
            c._fraud_detector.get_decision.return_value = Decision.FLAG

            await c.process(msg)

            call_kwargs = MockAlertRepo.return_value.create.call_args.kwargs
            rules = call_kwargs["triggered_rules"]
            assert isinstance(rules, list)
            assert len(rules) == 2
            for rule in rules:
                assert "code" in rule
                assert "score" in rule

    async def test_multiple_transactions_processed(self, consumer):
        """Multiple messages can be processed sequentially."""
        c, _, _ = consumer
        txns = [make_transaction() for _ in range(5)]

        for txn_data in txns:
            msg = _kafka_message(txn_data)
            stored_txn = _make_transaction_model(txn_data["external_id"])
            assessment = _make_assessment(score=10, decision_value="APPROVE")

            with (
                patch("app.core.evaluation_service.TransactionRepository") as MockTxnRepo,
                patch("app.core.evaluation_service.AlertRepository"),
                patch("app.consumers.transaction_consumer.IdempotentProcessor") as MockIdempotent,
            ):
                processor_mock = AsyncMock()
                processor_mock.should_process = True
                processor_mock.set_result = MagicMock()
                MockIdempotent.return_value.__aenter__ = AsyncMock(return_value=processor_mock)
                MockIdempotent.return_value.__aexit__ = AsyncMock(return_value=False)

                MockTxnRepo.return_value.upsert = AsyncMock(return_value=stored_txn)
                c._fraud_detector.evaluate.return_value = assessment
                c._fraud_detector.get_decision.return_value = Decision.APPROVE

                await c.process(msg)

        # All 5 should have been evaluated
        assert c._fraud_detector.evaluate.call_count == 5
