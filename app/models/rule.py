"""Fraud rule database model."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class RuleCategory(StrEnum):
    """Fraud rule categories."""

    VELOCITY = "velocity"
    AMOUNT = "amount"
    GEOGRAPHIC = "geographic"
    BEHAVIORAL = "behavioral"
    DEVICE = "device"
    COMBINED = "combined"


class Severity(StrEnum):
    """Rule severity levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FraudRule(Base, TimestampMixin):
    """Fraud detection rule model."""

    __tablename__ = "fraud_rules"

    # Primary key is the rule code (e.g., "AMT_001")
    code: Mapped[str] = mapped_column(String(50), primary_key=True)

    # Rule metadata
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(
        SAEnum(
            RuleCategory,
            name="rule_category",
            create_constraint=True,
            native_enum=False,
            values_callable=lambda e: [member.value for member in e],
        ),
        nullable=False,
    )
    severity: Mapped[str] = mapped_column(
        SAEnum(
            Severity,
            name="severity",
            create_constraint=True,
            native_enum=False,
            values_callable=lambda e: [member.value for member in e],
        ),
        nullable=False,
    )

    # Scoring
    score: Mapped[int] = mapped_column(Integer, nullable=False)

    # State
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Rule logic (pylitmus condition format)
    conditions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    # Temporal bounds (optional)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (CheckConstraint("score >= 0 AND score <= 100", name="ck_rule_score_range"),)

    def __repr__(self) -> str:
        return f"<FraudRule {self.code}: {self.name}>"
