"""Celery application configuration."""

from celery import Celery

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
    task_time_limit=300,  # Hard kill after 5 minutes
    task_soft_time_limit=240,  # SoftTimeLimitExceeded after 4 minutes
    # Result backend settings
    result_expires=3600,  # Results expire after 1 hour
    # Worker settings
    worker_prefetch_multiplier=1,  # One task at a time
    worker_concurrency=4,
    # Task routes
    task_routes={},
)

# Auto-discover tasks from task modules within the app package.
# Add module paths here as Celery tasks are implemented,
# e.g. ["app.tasks.alerts", "app.tasks.reports"].
celery_app.autodiscover_tasks(["app.tasks"])
