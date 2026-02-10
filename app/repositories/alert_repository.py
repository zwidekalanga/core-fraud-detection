"""Repository for fraud alert data access."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.types import Date

from app.core.alert_state import AlertStateMachine
from app.filters.alert import AlertFilter
from app.models.alert import AlertStatus, Decision, FraudAlert
from app.models.transaction import Transaction
from app.schemas.alert import ReviewerInfo


class AlertRepository:
    """Data access layer for fraud alerts."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_all(
        self,
        filters: AlertFilter,
        page: int = 1,
        size: int = 50,
        account_number: str | None = None,
    ) -> tuple[list[FraudAlert], int]:
        """Get alerts with declarative filtering, sorting, and pagination."""
        query = filters.filter(select(FraudAlert).options(selectinload(FraudAlert.transaction)))
        count_query = filters.filter(select(func.count()).select_from(FraudAlert))

        if account_number:
            query = query.join(Transaction, FraudAlert.transaction_id == Transaction.id).where(
                Transaction.account_number == account_number
            )
            count_query = count_query.join(
                Transaction, FraudAlert.transaction_id == Transaction.id
            ).where(Transaction.account_number == account_number)

        total = await self.session.scalar(count_query) or 0

        query = filters.sort(query)
        query = query.offset((page - 1) * size).limit(size)

        result = await self.session.execute(query)
        return list(result.scalars().all()), total

    async def get_by_id(self, alert_id: str) -> FraudAlert | None:
        """Get an alert by ID with transaction details."""
        query = (
            select(FraudAlert)
            .options(selectinload(FraudAlert.transaction))
            .where(FraudAlert.id == alert_id)
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def _next_reference_number(self) -> str:
        """Generate the next human-readable alert reference number.

        Uses a PostgreSQL sequence for atomic, gap-free incrementing.
        Falls back to a count-based approach if the sequence doesn't exist yet.
        """
        try:
            result = await self.session.execute(text("SELECT nextval('alert_ref_seq')"))
            seq_val = result.scalar()
        except Exception:
            # Fallback if sequence not yet created (pre-migration)
            result = await self.session.execute(select(func.count()).select_from(FraudAlert))
            seq_val = (result.scalar() or 0) + 1
        return f"FRD-{seq_val:05d}"

    async def create(
        self,
        *,
        transaction_id: str,
        customer_id: str,
        risk_score: int,
        decision: Decision,
        decision_tier: str | None = None,
        decision_tier_description: str | None = None,
        triggered_rules: list[dict[str, Any]],
        processing_time_ms: float | None = None,
    ) -> FraudAlert:
        """Create a new fraud alert."""
        reference_number = await self._next_reference_number()

        alert = FraudAlert(
            transaction_id=transaction_id,
            reference_number=reference_number,
            customer_id=customer_id,
            risk_score=risk_score,
            decision=decision,
            decision_tier=decision_tier,
            decision_tier_description=decision_tier_description,
            triggered_rules=triggered_rules,
            processing_time_ms=processing_time_ms,
            status=AlertStatus.PENDING,
        )
        self.session.add(alert)
        await self.session.flush()
        await self.session.refresh(alert)
        return alert

    async def review(
        self,
        alert_id: str,
        *,
        status: AlertStatus,
        reviewer: ReviewerInfo,
        notes: str | None = None,
    ) -> FraudAlert | None:
        """Review and update an alert's status.

        Raises:
            ValueError: If the status transition is not allowed.
        """
        alert = await self.get_by_id(alert_id)
        if not alert:
            return None

        # Validate state transition via State pattern
        current_state = AlertStateMachine.get_state(alert.status)
        current_state.validate_transition(alert_id, status)

        alert.status = status
        alert.reviewed_by = reviewer.id
        alert.reviewed_by_username = reviewer.username
        alert.reviewed_at = datetime.now(UTC)
        alert.review_notes = notes

        await self.session.flush()
        await self.session.refresh(alert)

        # Fire state-entry side-effects (logging, notifications)
        new_state = AlertStateMachine.get_state(status)
        new_state.on_enter(alert)

        return alert

    async def get_pending_count(self) -> int:
        """Get count of pending alerts."""
        query = (
            select(func.count())
            .select_from(FraudAlert)
            .where(FraudAlert.status == AlertStatus.PENDING)
        )
        return await self.session.scalar(query) or 0

    async def get_stats(self) -> dict[str, Any]:
        """Get alert statistics."""
        # Total by status
        status_query = select(FraudAlert.status, func.count()).group_by(FraudAlert.status)
        status_result = await self.session.execute(status_query)
        by_status = {row[0]: row[1] for row in status_result.all()}

        # Average score
        avg_score_query = select(func.avg(FraudAlert.risk_score))
        avg_score = await self.session.scalar(avg_score_query) or 0

        return {
            "total": sum(by_status.values()),
            "by_status": by_status,
            "average_score": round(float(avg_score), 1),
        }

    async def get_daily_volume(self, days: int = 7) -> list[dict[str, Any]]:
        """Get alert counts grouped by day for the last N days."""
        cutoff = datetime.now(UTC) - timedelta(days=days)
        day_col = cast(FraudAlert.created_at, Date).label("day")

        query = (
            select(day_col, func.count().label("count"))
            .where(FraudAlert.created_at >= cutoff)
            .group_by(day_col)
            .order_by(day_col)
        )
        result = await self.session.execute(query)
        volume_by_day = {row.day: row.count for row in result.all()}

        # Fill gaps so every day in the range is represented
        today = datetime.now(UTC).date()
        return [
            {
                "date": str(today - timedelta(days=days - 1 - i)),
                "alerts": volume_by_day.get(today - timedelta(days=days - 1 - i), 0),
            }
            for i in range(days)
        ]
