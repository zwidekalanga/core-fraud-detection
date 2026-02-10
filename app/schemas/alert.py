"""Pydantic schemas for fraud alerts."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from app.models.alert import AlertStatus


class ReviewerInfo(BaseModel):
    """Value object representing who reviewed an alert."""

    id: str
    username: str

    model_config = {"frozen": True}


SYSTEM_REVIEWER = ReviewerInfo(id="system:auto-escalation", username="System")


class AlertTransactionInfo(BaseModel):
    """Embedded transaction info in alert response."""

    external_id: str
    account_number: str | None = None
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
    reference_number: str | None = None
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
    reviewed_by_username: str | None = None
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


class AlertReviewResponse(BaseModel):
    """Schema for alert review response."""

    id: str
    reference_number: str | None = None
    status: str
    reviewed_by: str
    reviewed_by_username: str | None = None
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
