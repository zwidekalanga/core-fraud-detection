"""Pydantic schemas for fraud alerts."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from app.models.alert import AlertStatus


class AlertTransactionInfo(BaseModel):
    """Embedded transaction info in alert response."""

    external_id: str
    amount: Decimal
    currency: str
    transaction_type: str
    channel: str
    merchant_name: str | None = None
    location_country: str | None = None
    transaction_time: datetime

    model_config = {"from_attributes": True}


class TriggeredRuleDetail(BaseModel):
    """Detailed triggered rule information."""

    code: str
    name: str
    category: str
    severity: str
    score: int
    description: str | None = None


class AlertResponse(BaseModel):
    """Schema for alert response."""

    id: str
    transaction_id: str
    customer_id: str
    risk_score: int
    decision: str
    decision_tier: str | None = None
    decision_tier_description: str | None = None
    status: str
    triggered_rules: list[TriggeredRuleDetail]
    processing_time_ms: float | None
    reviewed_by: str | None
    reviewed_at: datetime | None
    review_notes: str | None
    created_at: datetime
    updated_at: datetime

    # Embedded transaction info
    transaction: AlertTransactionInfo | None = None

    model_config = {"from_attributes": True}


class AlertListResponse(BaseModel):
    """Schema for paginated alert list."""

    items: list[AlertResponse]
    total: int
    page: int
    size: int
    pages: int


class AlertReviewRequest(BaseModel):
    """Schema for reviewing an alert."""

    status: AlertStatus
    notes: str | None = Field(None, max_length=1000)

    @field_validator("status")
    @classmethod
    def validate_review_status(cls, v: AlertStatus) -> AlertStatus:
        """Only confirmed, dismissed, escalated are valid review targets."""
        allowed = {AlertStatus.CONFIRMED, AlertStatus.DISMISSED, AlertStatus.ESCALATED}
        if v not in allowed:
            raise ValueError(
                f"Invalid review status '{v}'. Must be one of: "
                f"{', '.join(s.value for s in allowed)}"
            )
        return v


# Valid alert status transitions (State pattern - M36)
VALID_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"confirmed", "dismissed", "escalated"},
    "escalated": {"confirmed", "dismissed"},
    "confirmed": set(),
    "dismissed": set(),
}


def validate_status_transition(current: str, target: str) -> None:
    """Validate that a status transition is allowed."""
    allowed = VALID_STATUS_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise ValueError(
            f"Cannot transition from '{current}' to '{target}'. "
            f"Allowed transitions: {allowed or 'none (terminal state)'}"
        )


class AlertReviewResponse(BaseModel):
    """Schema for alert review response."""

    id: str
    status: str
    reviewed_by: str
    reviewed_at: datetime
    review_notes: str | None


class AlertStatsResponse(BaseModel):
    """Typed response for alert statistics summary."""

    total: int
    by_status: dict[str, int]
    average_score: float


class DailyVolumeEntry(BaseModel):
    """Single day's alert volume."""

    date: str
    alerts: int


class DailyVolumeResponse(BaseModel):
    """Typed response for daily alert volume."""

    items: list[DailyVolumeEntry]
    days: int
