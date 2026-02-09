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
from app.core.evaluation_service import FraudEvaluationService
from app.core.fraud_detector import FraudDetector
from app.repositories.rule_repository import RuleRepository
from app.schemas.transaction import TransactionEvaluateRequest

logger = logging.getLogger(__name__)


class TransactionConsumer(BaseConsumer):
    """
    Consumer for processing transaction events.

    Flow:
    1. Consume message from transactions.raw
    2. Check idempotency (skip if already processed)
    3. Validate and parse transaction
    4. Evaluate via FraudEvaluationService (upsert + score + alert)
    5. Commit offset
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
        self._evaluation_service = FraudEvaluationService(settings, redis)
        self._fraud_detector: FraudDetector | None = None
        self._rules_count = 0

    async def start(self) -> None:
        """Start consumer and initialize fraud detector."""
        await super().start()

        # Initialize fraud detector with rules (kept warm across messages)
        self._fraud_detector = FraudDetector(self.settings)

        async with self.session_factory() as session:
            rule_repo = RuleRepository(session)
            rules = await rule_repo.get_enabled_rules()
            self._fraud_detector.load_rules(rules)
            self._rules_count = len(rules)

        logger.info("Loaded %d fraud rules", self._rules_count)

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
                    logger.info("Skipping duplicate message: %s", external_id)
                    status = "duplicate"
                    return

                # Process transaction
                result = await self._process_transaction(data)
                processor.set_result(result)

                logger.info(
                    "Processed transaction %s: score=%d, decision=%s",
                    external_id,
                    result["risk_score"],
                    result["decision"],
                )

        except json.JSONDecodeError as e:
            logger.error("Invalid JSON in message: %s", e)
            status = "error"
            raise
        except Exception as e:
            logger.error("Error processing transaction: %s", e)
            status = "error"
            raise
        finally:
            duration = time.perf_counter() - start_time
            logger.debug("Message processing took %.3fs, status=%s", duration, status)

    async def _process_transaction(self, data: dict[str, Any]) -> dict[str, Any]:
        """Process a single transaction through the fraud engine."""
        try:
            txn_request = TransactionEvaluateRequest(**data)
        except Exception as e:
            raise ValueError(f"Invalid transaction data: {e}") from e

        if self._fraud_detector is None:
            raise RuntimeError("FraudDetector not initialised — call start() first")

        async with self.session_factory() as session:
            result = await self._evaluation_service.evaluate(
                txn_request,
                session,
                fraud_detector=self._fraud_detector,
                enrich=False,
            )

            return {
                "transaction_id": result.transaction_id,
                "external_id": result.external_id,
                "risk_score": result.risk_score,
                "decision": result.decision,
                "triggered_rules": result.triggered_rules,
                "processing_time_ms": result.processing_time_ms,
                **({"alert_id": result.alert_id} if result.alert_created else {}),
            }
