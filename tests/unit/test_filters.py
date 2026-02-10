"""Unit tests for declarative fastapi-filter classes."""

from datetime import UTC, datetime

from sqlalchemy import select

from app.filters.alert import AlertFilter
from app.filters.rule import RuleFilter
from app.models.alert import FraudAlert
from app.models.rule import FraudRule


def _compile(query) -> str:
    """Compile a SQLAlchemy query to a string for inspection."""
    return str(query.compile(compile_kwargs={"literal_binds": True}))


# =========================================================================
# AlertFilter
# =========================================================================


class TestAlertFilter:
    """Tests for AlertFilter."""

    def test_defaults_are_none(self):
        f = AlertFilter()
        assert f.status is None
        assert f.customer_id is None
        assert f.risk_score__gte is None
        assert f.risk_score__lte is None
        assert f.decision is None
        assert f.created_at__gte is None
        assert f.created_at__lte is None

    def test_filter_by_status(self):
        f = AlertFilter(status="pending")
        query = f.filter(select(FraudAlert))
        compiled = _compile(query)
        assert "WHERE" in compiled
        assert "pending" in compiled

    def test_filter_by_customer_id(self):
        f = AlertFilter(customer_id="cust-001")
        query = f.filter(select(FraudAlert))
        compiled = _compile(query)
        assert "WHERE" in compiled

    def test_filter_by_risk_score_range(self):
        f = AlertFilter(risk_score__gte=50, risk_score__lte=90)
        query = f.filter(select(FraudAlert))
        compiled = _compile(query)
        assert "risk_score" in compiled
        assert "50" in compiled
        assert "90" in compiled

    def test_filter_by_decision(self):
        f = AlertFilter(decision="flag")
        query = f.filter(select(FraudAlert))
        compiled = _compile(query)
        assert "flag" in compiled

    def test_date_range_filter(self):
        start = datetime(2024, 1, 1, tzinfo=UTC)
        end = datetime(2024, 12, 31, tzinfo=UTC)
        f = AlertFilter(created_at__gte=start, created_at__lte=end)
        query = f.filter(select(FraudAlert))
        compiled = _compile(query)
        assert "created_at" in compiled

    def test_no_filter_produces_clean_query(self):
        f = AlertFilter()
        query = f.filter(select(FraudAlert))
        compiled = _compile(query)
        assert "WHERE" not in compiled

    def test_sort_applies_order_by(self):
        f = AlertFilter(order_by=["-risk_score"])
        query = f.sort(select(FraudAlert))
        compiled = _compile(query)
        assert "ORDER BY" in compiled
        assert "risk_score" in compiled

    def test_partial_filters_only_apply_set_values(self):
        f = AlertFilter(status="confirmed")
        query = f.filter(select(FraudAlert))
        compiled = _compile(query)
        where_clause = compiled.split("WHERE", 1)[1] if "WHERE" in compiled else ""
        assert "confirmed" in where_clause
        assert "risk_score" not in where_clause
        assert "decision" not in where_clause


# =========================================================================
# RuleFilter
# =========================================================================


class TestRuleFilter:
    """Tests for RuleFilter."""

    def test_defaults_are_none(self):
        f = RuleFilter()
        assert f.category is None
        assert f.enabled is None

    def test_filter_by_category(self):
        f = RuleFilter(category="velocity")
        query = f.filter(select(FraudRule))
        compiled = _compile(query)
        assert "WHERE" in compiled
        assert "velocity" in compiled

    def test_filter_by_enabled(self):
        f = RuleFilter(enabled=True)
        query = f.filter(select(FraudRule))
        compiled = _compile(query)
        assert "enabled" in compiled

    def test_no_filter_produces_clean_query(self):
        f = RuleFilter()
        query = f.filter(select(FraudRule))
        compiled = _compile(query)
        assert "WHERE" not in compiled

    def test_combined_filters(self):
        f = RuleFilter(category="amount", enabled=True)
        query = f.filter(select(FraudRule))
        compiled = _compile(query)
        assert "amount" in compiled
        assert "enabled" in compiled

    def test_sort_applies_order_by(self):
        f = RuleFilter(order_by=["category"])
        query = f.sort(select(FraudRule))
        compiled = _compile(query)
        assert "ORDER BY" in compiled
        assert "category" in compiled
