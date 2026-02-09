"""Benchmark: publish 1000 transactions to Kafka and measure end-to-end processing time.

Publishes N transactions as fast as possible, then polls the database until all
have been processed by the fraud.inbound.kafka consumer.  Reports per-phase and
aggregate throughput statistics.

Usage:
    python scripts/benchmark_kafka.py [--count 1000] [--high-risk 0.2]
"""

import argparse
import asyncio
import json
import random
import statistics
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from aiokafka import AIOKafkaProducer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Reuse the transaction generator
sys.path.insert(0, str(Path(__file__).parent.parent))
import scripts.generate_transactions as _txn_module
from scripts.generate_transactions import _load_customer_ids, generate_transaction

# ---------------------------------------------------------------------------
# Configuration — uses app settings when running inside Docker,
# falls back to localhost-mapped ports for host-side access.
# ---------------------------------------------------------------------------
try:
    from app.config import get_settings
    _settings = get_settings()
    KAFKA_BOOTSTRAP = _settings.kafka_bootstrap_servers
    DATABASE_URL = str(_settings.database_url)
except Exception:
    KAFKA_BOOTSTRAP = "localhost:9092"
    DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5433/fraud_engine"

TOPIC = "transactions.raw"


# ---------------------------------------------------------------------------
# Phase 1: Publish transactions
# ---------------------------------------------------------------------------
async def publish_transactions(count: int, high_risk_pct: float) -> list[str]:
    """Publish *count* transactions to Kafka as fast as possible.

    Returns the list of external_ids for verification.
    """
    # Ensure generate_transaction() uses real banking customer IDs
    _txn_module.CUSTOMERS = await _load_customer_ids()

    producer = AIOKafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP,
        value_serializer=lambda m: json.dumps(m).encode("utf-8"),
        linger_ms=5,
    )
    await producer.start()

    external_ids: list[str] = []
    try:
        for _ in range(count):
            high_risk = random.random() < high_risk_pct
            txn = generate_transaction(high_risk=high_risk)
            external_ids.append(txn["external_id"])
            await producer.send(TOPIC, txn)  # fire-and-forget for speed
        # Flush remaining batched messages
        await producer.flush()
    finally:
        await producer.stop()

    return external_ids


# ---------------------------------------------------------------------------
# Phase 2: Wait for consumer to process all transactions
# ---------------------------------------------------------------------------
async def wait_for_processing(
    external_ids: list[str],
    timeout: float = 120.0,
    poll_interval: float = 0.5,
) -> tuple[float, int, int]:
    """Poll the database until all external_ids appear in the transactions table.

    Returns (elapsed_seconds, processed_count, alert_count).
    """
    engine = create_async_engine(DATABASE_URL, pool_size=3)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    id_set = set(external_ids)
    total = len(id_set)
    start = time.perf_counter()
    deadline = start + timeout
    processed = 0
    last_report = 0

    try:
        while time.perf_counter() < deadline:
            async with factory() as session:
                # Count how many of our external_ids are in the DB
                result = await session.execute(
                    text("""
                        SELECT count(*)
                        FROM transactions
                        WHERE external_id = ANY(:ids)
                    """),
                    {"ids": list(id_set)},
                )
                processed = result.scalar() or 0

            elapsed = time.perf_counter() - start

            # Progress reporting every 10%
            pct = int((processed / total) * 100)
            if pct >= last_report + 10:
                rate = processed / elapsed if elapsed > 0 else 0
                print(
                    f"  [{pct:3d}%] {processed}/{total} processed  "
                    f"({elapsed:.1f}s elapsed, {rate:.0f} txn/s)"
                )
                last_report = pct

            if processed >= total:
                break

            await asyncio.sleep(poll_interval)

        elapsed = time.perf_counter() - start

        # Count alerts created for our transactions
        async with factory() as session:
            result = await session.execute(
                text("""
                    SELECT count(*)
                    FROM fraud_alerts a
                    JOIN transactions t ON a.transaction_id = t.id
                    WHERE t.external_id = ANY(:ids)
                """),
                {"ids": list(id_set)},
            )
            alert_count = result.scalar() or 0

        return elapsed, processed, alert_count

    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Phase 3: Gather per-transaction timing from alerts
# ---------------------------------------------------------------------------
async def get_processing_times(external_ids: list[str]) -> list[float]:
    """Retrieve processing_time_ms for each alert linked to our transactions."""
    engine = create_async_engine(DATABASE_URL, pool_size=2)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with factory() as session:
            result = await session.execute(
                text("""
                    SELECT a.processing_time_ms
                    FROM fraud_alerts a
                    JOIN transactions t ON a.transaction_id = t.id
                    WHERE t.external_id = ANY(:ids)
                      AND a.processing_time_ms IS NOT NULL
                """),
                {"ids": list(external_ids)},
            )
            return [float(row[0]) for row in result.all()]
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def print_report(
    count: int,
    publish_time: float,
    processing_time: float,
    processed: int,
    alert_count: int,
    per_txn_times: list[float],
    high_risk_pct: float,
) -> None:
    total_time = publish_time + processing_time

    print("\n" + "=" * 64)
    print("  KAFKA CONSUMER BENCHMARK RESULTS")
    print("=" * 64)

    print(f"\n  Transactions published :  {count:,}")
    print(f"  High-risk percentage   :  {high_risk_pct * 100:.0f}%")
    print(f"  Transactions processed :  {processed:,}")
    print(f"  Alerts created         :  {alert_count:,}")

    print(f"\n  --- Timing ---")
    print(f"  Publish phase          :  {publish_time:.2f}s  ({count / publish_time:,.0f} msg/s)")
    print(
        f"  Processing phase       :  {processing_time:.2f}s  "
        f"({processed / processing_time:,.0f} txn/s)"
        if processing_time > 0
        else f"  Processing phase       :  {processing_time:.2f}s"
    )
    print(f"  Total end-to-end       :  {total_time:.2f}s  ({count / total_time:,.0f} txn/s)")

    if per_txn_times:
        print(f"\n  --- Per-transaction rule evaluation (from alerts) ---")
        print(f"  Count                  :  {len(per_txn_times):,}")
        print(f"  Min                    :  {min(per_txn_times):.2f} ms")
        print(f"  Max                    :  {max(per_txn_times):.2f} ms")
        print(f"  Mean                   :  {statistics.mean(per_txn_times):.2f} ms")
        print(f"  Median                 :  {statistics.median(per_txn_times):.2f} ms")
        print(
            f"  Std Dev                :  {statistics.stdev(per_txn_times):.2f} ms"
            if len(per_txn_times) > 1
            else ""
        )
        p95 = sorted(per_txn_times)[int(len(per_txn_times) * 0.95)]
        p99 = sorted(per_txn_times)[min(int(len(per_txn_times) * 0.99), len(per_txn_times) - 1)]
        print(f"  P95                    :  {p95:.2f} ms")
        print(f"  P99                    :  {p99:.2f} ms")

    print("\n" + "=" * 64)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark Kafka consumer throughput")
    parser.add_argument(
        "--count",
        "-c",
        type=int,
        default=1000,
        help="Number of transactions to publish (default: 1000)",
    )
    parser.add_argument(
        "--high-risk",
        type=float,
        default=0.2,
        help="Fraction of high-risk transactions (default: 0.2)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Max seconds to wait for processing (default: 120)",
    )
    args = parser.parse_args()

    count = args.count
    high_risk_pct = args.high_risk

    print(f"\n  Benchmark: {count:,} transactions  ({high_risk_pct * 100:.0f}% high-risk)")
    print(f"  Kafka: {KAFKA_BOOTSTRAP}  |  DB: localhost:5433")
    print("-" * 64)

    # Phase 1: Publish
    print(f"\n[Phase 1] Publishing {count:,} transactions to Kafka...")
    t0 = time.perf_counter()
    external_ids = await publish_transactions(count, high_risk_pct)
    publish_time = time.perf_counter() - t0
    print(
        f"  Published {len(external_ids):,} messages in {publish_time:.2f}s "
        f"({count / publish_time:,.0f} msg/s)"
    )

    # Phase 2: Wait for consumer
    print(f"\n[Phase 2] Waiting for consumer to process all transactions...")
    processing_time, processed, alert_count = await wait_for_processing(
        external_ids, timeout=args.timeout
    )

    if processed < count:
        print(
            f"\n  WARNING: Only {processed}/{count} transactions processed "
            f"within {args.timeout}s timeout"
        )

    # Phase 3: Collect per-transaction metrics
    print(f"\n[Phase 3] Collecting per-transaction evaluation metrics...")
    per_txn_times = await get_processing_times(external_ids)

    # Report
    print_report(
        count, publish_time, processing_time, processed, alert_count, per_txn_times, high_risk_pct
    )


if __name__ == "__main__":
    asyncio.run(main())
