"""Protocol definitions for repository interfaces.

These protocols enable type-safe mocking in tests and decouple service
layer code from concrete SQLAlchemy implementations.
"""

from typing import Any, Protocol

from app.filters.alert import AlertFilter
from app.filters.rule import RuleFilter
from app.models.alert import AlertStatus, FraudAlert
from app.models.config import SystemConfig
from app.models.rule import FraudRule
from app.models.transaction import Transaction
from app.schemas.alert import ReviewerInfo
from app.schemas.rule import RuleCreate, RuleUpdate
from app.schemas.transaction import TransactionEvaluateRequest


class RuleRepositoryProtocol(Protocol):
    """Interface for fraud rule data access."""

    async def get_all(
        self, filters: RuleFilter, page: int = 1, size: int = 50
    ) -> tuple[list[FraudRule], int]: ...

    async def get_by_code(self, code: str) -> FraudRule | None: ...

    async def get_enabled_rules(self) -> list[FraudRule]: ...

    async def create(self, rule_data: RuleCreate) -> FraudRule: ...

    async def update(self, code: str, rule_data: RuleUpdate) -> FraudRule | None: ...

    async def delete(self, code: str) -> bool: ...

    async def toggle(self, code: str) -> FraudRule | None: ...


class AlertRepositoryProtocol(Protocol):
    """Interface for fraud alert data access."""

    async def get_all(
        self,
        filters: AlertFilter,
        page: int = 1,
        size: int = 50,
        account_number: str | None = None,
    ) -> tuple[list[FraudAlert], int]: ...

    async def get_by_id(self, alert_id: str) -> FraudAlert | None: ...

    async def create(self, **kwargs: Any) -> FraudAlert: ...

    async def review(
        self,
        alert_id: str,
        *,
        status: AlertStatus,
        reviewer: ReviewerInfo,
        notes: str | None = None,
    ) -> FraudAlert | None: ...

    async def get_pending_count(self) -> int: ...

    async def get_stats(self) -> dict[str, Any]: ...

    async def get_daily_volume(self, days: int = 7) -> list[dict[str, Any]]: ...


class ConfigRepositoryProtocol(Protocol):
    """Interface for system configuration data access."""

    async def get(self, key: str) -> str | None: ...

    async def get_int(self, key: str, default: int = 0) -> int: ...

    async def get_all(self) -> dict[str, str]: ...

    async def upsert(
        self, key: str, value: str, description: str | None = None
    ) -> SystemConfig: ...


class TransactionRepositoryProtocol(Protocol):
    """Interface for transaction data access."""

    async def get_by_external_id(self, external_id: str) -> Transaction | None: ...

    async def get_by_id(self, transaction_id: str) -> Transaction | None: ...

    async def create(self, txn_data: TransactionEvaluateRequest) -> Transaction: ...

    async def upsert(self, txn_data: TransactionEvaluateRequest) -> Transaction: ...
