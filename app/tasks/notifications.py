"""Celery tasks for sending notifications."""

import logging
import smtplib
from datetime import UTC, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models.alert import FraudAlert
from app.models.notification import Notification, NotificationStatus, NotificationType
from app.models.transaction import Transaction
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def get_sync_session() -> Session:
    """Get synchronous database session for Celery tasks."""
    settings = get_settings()
    # Use sync engine for Celery (not async)
    sync_url = str(settings.database_url).replace("+asyncpg", "")
    engine = create_engine(sync_url)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def send_fraud_notification(self, alert_id: str) -> dict[str, object]:
    """
    Main notification task - orchestrates all notification channels.

    This task:
    1. Fetches alert details from database
    2. Triggers email notification
    3. Records notification in database
    4. Returns summary of what was sent

    Args:
        alert_id: UUID of the fraud alert

    Returns:
        Dict with notification results
    """
    logger.info(f"Processing notification for alert: {alert_id}")

    try:
        session = get_sync_session()

        # Fetch alert with transaction details
        alert = session.execute(
            select(FraudAlert).where(FraudAlert.id == alert_id)
        ).scalar_one_or_none()

        if not alert:
            logger.error(f"Alert not found: {alert_id}")
            return {"status": "error", "message": "Alert not found"}

        # Fetch transaction
        transaction = session.execute(
            select(Transaction).where(Transaction.id == alert.transaction_id)
        ).scalar_one_or_none()

        results = {"alert_id": alert_id, "notifications": []}

        # Send email notification
        email_result = send_email_alert.delay(  # pyright: ignore[reportFunctionMemberAccess]
            alert_id=alert_id,
            risk_score=alert.risk_score,
            decision=alert.decision,
            customer_id=alert.customer_id,
            amount=float(transaction.amount) if transaction else 0,
            triggered_rules=alert.triggered_rules,
        )
        results["notifications"].append({"type": "email", "task_id": email_result.id})  # type: ignore[union-attr]

        session.close()

        logger.info(f"Notification tasks queued for alert: {alert_id}")
        return results

    except Exception as e:
        logger.error(f"Failed to process notification: {e}")
        self.retry(exc=e)
        return {
            "status": "retrying",
            "alert_id": alert_id,
        }  # unreachable but satisfies type checker


@celery_app.task(bind=True, max_retries=3, default_retry_delay=30)
def send_email_alert(
    self,
    alert_id: str,
    risk_score: int,
    decision: str,
    customer_id: str,
    amount: float,
    triggered_rules: list[dict[str, object]],
) -> dict[str, object]:
    """
    Send email notification for a fraud alert.

    Uses SMTP to send email (MailHog in development, real SMTP in production).
    """
    settings = get_settings()
    session = get_sync_session()
    notification = None

    try:
        # Create notification record
        notification = Notification(
            alert_id=alert_id,
            type=NotificationType.EMAIL.value,
            recipient=settings.notification_email_to,
            status=NotificationStatus.PENDING.value,
        )
        session.add(notification)
        session.commit()
        session.refresh(notification)

        # Build email content
        subject = f"Fraud Alert: Score {risk_score} - {decision.upper()}"

        rules_html = "\n".join(
            [
                f"<li><strong>{r['code']}</strong>: {r['name']} (+{r['score']} points)</li>"
                for r in triggered_rules
            ]
        )

        body_html = f"""
        <html>
        <body>
            <h1>Fraud Alert Detected</h1>

            <table border="1" cellpadding="10" cellspacing="0">
                <tr><td><strong>Alert ID</strong></td><td>{alert_id}</td></tr>
                <tr><td><strong>Risk Score</strong></td><td>{risk_score}/100</td></tr>
                <tr><td><strong>Decision</strong></td><td>{decision.upper()}</td></tr>
                <tr><td><strong>Customer ID</strong></td><td>{customer_id[:8]}...</td></tr>
                <tr><td><strong>Amount</strong></td><td>R{amount:,.2f}</td></tr>
            </table>

            <h2>Triggered Rules:</h2>
            <ul>
                {rules_html}
            </ul>

            <p>
                <a href="http://localhost:3000/alerts/{alert_id}">
                    Review this alert in the dashboard
                </a>
            </p>

            <hr>
            <p style="color: gray; font-size: 12px;">
                This is an automated alert from the Fraud Detection Service.
                Generated at {datetime.now(UTC).isoformat()}
            </p>
        </body>
        </html>
        """

        rules_text = "\n".join(
            [f"- {r['code']}: {r['name']} (+{r['score']})" for r in triggered_rules]
        )

        body_text = f"""
FRAUD ALERT DETECTED

Alert ID: {alert_id}
Risk Score: {risk_score}/100
Decision: {decision.upper()}
Customer ID: {customer_id[:8]}...
Amount: R{amount:,.2f}

Triggered Rules:
{rules_text}

Review: http://localhost:3000/alerts/{alert_id}
        """

        # Create email message
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.notification_email_from
        msg["To"] = settings.notification_email_to

        msg.attach(MIMEText(body_text, "plain"))
        msg.attach(MIMEText(body_html, "html"))

        # Send email
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            if settings.smtp_user and settings.smtp_password:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)

        # Update notification status
        notification.status = NotificationStatus.SENT.value
        notification.sent_at = datetime.now(UTC)
        session.commit()

        logger.info(f"Email sent for alert {alert_id} to {settings.notification_email_to}")

        return {
            "status": "sent",
            "notification_id": notification.id,
            "recipient": settings.notification_email_to,
        }

    except smtplib.SMTPException as e:
        logger.error(f"SMTP error sending email: {e}")

        # Update notification with error
        if notification:
            notification.status = NotificationStatus.FAILED.value
            notification.error_message = str(e)
            session.commit()

        self.retry(exc=e)
        return {"status": "retrying"}  # unreachable but satisfies type checker

    except Exception as e:
        logger.error(f"Error sending email: {e}")
        if notification:
            notification.status = NotificationStatus.FAILED.value
            notification.error_message = str(e)
            session.commit()
        self.retry(exc=e)
        return {"status": "retrying"}  # unreachable but satisfies type checker

    finally:
        session.close()


@celery_app.task(bind=True, max_retries=3)
def send_slack_alert(self, alert_id: str) -> dict[str, str]:  # noqa: ARG001
    """
    Send Slack notification for critical fraud alerts.

    Uses Slack Webhook (would need SLACK_WEBHOOK_URL in settings).
    """
    logger.info(f"Slack notification for alert: {alert_id}")

    # TODO: Implement Slack webhook integration
    # import requests
    # settings = get_settings()
    # webhook_url = settings.slack_webhook_url
    # ...

    return {"status": "skipped", "message": "Slack not configured"}
