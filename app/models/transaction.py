"""Transaction database model."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Index, Numeric, String
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class Channel(StrEnum):
    """Transaction channels."""

    CARD = "card"
    EFT = "eft"
    MOBILE = "mobile"
    ONLINE = "online"
    ATM = "atm"
    BRANCH = "branch"
    QR = "qr"
    POS = "pos"


class Transaction(Base, UUIDMixin, TimestampMixin):
    """Transaction model for storing processed transactions."""

    __tablename__ = "transactions"

    # External reference (from source system)
    external_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)

    # Customer reference
    customer_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)

    # Transaction details
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="ZAR", nullable=False)
    transaction_type: Mapped[str] = mapped_column(String(50), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)

    # Merchant info (optional - for card transactions)
    merchant_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    merchant_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    merchant_category: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Location info
    location_country: Mapped[str | None] = mapped_column(String(3), nullable=True)
    location_city: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Device info
    device_fingerprint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)

    # Transaction timestamp (when it occurred, not when we processed it)
    transaction_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Additional data
    extra_data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_txn_amount_positive"),
        Index("idx_txn_customer_time", "customer_id", "transaction_time"),
        Index("idx_txn_merchant_time", "merchant_id", "transaction_time"),
    )

    def __repr__(self) -> str:
        return f"<Transaction {self.external_id}: {self.amount} {self.currency}>"
