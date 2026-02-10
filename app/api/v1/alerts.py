"""Alerts API endpoints.

Security note — tenant scoping
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
All authenticated users (admin, analyst, viewer) can access any alert
regardless of which customer or organisation it belongs to.  This is
intentional: the Sentinel Fraud Ops Portal is an **internal back-office
tool** operated exclusively by Capitec staff who require full visibility
across all alerts to triage, investigate, and resolve fraud cases.
Row-level tenant filtering is therefore not applied.

If this service is ever exposed to external consumers or multi-tenant
access, alerts MUST be scoped by organisation / tenant ID.
"""

from math import ceil
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi_filter import FilterDepends

from app.auth.dependencies import CurrentUser, require_role
from app.dependencies import DBSession
from app.filters.alert import AlertFilter
from app.repositories.alert_repository import AlertRepository
from app.schemas.alert import (
    AlertListResponse,
    AlertResponse,
    AlertReviewRequest,
    AlertReviewResponse,
    AlertStatsResponse,
    DailyVolumeEntry,
    DailyVolumeResponse,
    validate_status_transition,
)
from app.utils.audit import audit_logged

router = APIRouter()


def _build_alert_response(alert) -> AlertResponse:
    """Build alert response from an ORM model using ``from_attributes``."""
    return AlertResponse.model_validate(alert)


@router.get(
    "",
    response_model=AlertListResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(require_role("admin", "analyst", "viewer"))],
)
async def list_alerts(
    db: DBSession,
    filters: AlertFilter = FilterDepends(AlertFilter),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=100),
) -> AlertListResponse:
    """
    List fraud alerts with optional filtering and sorting.

    - **status**: Filter by review status (pending, confirmed, dismissed, escalated)
    - **customer_id**: Filter by customer
    - **risk_score__gte / risk_score__lte**: Filter by risk score range
    - **decision**: Filter by detection decision (approve, review, flag)
    - **created_at__gte / created_at__lte**: Filter by creation date range
    - **order_by**: Sort fields (e.g. ``-created_at``, ``risk_score``)
    """
    repo = AlertRepository(db)
    alerts, total = await repo.get_all(filters, page=page, size=size)

    return AlertListResponse(
        items=[_build_alert_response(a) for a in alerts],
        total=total,
        page=page,
        size=size,
        pages=ceil(total / size) if size > 0 else 0,
    )


@router.get(
    "/stats/summary",
    response_model=AlertStatsResponse,
    dependencies=[Depends(require_role("admin", "analyst", "viewer"))],
)
async def get_alert_stats(db: DBSession, response: Response) -> AlertStatsResponse:
    """Get alert statistics summary."""
    response.headers["Cache-Control"] = "private, max-age=30"
    repo = AlertRepository(db)
    stats = await repo.get_stats()
    return AlertStatsResponse(**stats)


@router.get(
    "/stats/daily-volume",
    response_model=DailyVolumeResponse,
    dependencies=[Depends(require_role("admin", "analyst", "viewer"))],
)
async def get_daily_volume(
    db: DBSession,
    response: Response,
    days: int = Query(7, ge=1, le=90),
) -> DailyVolumeResponse:
    """Get alert counts per day for the last N days."""
    response.headers["Cache-Control"] = "private, max-age=30"
    repo = AlertRepository(db)
    rows = await repo.get_daily_volume(days=days)
    return DailyVolumeResponse(
        items=[DailyVolumeEntry(**row) for row in rows],
        days=days,
    )


@router.get(
    "/{alert_id}",
    response_model=AlertResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(require_role("admin", "analyst", "viewer"))],
)
async def get_alert(
    alert_id: UUID,
    db: DBSession,
) -> AlertResponse:
    """Get detailed information about a specific alert."""
    repo = AlertRepository(db)
    alert = await repo.get_by_id(str(alert_id))

    if not alert:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Alert with ID '{alert_id}' not found",
        )

    return _build_alert_response(alert)


@router.post(
    "/{alert_id}/review",
    response_model=AlertReviewResponse,
    dependencies=[Depends(audit_logged("review_alert"))],
)
async def review_alert(
    alert_id: UUID,
    review_data: AlertReviewRequest,
    current_user: CurrentUser,
    db: DBSession,
    _: None = Depends(require_role("admin", "analyst")),
) -> AlertReviewResponse:
    """
    Review and update an alert's status.

    Possible status values:
    - **confirmed**: Verified as actual fraud
    - **dismissed**: False positive
    - **escalated**: Needs further investigation
    """
    repo = AlertRepository(db)
    alert_id_str = str(alert_id)

    # Get current alert to verify it exists
    existing = await repo.get_by_id(alert_id_str)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Alert with ID '{alert_id_str}' not found",
        )

    # M36: Validate status transition
    try:
        validate_status_transition(existing.status, review_data.status.value)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )

    # M46: Use immutable user ID instead of mutable username
    reviewed_by = current_user.id

    alert = await repo.review(
        alert_id_str,
        status=review_data.status,
        reviewed_by=reviewed_by,
        notes=review_data.notes,
    )

    if not alert:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update alert",
        )

    return AlertReviewResponse(
        id=alert.id,
        status=alert.status,
        reviewed_by=alert.reviewed_by,  # type: ignore
        reviewed_at=alert.reviewed_at,  # type: ignore
        review_notes=alert.review_notes,
    )
