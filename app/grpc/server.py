"""gRPC server for the Fraud Evaluation service."""

import asyncio
import logging
from concurrent import futures
from decimal import Decimal

import grpc
from grpc import aio as grpc_aio
from redis.asyncio import Redis

from app.config import get_settings
from app.core.feature_service import FeatureService
from app.core.fraud_detector import FraudDetector
from app.dependencies import get_engine, get_session_factory
from app.grpc.generated import (  # pyright: ignore[reportMissingImports]
    fraud_evaluation_pb2,
    fraud_evaluation_pb2_grpc,
)
from app.models.alert import AlertStatus, Decision
from app.repositories.alert_repository import AlertRepository
from app.repositories.config_repository import ConfigRepository
from app.repositories.rule_repository import RuleRepository
from app.repositories.transaction_repository import TransactionRepository
from app.schemas.transaction import TransactionEvaluateRequest

logger = logging.getLogger(__name__)


class FraudEvaluationServicer(fraud_evaluation_pb2_grpc.FraudEvaluationServiceServicer):
    """gRPC servicer that evaluates transactions for fraud."""

    def __init__(self):
        self.settings = get_settings()
        self._engine = get_engine(self.settings)
        self._session_factory = get_session_factory(self.settings)
        self._redis = Redis.from_url(str(self.settings.redis_url), decode_responses=True)

    async def Evaluate(self, request, context):
        """Evaluate a transaction for fraud risk — mirrors the HTTP /evaluate endpoint."""
        async with self._session_factory() as db:
            try:
                # Build the internal evaluation request from gRPC message
                eval_request = TransactionEvaluateRequest(
                    external_id=request.external_id,
                    customer_id=request.customer_id,
                    amount=Decimal(str(request.amount)),
                    currency=request.currency or "ZAR",
                    transaction_type=request.transaction_type,
                    channel=request.channel,
                    merchant_id=request.merchant_id or None,
                    merchant_name=request.merchant_name or None,
                    merchant_category=request.merchant_category or None,
                    location_country=request.location_country or None,
                    location_city=request.location_city or None,
                    device_fingerprint=request.device_fingerprint or None,
                    ip_address=request.ip_address or None,
                )

                txn_repo = TransactionRepository(db)
                rule_repo = RuleRepository(db)
                alert_repo = AlertRepository(db)
                feature_service = FeatureService(self._redis, self.settings)

                # Upsert transaction
                txn = await txn_repo.upsert(eval_request)

                # Load rules and initialise detector
                rules = await rule_repo.get_enabled_rules()
                detector = FraudDetector(self.settings)
                detector.load_rules(rules)

                # Prepare evaluation data
                eval_data = {
                    "amount": float(eval_request.amount),
                    "currency": eval_request.currency,
                    "transaction_type": eval_request.transaction_type,
                    "channel": eval_request.channel,
                    "merchant_id": eval_request.merchant_id,
                    "merchant_name": eval_request.merchant_name,
                    "merchant_category": eval_request.merchant_category,
                    "location_country": eval_request.location_country,
                    "location_city": eval_request.location_city,
                    "device_fingerprint": eval_request.device_fingerprint,
                    "ip_address": eval_request.ip_address,
                    "customer_id": eval_request.customer_id,
                }

                # Enrich with features
                eval_data = await feature_service.enrich_transaction(eval_data)

                # Run pylitmus evaluation
                assessment = detector.evaluate(eval_data)
                decision = detector.get_decision(assessment)

                # Create alert if not APPROVE
                alert_id = ""
                alert_created = False

                if decision != Decision.APPROVE:
                    alert = await alert_repo.create(
                        transaction_id=txn.id,
                        customer_id=eval_request.customer_id,
                        risk_score=assessment.total_score,
                        decision=decision,
                        decision_tier=detector.get_decision_tier(assessment),
                        decision_tier_description=detector.get_decision_tier_description(
                            assessment
                        ),
                        triggered_rules=[
                            {
                                "code": r.rule_code,
                                "name": r.rule_name,
                                "category": r.category,
                                "severity": r.severity.value,
                                "score": r.score,
                                "description": r.explanation,
                            }
                            for r in assessment.triggered_rules
                        ],
                        processing_time_ms=assessment.processing_time_ms,
                    )
                    alert_id = str(alert.id)
                    alert_created = True

                    # Auto-confirm score 100 (no evaluation needed)
                    if assessment.total_score >= 100:
                        alert.status = AlertStatus.CONFIRMED.value
                        await db.commit()
                        await db.refresh(alert)
                    else:
                        # Auto-escalate if risk score meets threshold
                        config_repo = ConfigRepository(db)
                        threshold = await config_repo.get_int("auto_escalation_threshold", 90)
                        if assessment.total_score >= threshold:
                            alert.status = AlertStatus.ESCALATED.value
                            await db.commit()
                            await db.refresh(alert)

                    try:
                        from app.tasks.notifications import send_fraud_notification

                        send_fraud_notification.delay(alert.id)  # pyright: ignore[reportFunctionMemberAccess]
                    except Exception as e:
                        logger.error(f"Failed to queue notification task: {e}")

                # Build gRPC response
                triggered = [
                    fraud_evaluation_pb2.TriggeredRule(  # pyright: ignore[reportAttributeAccessIssue]
                        code=r.rule_code,
                        name=r.rule_name,
                        category=r.category,
                        severity=r.severity.value,
                        score=r.score,
                        description=r.explanation or "",
                    )
                    for r in assessment.triggered_rules
                ]

                return fraud_evaluation_pb2.EvaluateResponse(  # pyright: ignore[reportAttributeAccessIssue]
                    transaction_id=str(txn.id),
                    external_id=eval_request.external_id,
                    risk_score=assessment.total_score,
                    decision=decision.value,
                    decision_tier=detector.get_decision_tier(assessment),
                    decision_tier_description=detector.get_decision_tier_description(assessment),
                    triggered_rules=triggered,
                    processing_time_ms=assessment.processing_time_ms,
                    alert_created=alert_created,
                    alert_id=alert_id,
                )

            except Exception as e:
                logger.exception(f"gRPC Evaluate failed: {e}")
                context.set_code(grpc.StatusCode.INTERNAL)
                context.set_details(str(e))
                return fraud_evaluation_pb2.EvaluateResponse()  # pyright: ignore[reportAttributeAccessIssue]


async def serve(port: int = 50051):
    """Start the async gRPC server."""
    server = grpc_aio.server(futures.ThreadPoolExecutor(max_workers=10))
    fraud_evaluation_pb2_grpc.add_FraudEvaluationServiceServicer_to_server(
        FraudEvaluationServicer(), server
    )
    listen_addr = f"0.0.0.0:{port}"
    server.add_insecure_port(listen_addr)
    logger.info(f"gRPC FraudEvaluationService listening on {listen_addr}")
    await server.start()
    await server.wait_for_termination()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(serve())
