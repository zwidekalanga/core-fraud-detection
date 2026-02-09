"""Pydantic schemas for fraud alerts."""
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

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


class AlertReviewResponse(BaseModel):
    """Schema for alert review response."""
    id: str
    status: str
    reviewed_by: str
    reviewed_at: datetime
    review_notes: str | None
