"""Repository for fraud alert data access."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.types import Date

from app.models.alert import AlertStatus, Decision, FraudAlert


class AlertRepository:
    """Data access layer for fraud alerts."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_all(
        self,
        *,
        status: AlertStatus | None = None,
        customer_id: str | None = None,
        min_score: int | None = None,
        max_score: int | None = None,
        decision: Decision | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[FraudAlert], int]:
        """Get alerts with filtering and pagination."""
        query = select(FraudAlert).options(selectinload(FraudAlert.transaction))
        count_query = select(func.count()).select_from(FraudAlert)

        # Apply filters
        if status:
            query = query.where(FraudAlert.status == status.value)
            count_query = count_query.where(FraudAlert.status == status.value)

        if customer_id:
            query = query.where(FraudAlert.customer_id == customer_id)
            count_query = count_query.where(FraudAlert.customer_id == customer_id)

        if min_score is not None:
            query = query.where(FraudAlert.risk_score >= min_score)
            count_query = count_query.where(FraudAlert.risk_score >= min_score)

        if max_score is not None:
            query = query.where(FraudAlert.risk_score <= max_score)
            count_query = count_query.where(FraudAlert.risk_score <= max_score)

        if decision:
            query = query.where(FraudAlert.decision == decision.value)
            count_query = count_query.where(FraudAlert.decision == decision.value)

        if from_date:
            query = query.where(FraudAlert.created_at >= from_date)
            count_query = count_query.where(FraudAlert.created_at >= from_date)

        if to_date:
            query = query.where(FraudAlert.created_at <= to_date)
            count_query = count_query.where(FraudAlert.created_at <= to_date)

        # Get total count
        total = await self.session.scalar(count_query) or 0

        # Apply pagination and ordering
        query = query.order_by(FraudAlert.created_at.desc())
        query = query.offset((page - 1) * size).limit(size)

        result = await self.session.execute(query)
        alerts = list(result.scalars().all())

        return alerts, total

    async def get_by_id(self, alert_id: str) -> FraudAlert | None:
        """Get an alert by ID with transaction details."""
        query = (
            select(FraudAlert)
            .options(selectinload(FraudAlert.transaction))
            .where(FraudAlert.id == alert_id)
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

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
        alert = FraudAlert(
            transaction_id=transaction_id,
            customer_id=customer_id,
            risk_score=risk_score,
            decision=decision.value,
            decision_tier=decision_tier,
            decision_tier_description=decision_tier_description,
            triggered_rules=triggered_rules,
            processing_time_ms=processing_time_ms,
            status=AlertStatus.PENDING.value,
        )
        self.session.add(alert)
        await self.session.commit()
        await self.session.refresh(alert)
        return alert

    async def review(
        self,
        alert_id: str,
        *,
        status: AlertStatus,
        reviewed_by: str,
        notes: str | None = None,
    ) -> FraudAlert | None:
        """Review and update an alert's status."""
        alert = await self.get_by_id(alert_id)
        if not alert:
            return None

        alert.status = status.value
        alert.reviewed_by = reviewed_by
        alert.reviewed_at = datetime.now(UTC)
        alert.review_notes = notes

        await self.session.commit()
        await self.session.refresh(alert)
        return alert

    async def get_pending_count(self) -> int:
        """Get count of pending alerts."""
        query = (
            select(func.count())
            .select_from(FraudAlert)
            .where(FraudAlert.status == AlertStatus.PENDING.value)
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
