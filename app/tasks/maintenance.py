"""Celery tasks for maintenance operations."""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select

from app.models.alert import AlertStatus, FraudAlert
from app.models.notification import Notification, NotificationStatus
from app.repositories.config_repository import SyncConfigRepository
from app.tasks.celery_app import celery_app
from app.tasks.notifications import get_sync_session

logger = logging.getLogger(__name__)


@celery_app.task
def cleanup_old_notifications() -> dict[str, int]:
    """
    Clean up old notification records.

    Removes sent notifications older than 30 days.
    Keeps failed notifications for debugging.
    """
    logger.info("Starting notification cleanup...")

    session = get_sync_session()

    try:
        cutoff = datetime.now(UTC) - timedelta(days=30)

        # Delete old sent notifications
        result = session.execute(
            delete(Notification).where(
                Notification.status == NotificationStatus.SENT.value,
                Notification.sent_at < cutoff,
            )
        )
        deleted_count = result.rowcount  # pyright: ignore[reportAttributeAccessIssue]
        session.commit()

        logger.info(f"Deleted {deleted_count} old notifications")
        return {"deleted": deleted_count}

    finally:
        session.close()


@celery_app.task
def send_daily_summary() -> dict[str, object]:
    """
    Send daily summary email of fraud alerts.

    Scheduled to run at 8am UTC.
    """
    logger.info("Generating daily summary...")

    session = get_sync_session()

    try:
        # Get yesterday's date range
        today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday = today - timedelta(days=1)

        # Count alerts by status
        status_counts = {}
        for status in AlertStatus:
            count = session.scalar(
                select(func.count())
                .select_from(FraudAlert)
                .where(
                    FraudAlert.created_at >= yesterday,
                    FraudAlert.created_at < today,
                    FraudAlert.status == status.value,
                )
            )
            status_counts[status.value] = count or 0

        total = sum(status_counts.values())

        # Get average score
        avg_score = (
            session.scalar(
                select(func.avg(FraudAlert.risk_score)).where(
                    FraudAlert.created_at >= yesterday,
                    FraudAlert.created_at < today,
                )
            )
            or 0
        )

        # Get highest score
        max_score = (
            session.scalar(
                select(func.max(FraudAlert.risk_score)).where(
                    FraudAlert.created_at >= yesterday,
                    FraudAlert.created_at < today,
                )
            )
            or 0
        )

        summary = {
            "date": yesterday.strftime("%Y-%m-%d"),
            "total_alerts": total,
            "by_status": status_counts,
            "average_score": round(float(avg_score), 1),
            "highest_score": max_score,
        }

        logger.info(f"Daily summary: {summary}")

        # TODO: Send summary email using a different template

        return summary

    finally:
        session.close()


@celery_app.task
def reprocess_failed_notifications() -> dict[str, int]:
    """
    Retry failed notifications.

    Finds notifications that failed and retries them.
    """
    logger.info("Checking for failed notifications to retry...")

    session = get_sync_session()

    try:
        # Find failed notifications from the last 24 hours
        cutoff = datetime.now(UTC) - timedelta(hours=24)

        failed = (
            session.execute(
                select(Notification).where(
                    Notification.status == NotificationStatus.FAILED.value,
                    Notification.created_at >= cutoff,
                )
            )
            .scalars()
            .all()
        )

        retried = 0
        for notification in failed:
            # Reset status and retry
            notification.status = NotificationStatus.PENDING.value
            notification.error_message = None
            retried += 1

        session.commit()

        logger.info(f"Reset {retried} failed notifications for retry")
        return {"retried": retried}

    finally:
        session.close()


@celery_app.task
def archive_old_alerts() -> dict[str, int]:
    """
    Archive old alerts that have been reviewed.

    Moves reviewed alerts older than 90 days to archived status.
    """
    logger.info("Archiving old alerts...")

    session = get_sync_session()

    try:
        config_repo = SyncConfigRepository(session)
        retention_days = config_repo.get_int("data_retention_days", 90)
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)

        # Find old reviewed alerts
        old_alerts = (
            session.execute(
                select(FraudAlert).where(
                    FraudAlert.reviewed_at.isnot(None),
                    FraudAlert.reviewed_at < cutoff,
                    FraudAlert.status != "archived",
                )
            )
            .scalars()
            .all()
        )

        archived = 0
        for alert in old_alerts:
            alert.status = "archived"
            archived += 1

        session.commit()

        logger.info(f"Archived {archived} old alerts")
        return {"archived": archived}

    finally:
        session.close()
