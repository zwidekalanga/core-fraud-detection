"""Unit tests for the FraudDetector core logic."""

from unittest.mock import MagicMock

import pytest

from app.config import get_settings
from app.core.fraud_detector import DECISION_TIERS, FraudDetector
from app.models.alert import Decision


def _make_mock_rule(
    code="AMT_001",
    name="High Value",
    description="High value transaction",
    category="amount",
    severity="high",
    score=50,
    enabled=True,
    conditions=None,
    effective_from=None,
    effective_to=None,
):
    """Create a mock FraudRule database object."""
    rule = MagicMock()
    rule.code = code
    rule.name = name
    rule.description = description
    rule.category = category
    rule.severity = severity
    rule.score = score
    rule.enabled = enabled
    rule.conditions = conditions or {"field": "amount", "operator": "greater_than", "value": 10000}
    rule.effective_from = effective_from
    rule.effective_to = effective_to
    return rule


class TestFraudDetectorInitialization:
    """Tests for FraudDetector creation and rule loading."""

    def test_create_detector(self):
        settings = get_settings()
        detector = FraudDetector(settings)
        assert detector is not None

    def test_load_rules(self):
        settings = get_settings()
        detector = FraudDetector(settings)
        rules = [_make_mock_rule(), _make_mock_rule(code="AMT_002", score=30)]
        detector.load_rules(rules)  # type: ignore[arg-type]
        assert len(detector._rules) == 2

    def test_disabled_rules_are_excluded(self):
        settings = get_settings()
        detector = FraudDetector(settings)
        rules = [
            _make_mock_rule(code="AMT_001", enabled=True),
            _make_mock_rule(code="AMT_002", enabled=False),
        ]
        detector.load_rules(rules)  # type: ignore[arg-type]
        assert len(detector._rules) == 1

    def test_empty_rules(self):
        settings = get_settings()
        detector = FraudDetector(settings)
        detector.load_rules([])
        assert len(detector._rules) == 0


class TestFraudDetectorEvaluation:
    """Tests for transaction evaluation logic."""

    def test_low_risk_transaction_approved(self):
        """A small, normal transaction should be APPROVE."""
        settings = get_settings()
        detector = FraudDetector(settings)
        # Rule only triggers for amounts > 10000
        detector.load_rules([_make_mock_rule(score=50)])  # type: ignore[arg-type]

        result = detector.evaluate(
            {
                "amount": 100.0,
                "currency": "ZAR",
                "transaction_type": "purchase",
                "channel": "pos",
                "location_country": "ZA",
            }
        )

        assert result.total_score < 40
        decision = detector.get_decision(result)
        assert decision == Decision.APPROVE

    def test_high_amount_triggers_rule(self):
        """A transaction above the threshold should trigger the rule."""
        settings = get_settings()
        detector = FraudDetector(settings)
        detector.load_rules([_make_mock_rule(score=50)])  # type: ignore[arg-type]

        result = detector.evaluate(
            {
                "amount": 50000.0,
                "currency": "ZAR",
                "transaction_type": "purchase",
                "channel": "online",
            }
        )

        assert result.total_score >= 40
        assert len(result.triggered_rules) >= 1
        decision = detector.get_decision(result)
        assert decision in (Decision.REVIEW, Decision.FLAG)

    def test_multiple_rules_accumulate_score(self):
        """Multiple triggered rules should accumulate their scores."""
        settings = get_settings()
        detector = FraudDetector(settings)
        detector.load_rules(  # type: ignore[arg-type]
            [
                _make_mock_rule(
                    code="AMT_001",
                    score=30,
                    conditions={
                        "field": "amount",
                        "operator": "greater_than",
                        "value": 1000,
                    },
                ),
                _make_mock_rule(
                    code="GEO_001",
                    score=40,
                    conditions={
                        "field": "location_country",
                        "operator": "in",
                        "value": ["KP", "IR"],
                    },
                ),
            ]
        )

        result = detector.evaluate(
            {
                "amount": 5000.0,
                "location_country": "KP",
                "transaction_type": "purchase",
                "channel": "online",
            }
        )

        assert result.total_score >= 70
        assert len(result.triggered_rules) == 2

    def test_no_rules_means_zero_score(self):
        """With no rules loaded, score should be 0 and decision APPROVE."""
        settings = get_settings()
        detector = FraudDetector(settings)
        detector.load_rules([])

        result = detector.evaluate({"amount": 999999.0})

        assert result.total_score == 0
        assert detector.get_decision(result) == Decision.APPROVE


class TestDecisionTiers:
    """Tests for decision tier mapping."""

    def test_decision_tiers_defined(self):
        assert len(DECISION_TIERS) == 3
        names = [t.name for t in DECISION_TIERS]
        assert "APPROVE" in names
        assert "REVIEW" in names
        assert "FLAG" in names

    def test_tier_boundaries(self):
        """Verify tier boundaries are contiguous and cover 0-100."""
        assert DECISION_TIERS[0].min_score == 0
        assert DECISION_TIERS[0].max_score == 40
        assert DECISION_TIERS[1].min_score == 40
        assert DECISION_TIERS[1].max_score == 80
        assert DECISION_TIERS[2].min_score == 80
        assert DECISION_TIERS[2].max_score == 101

    def test_get_decision_tier_description(self):
        settings = get_settings()
        detector = FraudDetector(settings)
        detector.load_rules([])
        result = detector.evaluate({"amount": 10.0})
        desc = detector.get_decision_tier_description(result)
        assert "Low risk" in desc or "approve" in desc.lower()


class TestSeverityMapping:
    """Tests for rule severity conversion to pylitmus."""

    @pytest.mark.parametrize("severity", ["low", "medium", "high", "critical"])
    def test_all_severity_levels_convert(self, severity):
        settings = get_settings()
        detector = FraudDetector(settings)
        rule = _make_mock_rule(severity=severity)
        detector.load_rules([rule])  # type: ignore[arg-type]
        assert len(detector._rules) == 1
