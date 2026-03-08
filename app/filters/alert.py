"""Declarative filter for fraud alerts."""

from datetime import datetime
from typing import Any

from sqlalchemy import Select

from app.filters.base import Filter
from app.models.alert import FraudAlert
from app.models.transaction import Transaction


class AlertFilter(Filter):
    """Query-param filter for the ``GET /alerts`` endpoint."""

    status: str | None = None
    reference_number: str | None = None
    customer_id: str | None = None
    risk_score__gte: int | None = None
    risk_score__lte: int | None = None
    decision: str | None = None
    created_at__gte: datetime | None = None
    created_at__lte: datetime | None = None
    order_by: list[str] | None = None

    class Constants(Filter.Constants):
        model = FraudAlert

    @staticmethod
    def apply_account_number(query: Select[Any], account_number: str | None) -> Select[Any]:
        """Join through Transaction to filter by account number."""
        if account_number:
            query = query.join(
                Transaction, FraudAlert.transaction_id == Transaction.id
            ).where(Transaction.account_number == account_number)
        return query
