"""Declarative filter for fraud rules."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select

from app.filters.base import Filter
from app.models.rule import FraudRule


class RuleFilter(Filter):
    """Query-param filter for the ``GET /rules`` endpoint."""

    category: str | None = None
    enabled: bool | None = None
    order_by: list[str] | None = None

    class Constants(Filter.Constants):
        model = FraudRule

    def apply_temporal_bounds(self, query: Select[Any]) -> Select[Any]:
        """When enabled=True, also enforce effective_from/to date bounds."""
        if self.enabled is True:
            now = datetime.now(UTC)
            query = query.where(
                (FraudRule.effective_from.is_(None)) | (FraudRule.effective_from <= now),
                (FraudRule.effective_to.is_(None)) | (FraudRule.effective_to >= now),
            )
        return query

    def apply_default_ordering(self, query: Select[Any]) -> Select[Any]:
        """Apply category+code ordering when no explicit order_by is set."""
        if not self.order_by:
            query = query.order_by(FraudRule.category, FraudRule.code)
        return query
