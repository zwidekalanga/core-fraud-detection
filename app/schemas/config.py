"""Pydantic schemas for system configuration."""

from pydantic import BaseModel, Field


class ConfigResponse(BaseModel):
    """Response schema for system configuration."""

    auto_escalation_threshold: int
    data_retention_days: int


class ConfigUpdateRequest(BaseModel):
    """Request schema for updating system configuration (partial)."""

    auto_escalation_threshold: int | None = Field(None, ge=0, le=100)
    data_retention_days: int | None = Field(None, ge=1, le=3650)
