"""Fraud evaluation service facade — single entry point for transaction evaluation.

DRYs up the duplicated logic between the gRPC server and Kafka consumer.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.pylitmus_adapter import PylitmusAdapter
from app.config import Settings
from app.core.feature_service import FeatureService
from app.core.fraud_detector import EvaluationError, FraudDetector
from app.models.alert import AlertStatus, Decision, FraudAlert
from app.repositories.alert_repository import AlertRepository
from app.repositories.config_repository import ConfigRepository
from app.repositories.rule_repository import RuleRepository
from app.repositories.transaction_repository import TransactionRepository
from app.schemas.alert import SYSTEM_REVIEWER
from app.schemas.transaction import TransactionEvaluateRequest

logger = logging.getLogger(__name__)


@dataclass
class EvaluationResult:
    """Result of a fraud evaluation."""

    transaction_id: str
    external_id: str
    risk_score: int
    decision: str
    decision_tier: str
    decision_tier_description: str
    triggered_rules: list[dict[str, Any]] = field(default_factory=list)
    processing_time_ms: float = 0.0
    alert_created: bool = False
    alert_id: str = ""


class FraudEvaluationService:
    """Facade that orchestrates the full transaction evaluation pipeline.

    Used by both the gRPC server and the Kafka consumer to avoid duplicating
    the upsert → evaluate → alert → escalate flow.
    """

    def __init__(self, settings: Settings, redis: Redis):
        self._settings = settings
        self._redis = redis
        self._feature_service = FeatureService(redis)

    async def evaluate(
        self,
        request: TransactionEvaluateRequest,
        session: AsyncSession,
        *,
        fraud_detector: FraudDetector | None = None,
        enrich: bool = True,
    ) -> EvaluationResult:
        """Run the full evaluation pipeline for a transaction.

        All database writes use ``flush()`` so they participate in a
        single database transaction.  The caller owns the commit:

        - **HTTP path**: ``get_db_session`` commits after the route handler.
        - **gRPC / Kafka path**: this method commits explicitly at the end.

        Args:
            request: Validated transaction data.
            session: Active DB session (caller manages the session lifecycle).
            fraud_detector: Pre-initialised detector (Kafka consumer keeps one
                warm). If *None*, rules are loaded from the DB on each call
                (suitable for the gRPC path).
            enrich: Whether to call FeatureService to enrich eval data.
        """
        txn_repo = TransactionRepository(session)
        rule_repo = RuleRepository(session)
        alert_repo = AlertRepository(session)

        # 1. Upsert transaction
        try:
            txn = await txn_repo.upsert(request)
        except SQLAlchemyError as exc:
            logger.error(
                "Failed to upsert transaction external_id=%s: %s",
                request.external_id,
                exc,
            )
            raise

        # 2. Obtain a fraud detector
        if fraud_detector is None:
            fraud_detector = FraudDetector(self._settings)
            rules = await rule_repo.get_enabled_rules()
            fraud_detector.load_rules(rules)

        # 3. Build evaluation data dict
        eval_data = PylitmusAdapter.to_eval_data(request)

        # 4. (Optional) Enrich with feature service
        if enrich:
            eval_data = await self._feature_service.enrich_transaction(eval_data)

        # 5. Run pylitmus evaluation
        assessment = fraud_detector.evaluate(eval_data)
        decision = fraud_detector.get_decision(assessment)

        triggered_rules = PylitmusAdapter.to_triggered_rules(assessment.triggered_rules)

        # Cache decision tier values to avoid repeated computation
        decision_tier = fraud_detector.get_decision_tier(assessment)
        decision_tier_desc = fraud_detector.get_decision_tier_description(assessment)

        result = EvaluationResult(
            transaction_id=str(txn.id),
            external_id=request.external_id,
            risk_score=assessment.total_score,
            decision=decision.value,
            decision_tier=decision_tier,
            decision_tier_description=decision_tier_desc,
            triggered_rules=triggered_rules,
            processing_time_ms=assessment.processing_time_ms,
        )

        # 6. Create alert if not APPROVE
        if decision != Decision.APPROVE:
            alert = await alert_repo.create(
                transaction_id=txn.id,
                customer_id=request.customer_id,
                risk_score=assessment.total_score,
                decision=decision,
                decision_tier=decision_tier,
                decision_tier_description=decision_tier_desc,
                triggered_rules=triggered_rules,
                processing_time_ms=assessment.processing_time_ms,
            )
            result.alert_id = str(alert.id)
            result.alert_created = True

            # 7. Auto-confirm / auto-escalate
            await _auto_escalate(alert_repo, alert, assessment.total_score, session)

        # Commit the full unit of work (no-op if session was already
        # committed by the FastAPI dependency on the HTTP path).
        await session.commit()

        return result


async def _auto_escalate(
    alert_repo: AlertRepository,
    alert: FraudAlert,
    total_score: int,
    session: AsyncSession,
) -> None:
    """Auto-confirm score >= 100, else auto-escalate above threshold.

    Routes status changes through ``AlertRepository.review()`` so that
    audit fields (reviewed_by, reviewed_at) are populated consistently.
    Reuses the caller's ``alert_repo`` to avoid redundant instantiation.
    """
    if total_score >= 100:
        await alert_repo.review(
            str(alert.id),
            status=AlertStatus.CONFIRMED,
            reviewer=SYSTEM_REVIEWER,
        )
    else:
        config_repo = ConfigRepository(session)
        threshold = await config_repo.get_int("auto_escalation_threshold", 90)
        if total_score >= threshold:
            await alert_repo.review(
                str(alert.id),
                status=AlertStatus.ESCALATED,
                reviewer=SYSTEM_REVIEWER,
            )
