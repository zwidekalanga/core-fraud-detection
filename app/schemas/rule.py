"""Pydantic schemas for fraud rules."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.constants import RULE_CODE_PATTERN
from app.models.rule import RuleCategory, Severity


class RuleCondition(BaseModel):
    """Rule condition schema (pylitmus format)."""

    field: str | None = None
    operator: str | None = None
    value: Any = None
    all: list["RuleCondition"] | None = None
    any: list["RuleCondition"] | None = None

    @field_validator("operator")
    @classmethod
    def validate_operator(cls, v: str | None) -> str | None:
        """Validate operator is supported by pylitmus."""
        valid_operators = {
            "equals",
            "not_equals",
            "greater_than",
            "greater_than_or_equal",
            "less_than",
            "less_than_or_equal",
            "between",
            "in",
            "not_in",
            "contains",
            "starts_with",
            "ends_with",
            "matches_regex",
            "is_null",
            "is_not_null",
            "within_days",
            "before",
            "after",
        }
        if v is not None and v not in valid_operators:
            raise ValueError(f"Invalid operator: {v}. Must be one of {valid_operators}")
        return v


def _check_effective_dates(effective_from: datetime | None, effective_to: datetime | None) -> None:
    """Validate effective_to is after effective_from when both are set."""
    if effective_from and effective_to and effective_to <= effective_from:
        raise ValueError("effective_to must be after effective_from")


class RuleCreate(BaseModel):
    """Schema for creating a new rule."""

    code: str = Field(..., min_length=1, max_length=50, pattern=RULE_CODE_PATTERN)
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    category: RuleCategory
    severity: Severity
    score: int = Field(..., ge=0, le=100)
    enabled: bool = True
    conditions: dict[str, Any]
    effective_from: datetime | None = None
    effective_to: datetime | None = None

    @model_validator(mode="after")
    def validate_effective_dates(self) -> "RuleCreate":
        """Ensure effective_to is after effective_from when both are set."""
        _check_effective_dates(self.effective_from, self.effective_to)
        return self


class RuleUpdate(BaseModel):
    """Schema for updating a rule."""

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    category: RuleCategory | None = None
    severity: Severity | None = None
    score: int | None = Field(None, ge=0, le=100)
    enabled: bool | None = None
    conditions: dict[str, Any] | None = None
    effective_from: datetime | None = None
    effective_to: datetime | None = None

    @model_validator(mode="after")
    def validate_effective_dates(self) -> "RuleUpdate":
        """Ensure effective_to is after effective_from when both are set."""
        _check_effective_dates(self.effective_from, self.effective_to)
        return self


class RuleResponse(BaseModel):
    """Schema for rule response."""

    code: str
    name: str
    description: str | None
    category: str
    severity: str
    score: int
    enabled: bool
    conditions: dict[str, Any]
    effective_from: datetime | None
    effective_to: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RuleListResponse(BaseModel):
    """Schema for paginated rule list."""

    items: list[RuleResponse]
    total: int
    page: int
    size: int
    pages: int
