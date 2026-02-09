"""Unit tests for Pydantic schema validation."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models.alert import AlertStatus
from app.models.rule import RuleCategory, Severity
from app.schemas.alert import AlertReviewRequest
from app.schemas.auth import RefreshRequest, TokenResponse, UserResponse
from app.schemas.rule import RuleCondition, RuleCreate, RuleUpdate
from app.schemas.transaction import TransactionEvaluateRequest

# ======================================================================
# TransactionEvaluateRequest
# ======================================================================


class TestTransactionEvaluateRequest:
    """Tests for transaction evaluation request validation."""

    def test_valid_transaction(self):
        txn = TransactionEvaluateRequest(
            external_id="TXN-001",
            customer_id="cust-123",
            amount=Decimal("150.00"),
            currency="ZAR",
            transaction_type="purchase",
            channel="online",
            location_country="ZA",
        )
        assert txn.external_id == "TXN-001"
        assert txn.amount == Decimal("150.00")
        assert txn.currency == "ZAR"

    def test_defaults_currency_to_zar(self):
        txn = TransactionEvaluateRequest(
            external_id="TXN-002",
            customer_id="cust-123",
            amount=Decimal("100"),
            transaction_type="purchase",
            channel="pos",
            location_country="ZA",
        )
        assert txn.currency == "ZAR"

    def test_rejects_zero_amount(self):
        with pytest.raises(ValidationError) as exc_info:
            TransactionEvaluateRequest(
                external_id="TXN-003",
                customer_id="cust-123",
                amount=Decimal("0"),
                transaction_type="purchase",
                channel="online",
                location_country="ZA",
            )
        assert (
            "greater than 0" in str(exc_info.value).lower() or "gt" in str(exc_info.value).lower()
        )

    def test_rejects_negative_amount(self):
        with pytest.raises(ValidationError):
            TransactionEvaluateRequest(
                external_id="TXN-004",
                customer_id="cust-123",
                amount=Decimal("-50.00"),
                transaction_type="purchase",
                channel="online",
                location_country="ZA",
            )

    def test_rejects_empty_external_id(self):
        with pytest.raises(ValidationError):
            TransactionEvaluateRequest(
                external_id="",
                customer_id="cust-123",
                amount=Decimal("100"),
                transaction_type="purchase",
                channel="online",
                location_country="ZA",
            )

    def test_rejects_missing_required_fields(self):
        with pytest.raises(ValidationError):
            TransactionEvaluateRequest(amount=Decimal("100"))  # type: ignore[call-arg]

    def test_optional_fields_default_to_none(self):
        txn = TransactionEvaluateRequest(
            external_id="TXN-005",
            customer_id="cust-123",
            amount=Decimal("100"),
            transaction_type="purchase",
            channel="online",
            location_country="ZA",
        )
        assert txn.merchant_id is None
        assert txn.merchant_name is None
        assert txn.device_fingerprint is None
        assert txn.ip_address is None

    def test_extra_data_defaults_to_empty_dict(self):
        txn = TransactionEvaluateRequest(
            external_id="TXN-006",
            customer_id="cust-123",
            amount=Decimal("100"),
            transaction_type="purchase",
            channel="online",
            location_country="ZA",
        )
        assert txn.extra_data == {}

    def test_rejects_too_long_external_id(self):
        with pytest.raises(ValidationError):
            TransactionEvaluateRequest(
                external_id="X" * 101,
                customer_id="cust-123",
                amount=Decimal("100"),
                transaction_type="purchase",
                channel="online",
                location_country="ZA",
            )

    def test_location_country_length_constraints(self):
        # Valid 2-char code
        txn = TransactionEvaluateRequest(
            external_id="TXN-007",
            customer_id="cust-123",
            amount=Decimal("100"),
            transaction_type="purchase",
            channel="online",
            location_country="ZA",
        )
        assert txn.location_country == "ZA"

        # Valid 3-char code
        txn2 = TransactionEvaluateRequest(
            external_id="TXN-008",
            customer_id="cust-123",
            amount=Decimal("100"),
            transaction_type="purchase",
            channel="online",
            location_country="ZAF",
        )
        assert txn2.location_country == "ZAF"

        # Too short
        with pytest.raises(ValidationError):
            TransactionEvaluateRequest(
                external_id="TXN-009",
                customer_id="cust-123",
                amount=Decimal("100"),
                transaction_type="purchase",
                channel="online",
                location_country="Z",
            )


# ======================================================================
# RuleCreate / RuleUpdate / RuleCondition
# ======================================================================


class TestRuleCondition:
    """Tests for rule condition validation."""

    def test_valid_simple_condition(self):
        cond = RuleCondition(field="amount", operator="greater_than", value=10000)
        assert cond.field == "amount"
        assert cond.operator == "greater_than"

    def test_rejects_invalid_operator(self):
        with pytest.raises(ValidationError) as exc_info:
            RuleCondition(field="amount", operator="not_a_real_operator", value=1)
        assert "Invalid operator" in str(exc_info.value)

    def test_allows_none_operator_for_compound_conditions(self):
        cond = RuleCondition(
            all=[
                RuleCondition(field="amount", operator="greater_than", value=1000),
                RuleCondition(field="channel", operator="equals", value="online"),
            ]
        )
        assert cond.operator is None
        assert cond.all is not None and len(cond.all) == 2

    @pytest.mark.parametrize(
        "op",
        [
            "equals",
            "not_equals",
            "greater_than",
            "less_than",
            "between",
            "in",
            "not_in",
            "contains",
            "is_null",
            "is_not_null",
        ],
    )
    def test_valid_operators(self, op):
        cond = RuleCondition(field="test_field", operator=op, value="test")
        assert cond.operator == op


class TestRuleCreate:
    """Tests for rule creation schema validation."""

    def test_valid_rule(self):
        rule = RuleCreate(
            code="AMT_001",
            name="High Value Transaction",
            category=RuleCategory.AMOUNT,
            severity=Severity.HIGH,
            score=50,
            conditions={"field": "amount", "operator": "greater_than", "value": 50000},
        )
        assert rule.code == "AMT_001"
        assert rule.score == 50

    def test_rejects_invalid_code_format(self):
        with pytest.raises(ValidationError):
            RuleCreate(
                code="invalid-code",
                name="Bad Code",
                category=RuleCategory.AMOUNT,
                severity=Severity.LOW,
                score=10,
                conditions={"field": "amount", "operator": "greater_than", "value": 100},
            )

    def test_rejects_score_over_100(self):
        with pytest.raises(ValidationError):
            RuleCreate(
                code="AMT_002",
                name="Too High",
                category=RuleCategory.AMOUNT,
                severity=Severity.MEDIUM,
                score=150,
                conditions={"field": "amount", "operator": "greater_than", "value": 100},
            )

    def test_rejects_negative_score(self):
        with pytest.raises(ValidationError):
            RuleCreate(
                code="AMT_003",
                name="Negative",
                category=RuleCategory.AMOUNT,
                severity=Severity.LOW,
                score=-10,
                conditions={"field": "amount", "operator": "greater_than", "value": 100},
            )

    def test_defaults_enabled_to_true(self):
        rule = RuleCreate(
            code="VEL_001",
            name="Velocity Check",
            category=RuleCategory.VELOCITY,
            severity=Severity.MEDIUM,
            score=30,
            conditions={"field": "txn_count_1h", "operator": "greater_than", "value": 10},
        )
        assert rule.enabled is True

    @pytest.mark.parametrize("code", ["AMT_001", "GEO_100", "VEL_999", "COMB_050"])
    def test_valid_code_patterns(self, code):
        rule = RuleCreate(
            code=code,
            name="Test",
            category=RuleCategory.AMOUNT,
            severity=Severity.LOW,
            score=10,
            conditions={"field": "amount", "operator": "greater_than", "value": 100},
        )
        assert rule.code == code


class TestRuleUpdate:
    """Tests for rule update schema validation."""

    def test_allows_partial_update(self):
        update = RuleUpdate(name="Updated Name")  # type: ignore[call-arg]
        assert update.name == "Updated Name"
        assert update.score is None
        assert update.enabled is None

    def test_allows_empty_update(self):
        update = RuleUpdate()  # type: ignore[call-arg]
        assert update.name is None

    def test_validates_score_range(self):
        with pytest.raises(ValidationError):
            RuleUpdate(score=200)  # type: ignore[call-arg]


# ======================================================================
# AlertReviewRequest
# ======================================================================


class TestAlertReviewRequest:
    """Tests for alert review request validation."""

    def test_valid_review_confirmed(self):
        review = AlertReviewRequest(status=AlertStatus.CONFIRMED, notes="Verified fraud")
        assert review.status == AlertStatus.CONFIRMED
        assert review.notes == "Verified fraud"

    def test_valid_review_dismissed(self):
        review = AlertReviewRequest(status=AlertStatus.DISMISSED)  # type: ignore[call-arg]
        assert review.notes is None

    def test_valid_review_escalated(self):
        review = AlertReviewRequest(status=AlertStatus.ESCALATED, notes="Needs investigation")
        assert review.status == AlertStatus.ESCALATED

    def test_rejects_notes_over_max_length(self):
        with pytest.raises(ValidationError):
            AlertReviewRequest(status=AlertStatus.CONFIRMED, notes="X" * 1001)

    def test_allows_notes_at_max_length(self):
        review = AlertReviewRequest(status=AlertStatus.CONFIRMED, notes="X" * 1000)
        assert review.notes is not None and len(review.notes) == 1000


# ======================================================================
# Auth schemas
# ======================================================================


class TestTokenResponse:
    """Tests for token response schema."""

    def test_defaults_token_type_to_bearer(self):
        resp = TokenResponse(
            access_token="abc",
            refresh_token="def",
            expires_in=1800,
        )
        assert resp.token_type == "bearer"


class TestRefreshRequest:
    """Tests for refresh request schema."""

    def test_valid_request(self):
        req = RefreshRequest(refresh_token="some-token-value")
        assert req.refresh_token == "some-token-value"


class TestUserResponse:
    """Tests for user response schema."""

    def test_valid_user(self):
        user = UserResponse(
            id="user-123",
            username="admin",
            email="admin@test.com",
            full_name="Admin User",
            role="admin",
            is_active=True,
        )
        assert user.username == "admin"
        assert user.role == "admin"
