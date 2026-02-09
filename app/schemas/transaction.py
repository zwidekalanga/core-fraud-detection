"""Pydantic schemas for transactions."""
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class TransactionEvaluateRequest(BaseModel):
    """Schema for transaction evaluation request."""
    external_id: str = Field(..., min_length=1, max_length=100)
    customer_id: str = Field(..., min_length=1)
    amount: Decimal = Field(..., gt=0)
    currency: str = Field(default="ZAR", min_length=3, max_length=3)
    transaction_type: str = Field(..., min_length=1)
    channel: str = Field(..., min_length=1)

    # Optional fields
    merchant_id: str | None = None
    merchant_name: str | None = None
    merchant_category: str | None = None
    location_country: str | None = Field(None, min_length=2, max_length=3)
    location_city: str | None = None
    device_fingerprint: str | None = None
    ip_address: str | None = None
    transaction_time: datetime | None = None
    extra_data: dict[str, Any] = Field(default_factory=dict)


class TriggeredRuleInfo(BaseModel):
    """Information about a triggered rule."""
    code: str
    name: str
    category: str
    severity: str
    score: int
    description: str | None = None


class EvaluationResponse(BaseModel):
    """Schema for evaluation response."""
    transaction_id: str
    external_id: str
    risk_score: int
    decision: str
    decision_tier: str
    decision_tier_description: str
    triggered_rules: list[TriggeredRuleInfo]
    processing_time_ms: float
    alert_created: bool
    alert_id: str | None = None
