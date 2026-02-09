"""Fraud detection service using pylitmus rules engine.

Concurrency model: FraudDetector instances are NOT thread-safe. In the Kafka
consumer a single detector is kept warm and reused across sequential messages
(no concurrent access). The gRPC path creates a fresh detector per request.
If rule reloading is added at runtime, callers must serialise access via an
external ``asyncio.Lock``.
"""

import logging
from typing import Any

from pylitmus import DecisionTier, create_engine
from pylitmus.types import AssessmentResult
from pylitmus.types import Rule as PylitmusRule
from pylitmus.types import Severity as PylitmusSeverity

from app.config import Settings
from app.models.alert import Decision
from app.models.rule import FraudRule

logger = logging.getLogger(__name__)


class EvaluationError(Exception):
    """Raised when the pylitmus rules engine fails to evaluate a transaction."""


# Define decision tiers for fraud assessment
DECISION_TIERS = [
    DecisionTier("APPROVE", 0, 40, "Low risk - approve transaction"),
    DecisionTier("REVIEW", 40, 80, "Medium risk - requires manual review"),
    DecisionTier("FLAG", 80, 101, "High risk - flag for investigation"),
]


class FraudDetector:
    """
    Fraud detection service built on pylitmus rules engine.

    Provides real-time fraud scoring for financial transactions
    using configurable rules and decision tiers.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self._rules: list[PylitmusRule] = []
        self._engine = self._create_engine([])

    def _create_engine(self, rules: list[PylitmusRule]):
        """Create pylitmus engine with decision tiers and RETE algorithm."""
        return create_engine(
            rules=rules,
            decision_tiers=DECISION_TIERS,
            use_rete=True,
        )

    def load_rules(self, rules: list[FraudRule]) -> None:
        """Load rules from database into pylitmus engine."""
        self._rules = [self._convert_to_pylitmus_rule(rule) for rule in rules if rule.enabled]
        self._engine = self._create_engine(self._rules)

    def _convert_to_pylitmus_rule(self, rule: FraudRule) -> PylitmusRule:
        """Convert database rule to pylitmus Rule type."""
        # Map severity string to pylitmus Severity enum
        severity_map = {
            "low": PylitmusSeverity.LOW,
            "medium": PylitmusSeverity.MEDIUM,
            "high": PylitmusSeverity.HIGH,
            "critical": PylitmusSeverity.CRITICAL,
        }

        return PylitmusRule(
            code=rule.code,
            name=rule.name,
            description=rule.description or "",
            category=rule.category,
            severity=severity_map.get(rule.severity, PylitmusSeverity.MEDIUM),
            score=rule.score,
            enabled=rule.enabled,
            conditions=rule.conditions,
            version=1,
            effective_from=rule.effective_from,
            effective_to=rule.effective_to,
            metadata={},
        )

    def evaluate(self, data: dict[str, Any]) -> AssessmentResult:
        """
        Evaluate transaction data against all loaded rules.

        Args:
            data: Transaction data with enriched features

        Returns:
            AssessmentResult from pylitmus with score, decision tier, and triggered rules

        Raises:
            EvaluationError: If the rules engine fails unexpectedly.
        """
        try:
            return self._engine.evaluate(data)
        except Exception as exc:
            logger.exception(
                "Rules engine evaluation failed for external_id=%s",
                data.get("external_id", "unknown"),
            )
            raise EvaluationError(f"Failed to evaluate transaction: {exc}") from exc

    def get_decision(self, assessment: AssessmentResult) -> Decision:
        """Convert pylitmus decision tier to our Decision enum."""
        return Decision((assessment.decision or "approve").lower())

    def get_decision_tier(self, assessment: AssessmentResult) -> str:
        """Get the raw decision tier name from assessment."""
        return assessment.decision or "APPROVE"

    def get_decision_tier_description(self, assessment: AssessmentResult) -> str:
        """Get the description for the decision tier."""
        decision_str = (assessment.decision or "APPROVE").upper()
        for tier in DECISION_TIERS:
            if tier.name == decision_str:
                return tier.description or "Unknown tier"
        return "Unknown tier"
