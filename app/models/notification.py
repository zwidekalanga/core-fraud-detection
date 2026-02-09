"""Notification database model."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin


class NotificationType(StrEnum):
    """Notification channel types."""

    EMAIL = "email"
    SMS = "sms"
    SLACK = "slack"
    WEBHOOK = "webhook"


class NotificationStatus(StrEnum):
    """Notification delivery status."""

    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class Notification(Base, UUIDMixin, TimestampMixin):
    """Notification tracking model."""

    __tablename__ = "notifications"

    # Reference to alert
    alert_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("fraud_alerts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Notification details
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    recipient: Mapped[str] = mapped_column(String(255), nullable=False)

    # Delivery status
    status: Mapped[str] = mapped_column(
        String(20), default=NotificationStatus.PENDING.value, nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    alert = relationship("FraudAlert", backref="notifications")

    def __repr__(self) -> str:
        return f"<Notification {self.id}: {self.type} -> {self.recipient}>"
