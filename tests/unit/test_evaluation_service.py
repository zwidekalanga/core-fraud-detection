"""Unit tests for FraudEvaluationService facade (T-01).

Verifies the full evaluation pipeline: upsert → evaluate → alert → auto-escalate.
"""

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.core.evaluation_service import EvaluationResult, FraudEvaluationService, _auto_escalate
from app.models.alert import AlertStatus, Decision
from app.schemas.alert import SYSTEM_REVIEWER

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings():
    settings = MagicMock()
    settings.rule_cache_ttl = 300
    return settings


def _make_eval_request(**overrides):
    data = {
        "external_id": f"TXN-{uuid.uuid4().hex[:12]}",
        "customer_id": str(uuid.uuid4()),
        "amount": Decimal("500.00"),
        "currency": "ZAR",
        "transaction_type": "purchase",
        "channel": "online",
        "merchant_id": "MERCH-001",
        "merchant_name": "Test Store",
        "merchant_category": "retail",
        "location_country": "ZA",
        "location_city": "Cape Town",
        "device_fingerprint": "fp-abc",
        "ip_address": "41.0.0.1",
        "transaction_time": None,
        "extra_data": {},
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _make_transaction(external_id, **overrides):
    data = {
        "id": uuid.uuid4(),
        "external_id": external_id,
        "customer_id": str(uuid.uuid4()),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _make_assessment(score=10, triggered_rules=None):
    if triggered_rules is None:
        triggered_rules = []
    return SimpleNamespace(
        total_score=score,
        triggered_rules=triggered_rules,
        processing_time_ms=3.5,
    )


def _make_triggered_rule(code="AMT_001", name="High Amount", score=85):
    return SimpleNamespace(
        rule_code=code,
        rule_name=name,
        category="amount",
        severity=SimpleNamespace(value="high"),
        score=score,
        explanation=f"{name} triggered",
    )


def _make_alert(alert_id=None, **overrides):
    data = {
        "id": alert_id or uuid.uuid4(),
        "status": AlertStatus.PENDING,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def service():
    settings = _make_settings()
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    return FraudEvaluationService(settings, redis)


@pytest.fixture()
def mock_session():
    session = AsyncMock()
    session.commit = AsyncMock()
    session.flush = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# Tests — EvaluationResult dataclass
# ---------------------------------------------------------------------------


class TestEvaluationResult:
    def test_defaults(self):
        r = EvaluationResult(
            transaction_id="txn-1",
            external_id="ext-1",
            risk_score=0,
            decision="approve",
            decision_tier="low",
            decision_tier_description="Low risk",
        )
        assert r.triggered_rules == []
        assert r.processing_time_ms == 0.0
        assert r.alert_created is False
        assert r.alert_id == ""

    def test_custom_values(self):
        r = EvaluationResult(
            transaction_id="txn-2",
            external_id="ext-2",
            risk_score=85,
            decision="flag",
            decision_tier="high",
            decision_tier_description="High risk",
            triggered_rules=[{"code": "AMT_001"}],
            processing_time_ms=12.5,
            alert_created=True,
            alert_id="alert-123",
        )
        assert r.risk_score == 85
        assert r.alert_created is True
        assert len(r.triggered_rules) == 1


# ---------------------------------------------------------------------------
# Tests — evaluate()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestEvaluateLowRisk:
    """Low-risk transactions should NOT create an alert."""

    async def test_low_risk_no_alert(self, service, mock_session):
        request = _make_eval_request()
        txn = _make_transaction(request.external_id)
        assessment = _make_assessment(score=10)

        with (
            patch("app.core.evaluation_service.TransactionRepository") as MockTxnRepo,
            patch("app.core.evaluation_service.RuleRepository") as MockRuleRepo,
            patch("app.core.evaluation_service.AlertRepository") as MockAlertRepo,
        ):
            MockTxnRepo.return_value.upsert = AsyncMock(return_value=txn)
            MockRuleRepo.return_value.get_enabled_rules = AsyncMock(return_value=[])

            detector = MagicMock()
            detector.evaluate.return_value = assessment
            detector.get_decision.return_value = Decision.APPROVE
            detector.get_decision_tier.return_value = "low"
            detector.get_decision_tier_description.return_value = "Low risk"

            result = await service.evaluate(request, mock_session, fraud_detector=detector)

            assert result.risk_score == 10
            assert result.decision == "approve"
            assert result.alert_created is False
            assert result.alert_id == ""
            MockAlertRepo.return_value.create.assert_not_called()


@pytest.mark.asyncio
class TestEvaluateHighRisk:
    """High-risk transactions should create an alert."""

    async def test_high_risk_creates_alert(self, service, mock_session):
        request = _make_eval_request()
        txn = _make_transaction(request.external_id)
        triggered = [_make_triggered_rule()]
        assessment = _make_assessment(score=85, triggered_rules=triggered)
        alert = _make_alert()

        with (
            patch("app.core.evaluation_service.TransactionRepository") as MockTxnRepo,
            patch("app.core.evaluation_service.RuleRepository"),
            patch("app.core.evaluation_service.AlertRepository") as MockAlertRepo,
            patch("app.core.evaluation_service.ConfigRepository") as MockConfigRepo,
        ):
            MockTxnRepo.return_value.upsert = AsyncMock(return_value=txn)
            MockAlertRepo.return_value.create = AsyncMock(return_value=alert)
            MockAlertRepo.return_value.review = AsyncMock(return_value=alert)
            MockConfigRepo.return_value.get_int = AsyncMock(return_value=90)

            detector = MagicMock()
            detector.evaluate.return_value = assessment
            detector.get_decision.return_value = Decision.FLAG
            detector.get_decision_tier.return_value = "high"
            detector.get_decision_tier_description.return_value = "High risk"

            result = await service.evaluate(request, mock_session, fraud_detector=detector)

            assert result.risk_score == 85
            assert result.decision == "flag"
            assert result.alert_created is True
            assert result.alert_id == str(alert.id)
            MockAlertRepo.return_value.create.assert_called_once()

    async def test_review_decision_creates_alert(self, service, mock_session):
        request = _make_eval_request()
        txn = _make_transaction(request.external_id)
        assessment = _make_assessment(score=55)
        alert = _make_alert()

        with (
            patch("app.core.evaluation_service.TransactionRepository") as MockTxnRepo,
            patch("app.core.evaluation_service.RuleRepository"),
            patch("app.core.evaluation_service.AlertRepository") as MockAlertRepo,
            patch("app.core.evaluation_service.ConfigRepository") as MockConfigRepo,
        ):
            MockTxnRepo.return_value.upsert = AsyncMock(return_value=txn)
            MockAlertRepo.return_value.create = AsyncMock(return_value=alert)
            MockConfigRepo.return_value.get_int = AsyncMock(return_value=90)

            detector = MagicMock()
            detector.evaluate.return_value = assessment
            detector.get_decision.return_value = Decision.REVIEW
            detector.get_decision_tier.return_value = "medium"
            detector.get_decision_tier_description.return_value = "Medium risk"

            result = await service.evaluate(request, mock_session, fraud_detector=detector)

            assert result.alert_created is True
            MockAlertRepo.return_value.create.assert_called_once()


@pytest.mark.asyncio
class TestAutoEscalation:
    """Score >= 100 auto-confirms; score >= threshold auto-escalates."""

    async def test_score_100_auto_confirms(self, service, mock_session):
        request = _make_eval_request()
        txn = _make_transaction(request.external_id)
        assessment = _make_assessment(score=100)
        alert = _make_alert()

        with (
            patch("app.core.evaluation_service.TransactionRepository") as MockTxnRepo,
            patch("app.core.evaluation_service.RuleRepository"),
            patch("app.core.evaluation_service.AlertRepository") as MockAlertRepo,
        ):
            MockTxnRepo.return_value.upsert = AsyncMock(return_value=txn)
            MockAlertRepo.return_value.create = AsyncMock(return_value=alert)
            MockAlertRepo.return_value.review = AsyncMock(return_value=alert)

            detector = MagicMock()
            detector.evaluate.return_value = assessment
            detector.get_decision.return_value = Decision.FLAG
            detector.get_decision_tier.return_value = "high"
            detector.get_decision_tier_description.return_value = "High risk"

            await service.evaluate(request, mock_session, fraud_detector=detector)

            MockAlertRepo.return_value.review.assert_called_once_with(
                str(alert.id),
                status=AlertStatus.CONFIRMED,
                reviewer=SYSTEM_REVIEWER,
            )

    async def test_score_above_threshold_auto_escalates(self, service, mock_session):
        request = _make_eval_request()
        txn = _make_transaction(request.external_id)
        assessment = _make_assessment(score=92)
        alert = _make_alert()

        with (
            patch("app.core.evaluation_service.TransactionRepository") as MockTxnRepo,
            patch("app.core.evaluation_service.RuleRepository"),
            patch("app.core.evaluation_service.AlertRepository") as MockAlertRepo,
            patch("app.core.evaluation_service.ConfigRepository") as MockConfigRepo,
        ):
            MockTxnRepo.return_value.upsert = AsyncMock(return_value=txn)
            MockAlertRepo.return_value.create = AsyncMock(return_value=alert)
            MockAlertRepo.return_value.review = AsyncMock(return_value=alert)
            MockConfigRepo.return_value.get_int = AsyncMock(return_value=90)

            detector = MagicMock()
            detector.evaluate.return_value = assessment
            detector.get_decision.return_value = Decision.FLAG
            detector.get_decision_tier.return_value = "high"
            detector.get_decision_tier_description.return_value = "High risk"

            await service.evaluate(request, mock_session, fraud_detector=detector)

            MockAlertRepo.return_value.review.assert_called_once_with(
                str(alert.id),
                status=AlertStatus.ESCALATED,
                reviewer=SYSTEM_REVIEWER,
            )

    async def test_score_below_threshold_no_escalation(self, service, mock_session):
        request = _make_eval_request()
        txn = _make_transaction(request.external_id)
        assessment = _make_assessment(score=85)
        alert = _make_alert()

        with (
            patch("app.core.evaluation_service.TransactionRepository") as MockTxnRepo,
            patch("app.core.evaluation_service.RuleRepository"),
            patch("app.core.evaluation_service.AlertRepository") as MockAlertRepo,
            patch("app.core.evaluation_service.ConfigRepository") as MockConfigRepo,
        ):
            MockTxnRepo.return_value.upsert = AsyncMock(return_value=txn)
            MockAlertRepo.return_value.create = AsyncMock(return_value=alert)
            MockConfigRepo.return_value.get_int = AsyncMock(return_value=90)

            detector = MagicMock()
            detector.evaluate.return_value = assessment
            detector.get_decision.return_value = Decision.FLAG
            detector.get_decision_tier.return_value = "high"
            detector.get_decision_tier_description.return_value = "High risk"

            await service.evaluate(request, mock_session, fraud_detector=detector)

            # review should NOT be called — score 85 < threshold 90
            MockAlertRepo.return_value.review.assert_not_called()


@pytest.mark.asyncio
class TestEvaluateOptions:
    """Test optional parameters of evaluate()."""

    async def test_provided_detector_skips_rule_loading(self, service, mock_session):
        request = _make_eval_request()
        txn = _make_transaction(request.external_id)
        assessment = _make_assessment(score=5)

        with (
            patch("app.core.evaluation_service.TransactionRepository") as MockTxnRepo,
            patch("app.core.evaluation_service.RuleRepository") as MockRuleRepo,
            patch("app.core.evaluation_service.AlertRepository"),
        ):
            MockTxnRepo.return_value.upsert = AsyncMock(return_value=txn)

            detector = MagicMock()
            detector.evaluate.return_value = assessment
            detector.get_decision.return_value = Decision.APPROVE
            detector.get_decision_tier.return_value = "low"
            detector.get_decision_tier_description.return_value = "Low risk"

            await service.evaluate(request, mock_session, fraud_detector=detector)

            # When a detector is provided, rules should NOT be loaded from DB
            MockRuleRepo.return_value.get_enabled_rules.assert_not_called()

    async def test_no_detector_loads_rules_from_db(self, service, mock_session):
        request = _make_eval_request()
        txn = _make_transaction(request.external_id)
        assessment = _make_assessment(score=5)

        with (
            patch("app.core.evaluation_service.TransactionRepository") as MockTxnRepo,
            patch("app.core.evaluation_service.RuleRepository") as MockRuleRepo,
            patch("app.core.evaluation_service.AlertRepository"),
            patch("app.core.evaluation_service.FraudDetector") as MockDetectorClass,
        ):
            MockTxnRepo.return_value.upsert = AsyncMock(return_value=txn)
            MockRuleRepo.return_value.get_enabled_rules = AsyncMock(return_value=[])

            mock_detector = MagicMock()
            mock_detector.evaluate.return_value = assessment
            mock_detector.get_decision.return_value = Decision.APPROVE
            mock_detector.get_decision_tier.return_value = "low"
            mock_detector.get_decision_tier_description.return_value = "Low risk"
            MockDetectorClass.return_value = mock_detector

            await service.evaluate(request, mock_session)

            MockRuleRepo.return_value.get_enabled_rules.assert_called_once()
            mock_detector.load_rules.assert_called_once()

    async def test_enrich_false_skips_feature_service(self, service, mock_session):
        request = _make_eval_request()
        txn = _make_transaction(request.external_id)
        assessment = _make_assessment(score=5)

        with (
            patch("app.core.evaluation_service.TransactionRepository") as MockTxnRepo,
            patch("app.core.evaluation_service.RuleRepository"),
            patch("app.core.evaluation_service.AlertRepository"),
            patch.object(service._feature_service, "enrich_transaction") as mock_enrich,
        ):
            MockTxnRepo.return_value.upsert = AsyncMock(return_value=txn)

            detector = MagicMock()
            detector.evaluate.return_value = assessment
            detector.get_decision.return_value = Decision.APPROVE
            detector.get_decision_tier.return_value = "low"
            detector.get_decision_tier_description.return_value = "Low risk"

            await service.evaluate(
                request,
                mock_session,
                fraud_detector=detector,
                enrich=False,
            )

            mock_enrich.assert_not_called()

    async def test_enrich_true_calls_feature_service(self, service, mock_session):
        request = _make_eval_request()
        txn = _make_transaction(request.external_id)
        assessment = _make_assessment(score=5)

        with (
            patch("app.core.evaluation_service.TransactionRepository") as MockTxnRepo,
            patch("app.core.evaluation_service.RuleRepository"),
            patch("app.core.evaluation_service.AlertRepository"),
            patch.object(
                service._feature_service,
                "enrich_transaction",
                new_callable=AsyncMock,
                return_value={"amount": 500.0},
            ) as mock_enrich,
        ):
            MockTxnRepo.return_value.upsert = AsyncMock(return_value=txn)

            detector = MagicMock()
            detector.evaluate.return_value = assessment
            detector.get_decision.return_value = Decision.APPROVE
            detector.get_decision_tier.return_value = "low"
            detector.get_decision_tier_description.return_value = "Low risk"

            await service.evaluate(request, mock_session, fraud_detector=detector)

            mock_enrich.assert_called_once()


@pytest.mark.asyncio
class TestEvaluateErrorHandling:
    """Test error propagation in evaluate()."""

    async def test_upsert_sqlalchemy_error_propagates(self, service, mock_session):
        request = _make_eval_request()

        with patch("app.core.evaluation_service.TransactionRepository") as MockTxnRepo:
            MockTxnRepo.return_value.upsert = AsyncMock(
                side_effect=SQLAlchemyError("DB error"),
            )

            with pytest.raises(SQLAlchemyError, match="DB error"):
                await service.evaluate(request, mock_session)

    async def test_result_contains_triggered_rules(self, service, mock_session):
        request = _make_eval_request()
        txn = _make_transaction(request.external_id)
        triggered = [
            _make_triggered_rule("AMT_001", "High Amount", 85),
            _make_triggered_rule("GEO_002", "Risky Country", 15),
        ]
        assessment = _make_assessment(score=85, triggered_rules=triggered)
        alert = _make_alert()

        with (
            patch("app.core.evaluation_service.TransactionRepository") as MockTxnRepo,
            patch("app.core.evaluation_service.RuleRepository"),
            patch("app.core.evaluation_service.AlertRepository") as MockAlertRepo,
            patch("app.core.evaluation_service.ConfigRepository") as MockConfigRepo,
        ):
            MockTxnRepo.return_value.upsert = AsyncMock(return_value=txn)
            MockAlertRepo.return_value.create = AsyncMock(return_value=alert)
            MockConfigRepo.return_value.get_int = AsyncMock(return_value=90)

            detector = MagicMock()
            detector.evaluate.return_value = assessment
            detector.get_decision.return_value = Decision.FLAG
            detector.get_decision_tier.return_value = "high"
            detector.get_decision_tier_description.return_value = "High risk"

            result = await service.evaluate(request, mock_session, fraud_detector=detector)

            assert len(result.triggered_rules) == 2
            assert result.triggered_rules[0]["code"] == "AMT_001"
            assert result.triggered_rules[1]["code"] == "GEO_002"

    async def test_session_commit_called(self, service, mock_session):
        request = _make_eval_request()
        txn = _make_transaction(request.external_id)
        assessment = _make_assessment(score=5)

        with (
            patch("app.core.evaluation_service.TransactionRepository") as MockTxnRepo,
            patch("app.core.evaluation_service.RuleRepository"),
            patch("app.core.evaluation_service.AlertRepository"),
        ):
            MockTxnRepo.return_value.upsert = AsyncMock(return_value=txn)

            detector = MagicMock()
            detector.evaluate.return_value = assessment
            detector.get_decision.return_value = Decision.APPROVE
            detector.get_decision_tier.return_value = "low"
            detector.get_decision_tier_description.return_value = "Low risk"

            await service.evaluate(request, mock_session, fraud_detector=detector)

            mock_session.commit.assert_called_once()


# ---------------------------------------------------------------------------
# Tests — _auto_escalate helper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAutoEscalateHelper:
    async def test_score_100_confirms_via_review(self):
        alert_repo = AsyncMock()
        alert = _make_alert()
        session = AsyncMock()

        await _auto_escalate(alert_repo, alert, 100, session)

        alert_repo.review.assert_called_once_with(
            str(alert.id),
            status=AlertStatus.CONFIRMED,
            reviewer=SYSTEM_REVIEWER,
        )

    async def test_score_above_threshold_escalates(self):
        alert_repo = AsyncMock()
        alert = _make_alert()
        session = AsyncMock()

        with patch("app.core.evaluation_service.ConfigRepository") as MockConfigRepo:
            MockConfigRepo.return_value.get_int = AsyncMock(return_value=90)

            await _auto_escalate(alert_repo, alert, 95, session)

            alert_repo.review.assert_called_once_with(
                str(alert.id),
                status=AlertStatus.ESCALATED,
                reviewer=SYSTEM_REVIEWER,
            )

    async def test_score_below_threshold_no_action(self):
        alert_repo = AsyncMock()
        alert = _make_alert()
        session = AsyncMock()

        with patch("app.core.evaluation_service.ConfigRepository") as MockConfigRepo:
            MockConfigRepo.return_value.get_int = AsyncMock(return_value=90)

            await _auto_escalate(alert_repo, alert, 80, session)

            alert_repo.review.assert_not_called()

    async def test_score_at_threshold_boundary_escalates(self):
        alert_repo = AsyncMock()
        alert = _make_alert()
        session = AsyncMock()

        with patch("app.core.evaluation_service.ConfigRepository") as MockConfigRepo:
            MockConfigRepo.return_value.get_int = AsyncMock(return_value=90)

            await _auto_escalate(alert_repo, alert, 90, session)

            alert_repo.review.assert_called_once_with(
                str(alert.id),
                status=AlertStatus.ESCALATED,
                reviewer=SYSTEM_REVIEWER,
            )
