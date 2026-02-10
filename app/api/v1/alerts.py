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

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi_filter import FilterDepends

from app.auth.dependencies import CurrentUser, require_role
from app.filters.alert import AlertFilter
from app.providers import AlertSvc
from app.rate_limit import limiter
from app.schemas.alert import (
    AlertListResponse,
    AlertResponse,
    AlertReviewRequest,
    AlertReviewResponse,
    AlertStatsResponse,
    DailyVolumeResponse,
)
from app.utils.audit import audit_logged

router = APIRouter()


@router.get(
    "",
    response_model=AlertListResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(require_role("admin", "analyst", "viewer"))],
)
async def list_alerts(
    service: AlertSvc,
    filters: AlertFilter = FilterDepends(AlertFilter),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=100),
    account_number: str | None = Query(None, description="Filter by account number"),
) -> AlertListResponse:
    """
    List fraud alerts with optional filtering and sorting.

    - **status**: Filter by review status (pending, confirmed, dismissed, escalated)
    - **customer_id**: Filter by customer
    - **account_number**: Filter by account number (on the related transaction)
    - **risk_score__gte / risk_score__lte**: Filter by risk score range
    - **decision**: Filter by detection decision (approve, review, flag)
    - **created_at__gte / created_at__lte**: Filter by creation date range
    - **order_by**: Sort fields (e.g. ``-created_at``, ``risk_score``)
    """
    return await service.list_alerts(filters, page=page, size=size, account_number=account_number)


@router.get(
    "/stats/summary",
    response_model=AlertStatsResponse,
    dependencies=[Depends(require_role("admin", "analyst", "viewer"))],
)
async def get_alert_stats(service: AlertSvc, response: Response) -> AlertStatsResponse:
    """Get alert statistics summary."""
    response.headers["Cache-Control"] = "private, max-age=30"
    return await service.get_stats()


@router.get(
    "/stats/daily-volume",
    response_model=DailyVolumeResponse,
    dependencies=[Depends(require_role("admin", "analyst", "viewer"))],
)
async def get_daily_volume(
    service: AlertSvc,
    response: Response,
    days: int = Query(7, ge=1, le=90),
) -> DailyVolumeResponse:
    """Get alert counts per day for the last N days."""
    response.headers["Cache-Control"] = "private, max-age=30"
    return await service.get_daily_volume(days=days)


@router.get(
    "/{alert_id}",
    response_model=AlertResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(require_role("admin", "analyst", "viewer"))],
)
async def get_alert(
    alert_id: UUID,
    service: AlertSvc,
) -> AlertResponse:
    """Get detailed information about a specific alert."""
    result = await service.get_alert(str(alert_id))
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Alert with ID '{alert_id}' not found",
        )
    return result


@router.post(
    "/{alert_id}/review",
    response_model=AlertReviewResponse,
    dependencies=[Depends(audit_logged("review_alert"))],
)
@limiter.limit("30/minute")
async def review_alert(
    request: Request,
    alert_id: UUID,
    review_data: AlertReviewRequest,
    current_user: CurrentUser,
    service: AlertSvc,
    _: None = Depends(require_role("admin", "analyst")),
) -> AlertReviewResponse:
    """
    Review and update an alert's status.

    Possible status values:
    - **confirmed**: Verified as actual fraud
    - **dismissed**: False positive
    - **escalated**: Needs further investigation
    """
    try:
        result = await service.review_alert(str(alert_id), review_data, current_user)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Alert with ID '{alert_id}' not found",
        )
    return result
