"""Fraud alert database model."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin


class AlertStatus(StrEnum):
    """Alert review status."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    DISMISSED = "dismissed"
    ESCALATED = "escalated"


class Decision(StrEnum):
    """Fraud detection decision."""

    APPROVE = "approve"
    REVIEW = "review"
    FLAG = "flag"


class FraudAlert(Base, UUIDMixin, TimestampMixin):
    """Fraud alert model."""

    __tablename__ = "fraud_alerts"

    # Reference to transaction
    transaction_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("transactions.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Human-readable reference number (e.g. FRD-00142)
    reference_number: Mapped[str | None] = mapped_column(
        String(20), unique=True, nullable=True, index=True
    )

    # Customer reference (denormalized for faster queries)
    customer_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)

    # Risk assessment results
    risk_score: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[str] = mapped_column(
        SAEnum(
            Decision,
            name="decision",
            create_constraint=True,
            native_enum=False,
            values_callable=lambda e: [member.value for member in e],
        ),
        nullable=False,
    )
    decision_tier: Mapped[str | None] = mapped_column(String(50), nullable=True)
    decision_tier_description: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Which rules triggered
    triggered_rules: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, nullable=False
    )

    # Processing metrics
    processing_time_ms: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Review status
    status: Mapped[str] = mapped_column(
        SAEnum(
            AlertStatus,
            name="alert_status",
            create_constraint=True,
            native_enum=False,
            values_callable=lambda e: [member.value for member in e],
        ),
        default=AlertStatus.PENDING.value,
        nullable=False,
    )

    # Review details
    reviewed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reviewed_by_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    transaction = relationship("Transaction", lazy="selectin")

    __table_args__ = (
        Index("idx_alert_status_created", "status", "created_at"),
        Index("idx_alert_score", "risk_score", postgresql_where="status = 'pending'"),
        Index("idx_alert_customer_status_created", "customer_id", "status", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<FraudAlert {self.id}: score={self.risk_score} status={self.status}>"
