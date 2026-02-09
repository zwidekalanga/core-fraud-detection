"""Unit tests for the FraudDetector core logic (expanded with M48 complex condition tests)."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from app.config import get_settings
from app.core.fraud_detector import DECISION_TIERS, EvaluationError, FraudDetector
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

    def test_reload_rules_replaces_previous(self):
        """Reloading rules should replace the previous set, not append."""
        settings = get_settings()
        detector = FraudDetector(settings)
        detector.load_rules([_make_mock_rule(code="R1")])  # type: ignore[arg-type]
        assert len(detector._rules) == 1

        detector.load_rules([_make_mock_rule(code="R2"), _make_mock_rule(code="R3")])  # type: ignore[arg-type]
        assert len(detector._rules) == 2


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

    def test_score_capped_at_100(self):
        """Even with many high-score rules, total should not exceed engine max."""
        settings = get_settings()
        detector = FraudDetector(settings)
        detector.load_rules(  # type: ignore[arg-type]
            [
                _make_mock_rule(
                    code=f"R{i}",
                    score=50,
                    conditions={"field": "amount", "operator": "greater_than", "value": 0},
                )
                for i in range(5)
            ]
        )
        result = detector.evaluate({"amount": 100.0})
        # pylitmus caps at 100
        assert result.total_score <= 100

    def test_evaluation_returns_processing_time(self):
        """Assessment result should include processing time."""
        settings = get_settings()
        detector = FraudDetector(settings)
        detector.load_rules([_make_mock_rule()])  # type: ignore[arg-type]

        result = detector.evaluate({"amount": 50000.0})
        assert hasattr(result, "processing_time_ms")
        assert result.processing_time_ms >= 0

    def test_empty_data_dict(self):
        """Evaluating an empty dict should not crash."""
        settings = get_settings()
        detector = FraudDetector(settings)
        detector.load_rules([_make_mock_rule()])  # type: ignore[arg-type]

        result = detector.evaluate({})
        # No field to match → no rules fire
        assert result.total_score == 0


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

    def test_review_tier_description(self):
        """Score in 40-79 range should yield REVIEW tier."""
        settings = get_settings()
        detector = FraudDetector(settings)
        detector.load_rules(  # type: ignore[arg-type]
            [
                _make_mock_rule(
                    code="MED_001",
                    score=50,
                    conditions={"field": "amount", "operator": "greater_than", "value": 0},
                ),
            ]
        )
        result = detector.evaluate({"amount": 100.0})
        assert result.total_score >= 40
        assert result.total_score < 80
        desc = detector.get_decision_tier_description(result)
        assert "review" in desc.lower()

    def test_flag_tier_description(self):
        """Score >= 80 should yield FLAG tier."""
        settings = get_settings()
        detector = FraudDetector(settings)
        detector.load_rules(  # type: ignore[arg-type]
            [
                _make_mock_rule(
                    code="HIGH_001",
                    score=90,
                    conditions={"field": "amount", "operator": "greater_than", "value": 0},
                ),
            ]
        )
        result = detector.evaluate({"amount": 100.0})
        assert result.total_score >= 80
        desc = detector.get_decision_tier_description(result)
        assert "flag" in desc.lower() or "investigation" in desc.lower()


class TestSeverityMapping:
    """Tests for rule severity conversion to pylitmus."""

    @pytest.mark.parametrize("severity", ["low", "medium", "high", "critical"])
    def test_all_severity_levels_convert(self, severity):
        settings = get_settings()
        detector = FraudDetector(settings)
        rule = _make_mock_rule(severity=severity)
        detector.load_rules([rule])  # type: ignore[arg-type]
        assert len(detector._rules) == 1

    def test_unknown_severity_defaults_to_medium(self):
        """An unrecognised severity should fall back to MEDIUM."""
        settings = get_settings()
        detector = FraudDetector(settings)
        rule = _make_mock_rule(severity="unknown_level")
        detector.load_rules([rule])  # type: ignore[arg-type]
        assert len(detector._rules) == 1


# ======================================================================
# M48: Complex rule condition tests
# ======================================================================


class TestOperatorGreaterThan:
    """Tests for the 'greater_than' condition operator."""

    def _detector_with_rule(self, conditions, score=50):
        settings = get_settings()
        detector = FraudDetector(settings)
        detector.load_rules(  # type: ignore[arg-type]
            [_make_mock_rule(code="GT_001", conditions=conditions, score=score)]
        )
        return detector

    def test_fires_above_threshold(self):
        det = self._detector_with_rule(
            {"field": "amount", "operator": "greater_than", "value": 5000}
        )
        result = det.evaluate({"amount": 10000.0})
        assert len(result.triggered_rules) == 1

    def test_does_not_fire_below_threshold(self):
        det = self._detector_with_rule(
            {"field": "amount", "operator": "greater_than", "value": 5000}
        )
        result = det.evaluate({"amount": 100.0})
        assert len(result.triggered_rules) == 0

    def test_boundary_value_not_triggered(self):
        """Exactly equal should NOT trigger greater_than."""
        det = self._detector_with_rule(
            {"field": "amount", "operator": "greater_than", "value": 5000}
        )
        result = det.evaluate({"amount": 5000.0})
        assert len(result.triggered_rules) == 0


class TestOperatorLessThan:
    """Tests for the 'less_than' condition operator."""

    def _detector_with_rule(self, conditions, score=50):
        settings = get_settings()
        detector = FraudDetector(settings)
        detector.load_rules(  # type: ignore[arg-type]
            [_make_mock_rule(code="LT_001", conditions=conditions, score=score)]
        )
        return detector

    def test_fires_below_threshold(self):
        det = self._detector_with_rule({"field": "amount", "operator": "less_than", "value": 10})
        result = det.evaluate({"amount": 1.0})
        assert len(result.triggered_rules) == 1

    def test_does_not_fire_above_threshold(self):
        det = self._detector_with_rule({"field": "amount", "operator": "less_than", "value": 10})
        result = det.evaluate({"amount": 100.0})
        assert len(result.triggered_rules) == 0


class TestOperatorEquals:
    """Tests for the 'equals' condition operator."""

    def _detector_with_rule(self, conditions, score=50):
        settings = get_settings()
        detector = FraudDetector(settings)
        detector.load_rules(  # type: ignore[arg-type]
            [_make_mock_rule(code="EQ_001", conditions=conditions, score=score)]
        )
        return detector

    def test_string_equality(self):
        det = self._detector_with_rule(
            {"field": "channel", "operator": "equals", "value": "online"}
        )
        result = det.evaluate({"channel": "online", "amount": 100.0})
        assert len(result.triggered_rules) == 1

    def test_string_inequality(self):
        det = self._detector_with_rule(
            {"field": "channel", "operator": "equals", "value": "online"}
        )
        result = det.evaluate({"channel": "pos", "amount": 100.0})
        assert len(result.triggered_rules) == 0


class TestOperatorIn:
    """Tests for the 'in' condition operator (membership check)."""

    def _detector_with_rule(self, conditions, score=50):
        settings = get_settings()
        detector = FraudDetector(settings)
        detector.load_rules(  # type: ignore[arg-type]
            [_make_mock_rule(code="IN_001", conditions=conditions, score=score)]
        )
        return detector

    def test_value_in_list(self):
        det = self._detector_with_rule(
            {"field": "location_country", "operator": "in", "value": ["KP", "IR", "SY"]}
        )
        result = det.evaluate({"location_country": "KP", "amount": 100.0})
        assert len(result.triggered_rules) == 1

    def test_value_not_in_list(self):
        det = self._detector_with_rule(
            {"field": "location_country", "operator": "in", "value": ["KP", "IR", "SY"]}
        )
        result = det.evaluate({"location_country": "ZA", "amount": 100.0})
        assert len(result.triggered_rules) == 0

    def test_empty_list_never_matches(self):
        det = self._detector_with_rule({"field": "location_country", "operator": "in", "value": []})
        result = det.evaluate({"location_country": "ZA", "amount": 100.0})
        assert len(result.triggered_rules) == 0


class TestOperatorNotIn:
    """Tests for the 'not_in' condition operator."""

    def _detector_with_rule(self, conditions, score=50):
        settings = get_settings()
        detector = FraudDetector(settings)
        detector.load_rules(  # type: ignore[arg-type]
            [_make_mock_rule(code="NIN_001", conditions=conditions, score=score)]
        )
        return detector

    def test_value_not_in_list(self):
        det = self._detector_with_rule(
            {"field": "currency", "operator": "not_in", "value": ["ZAR", "USD", "EUR"]}
        )
        result = det.evaluate({"currency": "BTC", "amount": 100.0})
        assert len(result.triggered_rules) == 1

    def test_value_in_list_does_not_fire(self):
        det = self._detector_with_rule(
            {"field": "currency", "operator": "not_in", "value": ["ZAR", "USD", "EUR"]}
        )
        result = det.evaluate({"currency": "ZAR", "amount": 100.0})
        assert len(result.triggered_rules) == 0


class TestMissingFieldBehaviour:
    """Tests for behaviour when transaction data is missing fields referenced by rules."""

    def test_missing_field_does_not_trigger_greater_than(self):
        settings = get_settings()
        detector = FraudDetector(settings)
        detector.load_rules(  # type: ignore[arg-type]
            [
                _make_mock_rule(
                    code="AMT_001",
                    conditions={"field": "amount", "operator": "greater_than", "value": 100},
                )
            ]
        )
        # No 'amount' key at all
        result = detector.evaluate({"currency": "ZAR"})
        assert len(result.triggered_rules) == 0

    def test_missing_field_does_not_trigger_in(self):
        settings = get_settings()
        detector = FraudDetector(settings)
        detector.load_rules(  # type: ignore[arg-type]
            [
                _make_mock_rule(
                    code="GEO_001",
                    conditions={
                        "field": "location_country",
                        "operator": "in",
                        "value": ["KP"],
                    },
                )
            ]
        )
        result = detector.evaluate({"amount": 100.0})
        assert len(result.triggered_rules) == 0


class TestMultipleRuleCombinations:
    """Tests for complex multi-rule scenarios."""

    def test_only_matching_rules_trigger(self):
        """When some rules match and some don't, only matching ones contribute."""
        settings = get_settings()
        detector = FraudDetector(settings)
        detector.load_rules(  # type: ignore[arg-type]
            [
                _make_mock_rule(
                    code="AMT_HIGH",
                    score=30,
                    conditions={"field": "amount", "operator": "greater_than", "value": 50000},
                ),
                _make_mock_rule(
                    code="AMT_MED",
                    score=20,
                    conditions={"field": "amount", "operator": "greater_than", "value": 1000},
                ),
                _make_mock_rule(
                    code="GEO_BAD",
                    score=40,
                    conditions={
                        "field": "location_country",
                        "operator": "in",
                        "value": ["KP"],
                    },
                ),
            ]
        )
        # amount=5000 triggers AMT_MED but not AMT_HIGH; country=ZA doesn't trigger GEO_BAD
        result = detector.evaluate(
            {"amount": 5000.0, "location_country": "ZA", "channel": "online"}
        )
        codes = [r.rule_code for r in result.triggered_rules]
        assert "AMT_MED" in codes
        assert "AMT_HIGH" not in codes
        assert "GEO_BAD" not in codes

    def test_all_rules_trigger_high_risk(self):
        """When all rules fire, score should be high and decision FLAG."""
        settings = get_settings()
        detector = FraudDetector(settings)
        detector.load_rules(  # type: ignore[arg-type]
            [
                _make_mock_rule(
                    code="AMT_001",
                    score=40,
                    conditions={"field": "amount", "operator": "greater_than", "value": 1000},
                ),
                _make_mock_rule(
                    code="GEO_001",
                    score=40,
                    conditions={
                        "field": "location_country",
                        "operator": "in",
                        "value": ["KP"],
                    },
                ),
                _make_mock_rule(
                    code="CHAN_001",
                    score=20,
                    conditions={"field": "channel", "operator": "equals", "value": "online"},
                ),
            ]
        )
        result = detector.evaluate(
            {"amount": 50000.0, "location_country": "KP", "channel": "online"}
        )
        assert result.total_score >= 80
        assert detector.get_decision(result) == Decision.FLAG

    def test_score_just_below_review_threshold(self):
        """Score of 39 should still be APPROVE."""
        settings = get_settings()
        detector = FraudDetector(settings)
        detector.load_rules(  # type: ignore[arg-type]
            [
                _make_mock_rule(
                    code="LOW_001",
                    score=39,
                    conditions={"field": "amount", "operator": "greater_than", "value": 0},
                ),
            ]
        )
        result = detector.evaluate({"amount": 100.0})
        assert result.total_score == 39
        assert detector.get_decision(result) == Decision.APPROVE

    def test_score_at_review_boundary(self):
        """Score of exactly 40 should be REVIEW."""
        settings = get_settings()
        detector = FraudDetector(settings)
        detector.load_rules(  # type: ignore[arg-type]
            [
                _make_mock_rule(
                    code="MED_001",
                    score=40,
                    conditions={"field": "amount", "operator": "greater_than", "value": 0},
                ),
            ]
        )
        result = detector.evaluate({"amount": 100.0})
        assert result.total_score == 40
        assert detector.get_decision(result) == Decision.REVIEW

    def test_score_at_flag_boundary(self):
        """Score of exactly 80 should be FLAG."""
        settings = get_settings()
        detector = FraudDetector(settings)
        detector.load_rules(  # type: ignore[arg-type]
            [
                _make_mock_rule(
                    code="HI_001",
                    score=80,
                    conditions={"field": "amount", "operator": "greater_than", "value": 0},
                ),
            ]
        )
        result = detector.evaluate({"amount": 100.0})
        assert result.total_score == 80
        assert detector.get_decision(result) == Decision.FLAG


class TestEffectiveDateFiltering:
    """Tests for rules with effective_from/effective_to date ranges.

    Note: pylitmus internally uses ``datetime.utcnow()`` (naive) for date
    comparisons, so test dates must also be naive to avoid TypeError.
    """

    def test_expired_rule_not_compiled(self):
        """A rule whose effective_to is in the past should not be compiled by RETE."""
        settings = get_settings()
        detector = FraudDetector(settings)
        now = datetime.utcnow()
        rule = _make_mock_rule(
            code="EXP_001",
            effective_from=now - timedelta(days=30),
            effective_to=now - timedelta(days=1),
            conditions={"field": "amount", "operator": "greater_than", "value": 0},
        )
        detector.load_rules([rule])  # type: ignore[arg-type]
        # pylitmus filters expired rules at compile time → score stays 0
        result = detector.evaluate({"amount": 50000.0})
        assert result.total_score == 0

    def test_future_rule_not_effective(self):
        """A rule with effective_from in the future should not fire."""
        settings = get_settings()
        detector = FraudDetector(settings)
        now = datetime.utcnow()
        rule = _make_mock_rule(
            code="FUT_001",
            effective_from=now + timedelta(days=1),
            conditions={"field": "amount", "operator": "greater_than", "value": 0},
        )
        detector.load_rules([rule])  # type: ignore[arg-type]
        # Rule is loaded into the list but pylitmus skips it at compile time
        assert len(detector._rules) == 1
        result = detector.evaluate({"amount": 50000.0})
        assert result.total_score == 0

    def test_currently_active_rule_fires(self):
        """A rule within its effective window should fire."""
        settings = get_settings()
        detector = FraudDetector(settings)
        now = datetime.utcnow()
        rule = _make_mock_rule(
            code="ACT_001",
            effective_from=now - timedelta(days=7),
            effective_to=now + timedelta(days=7),
            conditions={"field": "amount", "operator": "greater_than", "value": 0},
            score=50,
        )
        detector.load_rules([rule])  # type: ignore[arg-type]
        result = detector.evaluate({"amount": 100.0})
        assert result.total_score >= 50
