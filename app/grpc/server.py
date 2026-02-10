"""gRPC server for the Fraud Evaluation service."""

import asyncio
import logging
from concurrent import futures
from decimal import Decimal

import grpc
from grpc import aio as grpc_aio
from grpc_health.v1 import health as grpc_health
from grpc_health.v1 import health_pb2 as health_pb2
from grpc_health.v1 import health_pb2_grpc as health_pb2_grpc

from app.config import get_settings
from app.core.evaluation_service import FraudEvaluationService
from app.dependencies import InfrastructureContainer
from app.grpc.generated import (  # pyright: ignore[reportMissingImports]
    fraud_evaluation_pb2,
    fraud_evaluation_pb2_grpc,
)
from app.schemas.transaction import TransactionEvaluateRequest
from app.telemetry import TracingInterceptor, init_telemetry

logger = logging.getLogger(__name__)


class FraudEvaluationServicer(fraud_evaluation_pb2_grpc.FraudEvaluationServiceServicer):
    """gRPC servicer that evaluates transactions for fraud."""

    def __init__(self, infra: InfrastructureContainer):
        self.settings = get_settings()
        self._infra = infra
        self._session_factory = infra.session_factory
        self._evaluation_service = FraudEvaluationService(self.settings, infra.redis)

    async def Evaluate(self, request, context):
        """Evaluate a transaction for fraud risk — mirrors the HTTP /evaluate endpoint."""
        async with self._session_factory() as db:
            try:
                # Build the internal evaluation request from gRPC message
                try:
                    amount = Decimal(str(request.amount))
                except Exception as exc:
                    raise ValueError(f"Invalid amount '{request.amount}': {exc}") from exc

                eval_request = TransactionEvaluateRequest(
                    external_id=request.external_id,
                    customer_id=request.customer_id,
                    amount=amount,
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

                result = await self._evaluation_service.evaluate(
                    eval_request,
                    db,
                    enrich=True,
                )

                # Build gRPC response
                triggered = [
                    fraud_evaluation_pb2.TriggeredRule(  # pyright: ignore[reportAttributeAccessIssue]
                        code=r["code"],
                        name=r["name"],
                        category=r["category"],
                        severity=r["severity"],
                        score=r["score"],
                        description=r.get("description") or "",
                    )
                    for r in result.triggered_rules
                ]

                return fraud_evaluation_pb2.EvaluateResponse(  # pyright: ignore[reportAttributeAccessIssue]
                    transaction_id=result.transaction_id,
                    external_id=result.external_id,
                    risk_score=result.risk_score,
                    decision=result.decision,
                    decision_tier=result.decision_tier,
                    decision_tier_description=result.decision_tier_description,
                    triggered_rules=triggered,
                    processing_time_ms=result.processing_time_ms,
                    alert_created=result.alert_created,
                    alert_id=result.alert_id,
                )

            except ValueError as e:
                logger.warning("gRPC Evaluate — validation error: %s", e)
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details(f"Validation error: {e}")
                return fraud_evaluation_pb2.EvaluateResponse(  # pyright: ignore[reportAttributeAccessIssue]
                    external_id=getattr(request, "external_id", ""),
                )

            except Exception as e:
                logger.exception("gRPC Evaluate failed: %s", e)
                context.set_code(grpc.StatusCode.INTERNAL)
                context.set_details(f"Internal error processing transaction: {type(e).__name__}")
                return fraud_evaluation_pb2.EvaluateResponse(  # pyright: ignore[reportAttributeAccessIssue]
                    external_id=getattr(request, "external_id", ""),
                )


async def serve(port: int = 50051):
    """Start the async gRPC server."""
    settings = get_settings()
    infra = InfrastructureContainer.from_settings(settings)
    await infra.verify()

    init_telemetry("core-fraud-detection.grpc")

    server = grpc_aio.server(
        futures.ThreadPoolExecutor(max_workers=10),
        options=[
            ("grpc.max_concurrent_streams", 100),
        ],
        interceptors=[TracingInterceptor()],
    )
    fraud_evaluation_pb2_grpc.add_FraudEvaluationServiceServicer_to_server(
        FraudEvaluationServicer(infra=infra), server
    )

    # Register gRPC health checking service (grpc.health.v1.Health)
    health_servicer = grpc_health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    # Mark the evaluation service and the overall server as SERVING
    health_servicer.set(
        "sentinel.fraud.v1.FraudEvaluationService",
        health_pb2.HealthCheckResponse.SERVING,
    )
    health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)

    listen_addr = f"0.0.0.0:{port}"
    server.add_insecure_port(listen_addr)
    logger.info("gRPC FraudEvaluationService listening on %s", listen_addr)
    await server.start()
    try:
        await server.wait_for_termination()
    finally:
        # Mark as NOT_SERVING before tearing down infrastructure
        health_servicer.set("", health_pb2.HealthCheckResponse.NOT_SERVING)
        await infra.close()
        logger.info("gRPC server shut down — infrastructure closed.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(serve())
