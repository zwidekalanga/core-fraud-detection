"""Declarative filter for fraud rules."""

from typing import Optional

from fastapi_filter.contrib.sqlalchemy import Filter

from app.models.rule import FraudRule


class RuleFilter(Filter):
    """Query-param filter for the ``GET /rules`` endpoint."""

    category: Optional[str] = None
    enabled: Optional[bool] = None
    order_by: Optional[list[str]] = None

    class Constants(Filter.Constants):
        model = FraudRule
