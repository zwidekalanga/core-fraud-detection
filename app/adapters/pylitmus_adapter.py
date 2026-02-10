"""Adapter that isolates pylitmus types from the domain layer.

Centralises all conversions between our ORM/schema models and the pylitmus
rules engine, so the rest of the application never touches pylitmus types
directly.
"""

from __future__ import annotations

import logging
from typing import Any

from pylitmus.types import Rule as PylitmusRule
from pylitmus.types import Severity as PylitmusSeverity

from app.models.alert import Decision
from app.models.rule import FraudRule
from app.schemas.transaction import TransactionEvaluateRequest

logger = logging.getLogger(__name__)

# Mapping from domain severity strings to pylitmus enum values.
SEVERITY_MAP: dict[str, PylitmusSeverity] = {
    "low": PylitmusSeverity.LOW,
    "medium": PylitmusSeverity.MEDIUM,
    "high": PylitmusSeverity.HIGH,
    "critical": PylitmusSeverity.CRITICAL,
}


class PylitmusAdapter:
    """Adapts domain models to/from pylitmus engine types."""

    @staticmethod
    def to_pylitmus_rule(rule: FraudRule) -> PylitmusRule:
        """Convert an ORM ``FraudRule`` to a pylitmus ``Rule``."""
        severity = SEVERITY_MAP.get(rule.severity)
        if severity is None:
            logger.warning(
                "Unknown severity '%s' for rule %s, defaulting to MEDIUM",
                rule.severity,
                rule.code,
            )
            severity = PylitmusSeverity.MEDIUM

        return PylitmusRule(
            code=rule.code,
            name=rule.name,
            description=rule.description or "",
            category=rule.category,
            severity=severity,
            score=rule.score,
            enabled=rule.enabled,
            conditions=rule.conditions,
            version=1,
            effective_from=rule.effective_from,
            effective_to=rule.effective_to,
            metadata={},
        )

    @staticmethod
    def to_eval_data(request: TransactionEvaluateRequest) -> dict[str, Any]:
        """Convert a ``TransactionEvaluateRequest`` to the dict consumed by pylitmus."""
        return {
            "amount": float(request.amount),
            "currency": request.currency,
            "transaction_type": request.transaction_type,
            "channel": request.channel,
            "merchant_id": request.merchant_id,
            "merchant_name": request.merchant_name,
            "merchant_category": request.merchant_category,
            "location_country": request.location_country,
            "location_city": request.location_city,
            "device_fingerprint": request.device_fingerprint,
            "ip_address": request.ip_address,
            "customer_id": request.customer_id,
        }

    @staticmethod
    def to_decision(assessment_decision: str | None) -> Decision:
        """Convert a pylitmus decision tier name to a domain ``Decision`` enum."""
        return Decision((assessment_decision or "approve").lower())

    @staticmethod
    def to_triggered_rules(triggered_rules: list[Any]) -> list[dict[str, Any]]:
        """Convert pylitmus triggered rule objects to serialisable dicts."""
        return [
            {
                "code": r.rule_code,
                "name": r.rule_name,
                "category": r.category,
                "severity": r.severity.value,
                "score": r.score,
                "description": r.explanation,
            }
            for r in triggered_rules
        ]
