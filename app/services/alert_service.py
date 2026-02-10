"""Service layer for fraud alert management."""

from datetime import UTC, datetime
from math import ceil

from app.repositories.alert_repository import AlertRepository
from app.schemas.alert import (
    AlertListResponse,
    AlertResponse,
    AlertReviewRequest,
    AlertReviewResponse,
    AlertStatsResponse,
    DailyVolumeEntry,
    DailyVolumeResponse,
    ReviewerInfo,
)
from app.schemas.auth import TokenUser


class AlertService:
    """Version-agnostic business logic for fraud alerts."""

    def __init__(self, repo: AlertRepository):
        self._repo = repo

    @staticmethod
    def _build_response(alert) -> AlertResponse:
        return AlertResponse.model_validate(alert)

    async def list_alerts(
        self,
        filters,
        page: int = 1,
        size: int = 50,
        account_number: str | None = None,
    ) -> AlertListResponse:
        alerts, total = await self._repo.get_all(
            filters, page=page, size=size, account_number=account_number
        )
        return AlertListResponse(
            items=[self._build_response(a) for a in alerts],
            total=total,
            page=page,
            size=size,
            pages=ceil(total / size) if size > 0 else 0,
        )

    async def get_alert(self, alert_id: str) -> AlertResponse | None:
        alert = await self._repo.get_by_id(alert_id)
        if alert is None:
            return None
        return self._build_response(alert)

    async def get_stats(self) -> AlertStatsResponse:
        stats = await self._repo.get_stats()
        return AlertStatsResponse(**stats)

    async def get_daily_volume(self, days: int = 7) -> DailyVolumeResponse:
        rows = await self._repo.get_daily_volume(days=days)
        return DailyVolumeResponse(
            items=[DailyVolumeEntry(**row) for row in rows],
            days=days,
        )

    async def review_alert(
        self,
        alert_id: str,
        review_data: AlertReviewRequest,
        current_user: TokenUser,
    ) -> AlertReviewResponse | None:
        """Review and update an alert's status.

        Returns ``None`` when *alert_id* does not exist.

        Raises:
            ValueError: If the status transition is invalid.
        """
        existing = await self._repo.get_by_id(alert_id)
        if existing is None:
            return None

        # State transition validation is handled by AlertRepository.review()
        # via AlertStateMachine — single source of truth (see D-01).
        reviewer = ReviewerInfo(id=current_user.id, username=current_user.username)

        alert = await self._repo.review(
            alert_id,
            status=review_data.status,
            reviewer=reviewer,
            notes=review_data.notes,
        )

        if alert is None:
            return None

        return AlertReviewResponse(
            id=alert.id,
            reference_number=alert.reference_number,
            status=alert.status,
            reviewed_by=alert.reviewed_by or reviewer.id,
            reviewed_by_username=alert.reviewed_by_username or reviewer.username,
            reviewed_at=alert.reviewed_at or datetime.now(UTC),
            review_notes=alert.review_notes,
        )
