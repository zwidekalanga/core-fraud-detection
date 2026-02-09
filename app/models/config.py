"""SystemConfig model for persistent key-value configuration."""

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class SystemConfig(Base, TimestampMixin):
    """Key-value configuration table for system settings."""

    __tablename__ = "system_config"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)


CONFIG_DEFAULTS: dict[str, str] = {
    "auto_escalation_threshold": "90",
    "data_retention_days": "90",
}
