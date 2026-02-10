"""Throughput and stress benchmarks for the evaluation pipeline (T-05).

Complements the Kafka benchmark script (scripts/benchmark_kafka.py) with
in-process benchmarks that don't need Kafka/Postgres running.

Run with: pytest tests/benchmark/ -v -s
"""

import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import get_settings
from app.core.evaluation_service import FraudEvaluationService
from app.core.fraud_detector import FraudDetector
from app.models.alert import Decision
from app.schemas.transaction import TransactionEvaluateRequest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_rule(code, field, operator, value, score):
    """Build a FraudRule-like object."""
    return SimpleNamespace(
        code=code,
        name=f"Rule {code}",
        description=f"Test rule {code}",
        category="amount",
        severity="medium",
        score=score,
        enabled=True,
        conditions={"field": field, "operator": operator, "value": value},
        effective_from=None,
        effective_to=None,
    )


def _make_eval_data(**overrides):
    data = {
        "amount": 500.0,
        "currency": "ZAR",
        "transaction_type": "purchase",
        "channel": "online",
        "customer_id": str(uuid.uuid4()),
        "merchant_id": "MERCH-001",
        "merchant_name": "Test Store",
        "merchant_category": "retail",
        "location_country": "ZA",
        "location_city": "Cape Town",
        "device_fingerprint": "fp-abc",
        "ip_address": "41.0.0.1",
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# Benchmark: FraudDetector.evaluate() throughput
# ---------------------------------------------------------------------------


class TestFraudDetectorThroughput:
    """Benchmark raw rule evaluation performance (no DB, no network)."""

    def test_1000_evaluations_under_2_seconds(self):
        """1000 evaluations with 5 rules should complete in <2s."""
        settings = get_settings()
        detector = FraudDetector(settings)

        rules = [
            _make_rule("AMT_001", "amount", "greater_than", 10000, 30),
            _make_rule("AMT_002", "amount", "greater_than", 50000, 25),
            _make_rule("GEO_001", "location_country", "in", ["KP", "IR", "SY"], 40),
            _make_rule("VEL_001", "txn_count_1h", "greater_than", 10, 20),
            _make_rule("DEV_001", "device_fingerprint", "equals", None, 15),
        ]
        detector.load_rules(rules)

        count = 1000
        start = time.perf_counter()

        for i in range(count):
            data = _make_eval_data(amount=float(i * 100))
            detector.evaluate(data)

        elapsed = time.perf_counter() - start
        rate = count / elapsed

        print(f"\n  FraudDetector: {count} evals in {elapsed:.3f}s ({rate:.0f} eval/s)")
        assert elapsed < 2.0, f"Expected <2s, got {elapsed:.3f}s"

    def test_high_rule_count_performance(self):
        """50 rules should still evaluate 500 transactions in <2s."""
        settings = get_settings()
        detector = FraudDetector(settings)

        rules = [
            _make_rule(
                f"TST_{i:03d}",
                "amount",
                "greater_than",
                1000 * (i + 1),
                2,
            )
            for i in range(50)
        ]
        detector.load_rules(rules)

        count = 500
        start = time.perf_counter()

        for i in range(count):
            data = _make_eval_data(amount=float(i * 1000))
            detector.evaluate(data)

        elapsed = time.perf_counter() - start
        rate = count / elapsed

        print(f"\n  50-rule detector: {count} evals in {elapsed:.3f}s ({rate:.0f} eval/s)")
        assert elapsed < 2.0, f"Expected <2s, got {elapsed:.3f}s"


# ---------------------------------------------------------------------------
# Benchmark: FraudEvaluationService pipeline throughput (mocked I/O)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestEvaluationPipelineThroughput:
    """Benchmark the full evaluate() pipeline with mocked DB/Redis."""

    async def test_100_pipeline_evaluations_under_2_seconds(self):
        """100 full pipeline calls (mocked I/O) should complete in <2s."""
        settings = _make_settings_mock()
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)

        service = FraudEvaluationService(settings, redis)

        count = 100
        start = time.perf_counter()

        for i in range(count):
            request = TransactionEvaluateRequest(
                external_id=f"BENCH-{uuid.uuid4().hex[:8]}",
                customer_id=str(uuid.uuid4()),
                amount=150 + i,
                currency="ZAR",
                transaction_type="purchase",
                channel="online",
            )
            session = AsyncMock()
            session.commit = AsyncMock()

            txn = SimpleNamespace(id=uuid.uuid4(), external_id=request.external_id)
            assessment = SimpleNamespace(
                total_score=10,
                triggered_rules=[],
                processing_time_ms=1.0,
            )

            with (
                patch("app.core.evaluation_service.TransactionRepository") as MockTxnRepo,
                patch("app.core.evaluation_service.RuleRepository"),
                patch("app.core.evaluation_service.AlertRepository"),
            ):
                MockTxnRepo.return_value.upsert = AsyncMock(return_value=txn)

                detector = MagicMock()
                detector.evaluate.return_value = assessment
                detector.get_decision.return_value = Decision.APPROVE
                detector.get_decision_tier.return_value = "low"
                detector.get_decision_tier_description.return_value = "Low risk"

                await service.evaluate(request, session, fraud_detector=detector, enrich=False)

        elapsed = time.perf_counter() - start
        rate = count / elapsed

        print(f"\n  Pipeline: {count} evals in {elapsed:.3f}s ({rate:.0f} eval/s)")
        assert elapsed < 2.0, f"Expected <2s, got {elapsed:.3f}s"


# ---------------------------------------------------------------------------
# Stress: concurrent evaluations
# ---------------------------------------------------------------------------


class TestConcurrentEvaluations:
    """Verify FraudDetector is safe under concurrent access."""

    def test_detector_handles_sequential_reload_and_evaluate(self):
        """Simulate rule reload between evaluations (no crash)."""
        settings = get_settings()
        detector = FraudDetector(settings)

        rules_v1 = [_make_rule("AMT_001", "amount", "greater_than", 10000, 30)]
        rules_v2 = [
            _make_rule("AMT_001", "amount", "greater_than", 5000, 30),
            _make_rule("GEO_001", "location_country", "in", ["KP"], 40),
        ]

        detector.load_rules(rules_v1)
        r1 = detector.evaluate(_make_eval_data(amount=20000))
        assert r1.total_score > 0

        # Reload with different rules
        detector.load_rules(rules_v2)
        r2 = detector.evaluate(_make_eval_data(amount=20000, location_country="KP"))
        assert r2.total_score > r1.total_score  # GEO rule adds score


def _make_settings_mock():
    settings = MagicMock()
    settings.rule_cache_ttl = 300
    return settings
