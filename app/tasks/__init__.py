"""Celery tasks for the Fraud Detection Service."""
from app.tasks.celery_app import celery_app

__all__ = [
    "celery_app",
]
