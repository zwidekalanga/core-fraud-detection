"""Unit tests for database models and enums."""

import pytest

from app.models.alert import AlertStatus, Decision
from app.models.rule import RuleCategory, Severity
from app.models.transaction import Channel
from app.models.user import UserRole


class TestAlertStatusEnum:
    """Tests for AlertStatus enum."""

    def test_all_statuses_defined(self):
        assert AlertStatus.PENDING.value == "pending"
        assert AlertStatus.CONFIRMED.value == "confirmed"
        assert AlertStatus.DISMISSED.value == "dismissed"
        assert AlertStatus.ESCALATED.value == "escalated"

    def test_status_count(self):
        assert len(AlertStatus) == 4

    def test_string_coercion(self):
        """AlertStatus inherits str, so value comparisons should work."""
        assert AlertStatus.PENDING == "pending"
        assert AlertStatus.CONFIRMED.value == "confirmed"

    def test_from_string(self):
        status = AlertStatus("pending")
        assert status == AlertStatus.PENDING


class TestDecisionEnum:
    """Tests for Decision enum."""

    def test_all_decisions_defined(self):
        assert Decision.APPROVE.value == "approve"
        assert Decision.REVIEW.value == "review"
        assert Decision.FLAG.value == "flag"

    def test_decision_count(self):
        assert len(Decision) == 3

    def test_from_string(self):
        assert Decision("approve") == Decision.APPROVE
        assert Decision("review") == Decision.REVIEW
        assert Decision("flag") == Decision.FLAG

    def test_invalid_decision_raises(self):
        with pytest.raises(ValueError):
            Decision("reject")


class TestRuleCategoryEnum:
    """Tests for RuleCategory enum."""

    def test_all_categories_defined(self):
        expected = {"velocity", "amount", "geographic", "behavioral", "device", "combined"}
        actual = {c.value for c in RuleCategory}
        assert actual == expected

    def test_category_count(self):
        assert len(RuleCategory) == 6


class TestSeverityEnum:
    """Tests for Severity enum."""

    def test_all_severities_defined(self):
        assert Severity.LOW.value == "low"
        assert Severity.MEDIUM.value == "medium"
        assert Severity.HIGH.value == "high"
        assert Severity.CRITICAL.value == "critical"

    def test_severity_count(self):
        assert len(Severity) == 4


class TestChannelEnum:
    """Tests for Channel enum."""

    def test_all_channels_defined(self):
        expected = {"card", "eft", "mobile", "online", "atm", "branch", "qr", "pos"}
        actual = {c.value for c in Channel}
        assert actual == expected

    def test_channel_count(self):
        assert len(Channel) == 8


class TestUserRoleEnum:
    """Tests for UserRole enum."""

    def test_all_roles_defined(self):
        assert UserRole.admin.value == "admin"
        assert UserRole.analyst.value == "analyst"
        assert UserRole.viewer.value == "viewer"

    def test_role_count(self):
        assert len(UserRole) == 3

    def test_string_coercion(self):
        assert UserRole.admin == "admin"
