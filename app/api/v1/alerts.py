"""Alerts API endpoints."""

from datetime import datetime
from math import ceil

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.dependencies import CurrentUser, require_role
from app.dependencies import DBSession
from app.models.alert import AlertStatus, Decision
from app.repositories.alert_repository import AlertRepository
from app.schemas.alert import (
    AlertListResponse,
    AlertResponse,
    AlertReviewRequest,
    AlertReviewResponse,
    AlertTransactionInfo,
    TriggeredRuleDetail,
)

router = APIRouter()


def _build_alert_response(alert) -> AlertResponse:
    """Build alert response with embedded transaction info."""
    txn_info = None
    if alert.transaction:
        txn_info = AlertTransactionInfo(
            external_id=alert.transaction.external_id,
            amount=alert.transaction.amount,
            currency=alert.transaction.currency,
            transaction_type=alert.transaction.transaction_type,
            channel=alert.transaction.channel,
            merchant_name=alert.transaction.merchant_name,
            location_country=alert.transaction.location_country,
            transaction_time=alert.transaction.transaction_time,
        )

    return AlertResponse(
        id=alert.id,
        transaction_id=alert.transaction_id,
        customer_id=alert.customer_id,
        risk_score=alert.risk_score,
        decision=alert.decision,
        decision_tier=alert.decision_tier,
        decision_tier_description=alert.decision_tier_description,
        status=alert.status,
        triggered_rules=[TriggeredRuleDetail(**r) for r in alert.triggered_rules],
        processing_time_ms=alert.processing_time_ms,
        reviewed_by=alert.reviewed_by,
        reviewed_at=alert.reviewed_at,
        review_notes=alert.review_notes,
        created_at=alert.created_at,
        updated_at=alert.updated_at,
        transaction=txn_info,
    )


@router.get(
    "",
    response_model=AlertListResponse,
    dependencies=[Depends(require_role("admin", "analyst", "viewer"))],
)
async def list_alerts(
    db: DBSession,
    status: AlertStatus | None = None,
    customer_id: str | None = None,
    min_score: int | None = Query(None, ge=0, le=100),
    max_score: int | None = Query(None, ge=0, le=100),
    decision: Decision | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=100),
) -> AlertListResponse:
    """
    List fraud alerts with optional filtering.

    - **status**: Filter by review status (pending, confirmed, dismissed, escalated)
    - **customer_id**: Filter by customer
    - **min_score / max_score**: Filter by risk score range
    - **decision**: Filter by detection decision (approve, review, flag)
    - **from_date / to_date**: Filter by creation date range
    """
    repo = AlertRepository(db)
    alerts, total = await repo.get_all(
        status=status,
        customer_id=customer_id,
        min_score=min_score,
        max_score=max_score,
        decision=decision,
        from_date=from_date,
        to_date=to_date,
        page=page,
        size=size,
    )

    return AlertListResponse(
        items=[_build_alert_response(a) for a in alerts],
        total=total,
        page=page,
        size=size,
        pages=ceil(total / size) if size > 0 else 0,
    )


@router.get(
    "/stats/summary",
    dependencies=[Depends(require_role("admin", "analyst", "viewer"))],
)
async def get_alert_stats(db: DBSession) -> dict:
    """Get alert statistics summary."""
    repo = AlertRepository(db)
    return await repo.get_stats()


@router.get(
    "/stats/daily-volume",
    dependencies=[Depends(require_role("admin", "analyst", "viewer"))],
)
async def get_daily_volume(
    db: DBSession,
    days: int = Query(7, ge=1, le=90),
) -> list[dict]:
    """Get alert counts per day for the last N days."""
    repo = AlertRepository(db)
    return await repo.get_daily_volume(days=days)


@router.get(
    "/{alert_id}",
    response_model=AlertResponse,
    dependencies=[Depends(require_role("admin", "analyst", "viewer"))],
)
async def get_alert(
    alert_id: str,
    db: DBSession,
) -> AlertResponse:
    """Get detailed information about a specific alert."""
    repo = AlertRepository(db)
    alert = await repo.get_by_id(alert_id)

    if not alert:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Alert with ID '{alert_id}' not found",
        )

    return _build_alert_response(alert)


@router.post("/{alert_id}/review", response_model=AlertReviewResponse)
async def review_alert(
    alert_id: str,
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

    # Get current alert to verify it exists
    existing = await repo.get_by_id(alert_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Alert with ID '{alert_id}' not found",
        )

    # Use the authenticated user's email as reviewer
    reviewed_by = current_user.username

    alert = await repo.review(
        alert_id,
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
