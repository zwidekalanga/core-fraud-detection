"""Celery application configuration."""
from celery import Celery
from celery.schedules import crontab

from app.config import get_settings

settings = get_settings()

# Create Celery app
celery_app = Celery(
    "fraud_engine",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

# Configure Celery
celery_app.conf.update(
    # Task settings
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Task execution settings
    task_acks_late=True,  # Ack after task completes
    task_reject_on_worker_lost=True,
    task_track_started=True,
    # Result backend settings
    result_expires=3600,  # Results expire after 1 hour
    # Worker settings
    worker_prefetch_multiplier=1,  # One task at a time
    worker_concurrency=4,
    # Task routes
    task_routes={
        "app.tasks.notifications.*": {"queue": "notifications"},
        "app.tasks.maintenance.*": {"queue": "maintenance"},
        "app.tasks.features.*": {"queue": "features"},
    },
    # Beat schedule (for periodic tasks)
    beat_schedule={
        "cleanup-old-notifications": {
            "task": "app.tasks.maintenance.cleanup_old_notifications",
            "schedule": 3600.0,  # Every hour
        },
        "refresh-customer-features": {
            "task": "app.tasks.features.refresh_expired_features",
            "schedule": 300.0,  # Every 5 minutes
        },
        "alert-summary-report": {
            "task": "app.tasks.maintenance.send_daily_summary",
            "schedule": crontab(hour=8, minute=0),  # Daily at 8am UTC
        },
    },
)

# Auto-discover tasks from task modules
celery_app.autodiscover_tasks([
    "app.tasks.notifications",
    "app.tasks.maintenance",
    "app.tasks.features",
])
