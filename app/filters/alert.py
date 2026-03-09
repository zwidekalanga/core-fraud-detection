"""Declarative filter for fraud alerts."""

from datetime import datetime
from typing import Any

from sqlalchemy import Select

from app.filters.base import Filter
from app.models.alert import FraudAlert
from app.models.transaction import Transaction


class AlertFilter(Filter):
    """Query-param filter for the ``GET /alerts`` endpoint.

    ``account_number`` lives on the related ``Transaction`` model, so it is
    handled via an explicit JOIN inside :meth:`filter` and excluded from
    fastapi-filter's automatic column resolution via :attr:`filtering_fields`.
    """

    # Fields resolved automatically against FraudAlert columns:
    status: str | None = None
    reference_number: str | None = None
    customer_id: str | None = None
    risk_score__gte: int | None = None
    risk_score__lte: int | None = None
    decision: str | None = None
    created_at__gte: datetime | None = None
    created_at__lte: datetime | None = None
    order_by: list[str] | None = None

    # Cross-table field — handled manually in filter():
    account_number: str | None = None

    class Constants(Filter.Constants):
        model = FraudAlert

    @property
    def filtering_fields(self):
        """Exclude account_number from automatic column resolution."""
        return {
            k: v for k, v in super().filtering_fields if k != "account_number"
        }.items()

    def filter(self, query: Select[Any]) -> Select[Any]:  # type: ignore[override]
        """Apply standard column filters, then the cross-table account_number JOIN."""
        query = super().filter(query)
        if self.account_number:
            query = query.join(
                Transaction, FraudAlert.transaction_id == Transaction.id,
            ).where(Transaction.account_number == self.account_number)
        return query
