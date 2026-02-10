"""Unit tests for the gRPC FraudEvaluationServicer (T-03).

Tests the servicer logic in isolation by mocking the evaluation service
and gRPC context. Does NOT start a real gRPC server.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.evaluation_service import EvaluationResult
from app.grpc.server import FraudEvaluationServicer

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_infra():
    """Mock InfrastructureContainer."""
    infra = MagicMock()
    mock_session = AsyncMock()
    infra.session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    infra.session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    infra.redis = AsyncMock()
    return infra, mock_session


def _make_grpc_request(**overrides):
    """Build a SimpleNamespace that mimics a gRPC EvaluateRequest."""
    data = {
        "external_id": f"TXN-{uuid.uuid4().hex[:12]}",
        "customer_id": str(uuid.uuid4()),
        "amount": 500.0,
        "currency": "ZAR",
        "transaction_type": "purchase",
        "channel": "online",
        "merchant_id": "MERCH-001",
        "merchant_name": "Test Store",
        "merchant_category": "retail",
        "location_country": "ZA",
        "location_city": "Cape Town",
        "device_fingerprint": "fp-abc",
        "ip_address": "41.0.0.1",
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _make_evaluation_result(**overrides):
    """Build a mock EvaluationResult."""
    data = {
        "transaction_id": str(uuid.uuid4()),
        "external_id": f"TXN-{uuid.uuid4().hex[:12]}",
        "risk_score": 10,
        "decision": "approve",
        "decision_tier": "low",
        "decision_tier_description": "Low risk",
        "triggered_rules": [],
        "processing_time_ms": 3.5,
        "alert_created": False,
        "alert_id": "",
    }
    data.update(overrides)
    return EvaluationResult(**data)


def _make_context():
    """Build a mock gRPC ServicerContext."""
    ctx = MagicMock()
    ctx.set_code = MagicMock()
    ctx.set_details = MagicMock()
    return ctx


# ---------------------------------------------------------------------------
# Tests — Evaluate RPC
# ---------------------------------------------------------------------------


class TestGrpcEvaluateSuccess:
    """Test successful gRPC Evaluate calls."""

    async def test_evaluate_returns_response(self):
        infra, _ = _make_infra()
        servicer = FraudEvaluationServicer(infra)

        request = _make_grpc_request()
        context = _make_context()
        eval_result = _make_evaluation_result(external_id=request.external_id)

        with patch.object(
            servicer._evaluation_service,
            "evaluate",
            new_callable=AsyncMock,
            return_value=eval_result,
        ):
            response = await servicer.Evaluate(request, context)

        assert response.external_id == request.external_id
        assert response.risk_score == 10
        assert response.decision == "approve"
        assert response.alert_created is False
        context.set_code.assert_not_called()

    async def test_evaluate_with_triggered_rules(self):
        infra, _ = _make_infra()
        servicer = FraudEvaluationServicer(infra)

        request = _make_grpc_request(amount=999999.0)
        context = _make_context()
        eval_result = _make_evaluation_result(
            external_id=request.external_id,
            risk_score=85,
            decision="flag",
            decision_tier="high",
            decision_tier_description="High risk",
            triggered_rules=[
                {
                    "code": "AMT_001",
                    "name": "High Amount",
                    "category": "amount",
                    "severity": "high",
                    "score": 85,
                    "description": "Amount exceeds threshold",
                },
            ],
            alert_created=True,
            alert_id=str(uuid.uuid4()),
        )

        with patch.object(
            servicer._evaluation_service,
            "evaluate",
            new_callable=AsyncMock,
            return_value=eval_result,
        ):
            response = await servicer.Evaluate(request, context)

        assert response.risk_score == 85
        assert response.decision == "flag"
        assert response.alert_created is True
        assert len(response.triggered_rules) == 1
        assert response.triggered_rules[0].code == "AMT_001"
        assert response.triggered_rules[0].score == 85


class TestGrpcEvaluateErrors:
    """Test gRPC error handling."""

    async def test_invalid_amount_returns_invalid_argument(self):
        infra, _ = _make_infra()
        servicer = FraudEvaluationServicer(infra)

        request = _make_grpc_request(amount="not-a-number")
        context = _make_context()

        response = await servicer.Evaluate(request, context)

        context.set_code.assert_called_once()
        code_arg = context.set_code.call_args[0][0]
        assert code_arg.name == "INVALID_ARGUMENT"

    async def test_evaluation_service_error_returns_internal(self):
        infra, _ = _make_infra()
        servicer = FraudEvaluationServicer(infra)

        request = _make_grpc_request()
        context = _make_context()

        with patch.object(
            servicer._evaluation_service,
            "evaluate",
            new_callable=AsyncMock,
            side_effect=RuntimeError("DB connection lost"),
        ):
            response = await servicer.Evaluate(request, context)

        context.set_code.assert_called_once()
        code_arg = context.set_code.call_args[0][0]
        assert code_arg.name == "INTERNAL"
        assert "RuntimeError" in context.set_details.call_args[0][0]

    async def test_validation_error_returns_invalid_argument(self):
        infra, _ = _make_infra()
        servicer = FraudEvaluationServicer(infra)

        request = _make_grpc_request()
        context = _make_context()

        with patch.object(
            servicer._evaluation_service,
            "evaluate",
            new_callable=AsyncMock,
            side_effect=ValueError("Invalid customer_id"),
        ):
            response = await servicer.Evaluate(request, context)

        context.set_code.assert_called_once()
        code_arg = context.set_code.call_args[0][0]
        assert code_arg.name == "INVALID_ARGUMENT"


class TestGrpcEvaluateRequestConversion:
    """Test that gRPC request fields are correctly converted."""

    async def test_optional_fields_default_to_none(self):
        infra, _ = _make_infra()
        servicer = FraudEvaluationServicer(infra)

        # gRPC sends empty strings for unset fields
        request = _make_grpc_request(
            merchant_id="",
            merchant_name="",
            merchant_category="",
            location_country="",
            location_city="",
            device_fingerprint="",
            ip_address="",
        )
        context = _make_context()
        eval_result = _make_evaluation_result(external_id=request.external_id)

        captured_request = None

        async def capture_evaluate(req, session, **kwargs):
            nonlocal captured_request
            captured_request = req
            return eval_result

        with patch.object(
            servicer._evaluation_service,
            "evaluate",
            side_effect=capture_evaluate,
        ):
            await servicer.Evaluate(request, context)

        # Empty gRPC strings should be converted to None
        assert captured_request is not None
        assert captured_request.merchant_id is None
        assert captured_request.merchant_name is None
        assert captured_request.device_fingerprint is None

    async def test_currency_defaults_to_zar(self):
        infra, _ = _make_infra()
        servicer = FraudEvaluationServicer(infra)

        request = _make_grpc_request(currency="")
        context = _make_context()
        eval_result = _make_evaluation_result(external_id=request.external_id)

        captured_request = None

        async def capture_evaluate(req, session, **kwargs):
            nonlocal captured_request
            captured_request = req
            return eval_result

        with patch.object(
            servicer._evaluation_service,
            "evaluate",
            side_effect=capture_evaluate,
        ):
            await servicer.Evaluate(request, context)

        assert captured_request.currency == "ZAR"
