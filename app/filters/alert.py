"""Declarative filter for fraud alerts."""

from datetime import datetime
from typing import Optional

from fastapi_filter.contrib.sqlalchemy import Filter

from app.models.alert import FraudAlert


class AlertFilter(Filter):
    """Query-param filter for the ``GET /alerts`` endpoint."""

    status: Optional[str] = None
    customer_id: Optional[str] = None
    risk_score__gte: Optional[int] = None
    risk_score__lte: Optional[int] = None
    decision: Optional[str] = None
    created_at__gte: Optional[datetime] = None
    created_at__lte: Optional[datetime] = None
    order_by: Optional[list[str]] = None

    class Constants(Filter.Constants):
        model = FraudAlert
