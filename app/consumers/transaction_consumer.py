"""Kafka consumer for processing transactions."""

import json
import logging
import time
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.consumers.base import BaseConsumer
from app.consumers.idempotency import IdempotencyService, IdempotentProcessor
from app.core.fraud_detector import FraudDetector
from app.models.alert import AlertStatus, Decision
from app.repositories.alert_repository import AlertRepository
from app.repositories.config_repository import ConfigRepository
from app.repositories.rule_repository import RuleRepository
from app.repositories.transaction_repository import TransactionRepository
from app.schemas.transaction import TransactionEvaluateRequest

logger = logging.getLogger(__name__)


class TransactionConsumer(BaseConsumer):
    """
    Consumer for processing transaction events.

    Flow:
    1. Consume message from transactions.raw
    2. Check idempotency (skip if already processed)
    3. Validate and parse transaction
    4. Store transaction in database
    5. Evaluate against fraud rules
    6. Create alert if fraud detected
    7. Commit offset
    """

    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        redis: Redis,
    ):
        super().__init__(
            settings=settings,
            topic="transactions.raw",
            dlq_topic="transactions.dlq",
        )
        self.session_factory = session_factory
        self.redis = redis
        self.idempotency = IdempotencyService(redis)
        self._fraud_detector: FraudDetector | None = None
        self._rules_count = 0

    async def start(self) -> None:
        """Start consumer and initialize fraud detector."""
        await super().start()

        # Initialize fraud detector with rules
        self._fraud_detector = FraudDetector(self.settings)

        # Load rules from database
        async with self.session_factory() as session:
            rule_repo = RuleRepository(session)
            rules = await rule_repo.get_enabled_rules()
            self._fraud_detector.load_rules(rules)
            self._rules_count = len(rules)

        logger.info(f"Loaded {self._rules_count} fraud rules")

    async def stop(self) -> None:
        """Stop consumer gracefully."""
        await super().stop()

    async def process(self, message: Any) -> None:
        """Process a transaction message."""
        start_time = time.perf_counter()
        status = "success"

        try:
            # Parse message
            data = json.loads(message.value)
            external_id = data.get("external_id")

            if not external_id:
                logger.warning("Message missing external_id, skipping")
                status = "invalid"
                return

            # Idempotency check
            async with IdempotentProcessor(
                self.idempotency, "transaction", external_id
            ) as processor:
                if not processor.should_process:
                    logger.info(f"Skipping duplicate message: {external_id}")
                    status = "duplicate"
                    return

                # Process transaction
                result = await self._process_transaction(data)
                processor.set_result(result)

                logger.info(
                    f"Processed transaction {external_id}: "
                    f"score={result['risk_score']}, decision={result['decision']}"
                )

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in message: {e}")
            status = "error"
            raise
        except Exception as e:
            logger.error(f"Error processing transaction: {e}")
            status = "error"
            raise
        finally:
            duration = time.perf_counter() - start_time
            logger.debug(f"Message processing took {duration:.3f}s, status={status}")

    async def _process_transaction(self, data: dict[str, Any]) -> dict[str, Any]:
        """Process a single transaction through the fraud engine."""
        # Validate transaction data
        try:
            txn_request = TransactionEvaluateRequest(**data)
        except Exception as e:
            raise ValueError(f"Invalid transaction data: {e}") from e

        async with self.session_factory() as session:
            # Initialize repositories
            txn_repo = TransactionRepository(session)
            alert_repo = AlertRepository(session)

            # Store transaction (idempotent)
            txn = await txn_repo.upsert(txn_request)

            # Prepare data for evaluation
            eval_data = {
                "amount": float(txn_request.amount),
                "currency": txn_request.currency,
                "transaction_type": txn_request.transaction_type,
                "channel": txn_request.channel,
                "merchant_id": txn_request.merchant_id,
                "merchant_name": txn_request.merchant_name,
                "merchant_category": txn_request.merchant_category,
                "location_country": txn_request.location_country,
                "location_city": txn_request.location_city,
                "device_fingerprint": txn_request.device_fingerprint,
                "ip_address": txn_request.ip_address,
                "customer_id": txn_request.customer_id,
            }

            if self._fraud_detector is None:
                raise RuntimeError("FraudDetector not initialised — call start() first")

            # Evaluate transaction
            assessment = self._fraud_detector.evaluate(eval_data)
            decision = self._fraud_detector.get_decision(assessment)

            result = {
                "transaction_id": txn.id,
                "external_id": txn_request.external_id,
                "risk_score": assessment.total_score,
                "decision": decision.value,
                "triggered_rules": [
                    {
                        "code": r.rule_code,
                        "name": r.rule_name,
                        "category": r.category,
                        "severity": r.severity.value,
                        "score": r.score,
                    }
                    for r in assessment.triggered_rules
                ],
                "processing_time_ms": assessment.processing_time_ms,
            }

            # Create alert if not approved
            if decision != Decision.APPROVE:
                alert = await alert_repo.create(
                    transaction_id=txn.id,
                    customer_id=txn_request.customer_id,
                    risk_score=assessment.total_score,
                    decision=decision,
                    triggered_rules=result["triggered_rules"],
                    processing_time_ms=assessment.processing_time_ms,
                )
                result["alert_id"] = alert.id

                # Auto-confirm score 100 (no evaluation needed)
                if assessment.total_score >= 100:
                    alert.status = AlertStatus.CONFIRMED.value
                    await session.commit()
                    await session.refresh(alert)
                else:
                    # Auto-escalate if risk score meets threshold
                    config_repo = ConfigRepository(session)
                    threshold = await config_repo.get_int("auto_escalation_threshold", 90)
                    if assessment.total_score >= threshold:
                        alert.status = AlertStatus.ESCALATED.value
                        await session.commit()
                        await session.refresh(alert)

                # Trigger notification task (Phase 4)
                # from app.tasks.notifications import send_fraud_notification
                # send_fraud_notification.delay(alert.id)

            return result
