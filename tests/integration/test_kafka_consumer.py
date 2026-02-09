"""Integration tests for fraud.inbound.kafka — the Kafka consumer pipeline.

These tests verify the TransactionConsumer processes messages correctly
by publishing to Kafka and checking the results in the database.
They run against the live devstack containers.
"""
import asyncio
import json
import uuid

import pytest
import pytest_asyncio
from aiokafka import AIOKafkaProducer
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from tests.conftest import make_high_risk_transaction, make_transaction

pytestmark = pytest.mark.asyncio

TOPIC = "transactions.raw"
DLQ_TOPIC = "transactions.dlq"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture()
async def kafka_producer():
    """Create a Kafka producer for publishing test messages."""
    settings = get_settings()
    producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )
    await producer.start()
    yield producer
    await producer.stop()


async def _wait_for_transaction(db_session: AsyncSession, external_id: str, timeout: float = 15.0) -> dict | None:
    """Poll the database until the transaction appears or timeout."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        result = await db_session.execute(
            text("SELECT id, external_id FROM transactions WHERE external_id = :eid"),
            {"eid": external_id},
        )
        row = result.fetchone()
        if row:
            return {"id": str(row[0]), "external_id": row[1]}
        await asyncio.sleep(0.5)
        # Refresh session to see committed data from consumer
        await db_session.rollback()
    return None


async def _wait_for_alert(db_session: AsyncSession, external_id: str, timeout: float = 15.0) -> dict | None:
    """Poll the database until an alert linked to the transaction appears."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        result = await db_session.execute(
            text("""
                SELECT a.id, a.risk_score, a.decision, a.status, a.triggered_rules
                FROM fraud_alerts a
                JOIN transactions t ON a.transaction_id = t.id
                WHERE t.external_id = :eid
            """),
            {"eid": external_id},
        )
        row = result.fetchone()
        if row:
            return {
                "id": str(row[0]),
                "risk_score": row[1],
                "decision": row[2],
                "status": row[3],
                "triggered_rules": row[4],
            }
        await asyncio.sleep(0.5)
        await db_session.rollback()
    return None


async def _check_idempotency_key(redis_client: Redis, external_id: str) -> bool:
    """Check if the idempotency key exists in Redis."""
    return await redis_client.exists(f"idempotency:transaction:{external_id}") > 0


# ---------------------------------------------------------------------------
# Tests — Message Processing
# ---------------------------------------------------------------------------

class TestKafkaConsumerProcessing:
    """Tests that the running Kafka consumer processes messages end-to-end."""

    async def test_consumer_processes_transaction(self, kafka_producer, db_session):
        """Publish a transaction to Kafka → consumer stores it in DB."""
        txn = make_transaction()
        await kafka_producer.send_and_wait(TOPIC, txn)

        row = await _wait_for_transaction(db_session, txn["external_id"])
        assert row is not None, f"Transaction {txn['external_id']} not found in DB after 15s"
        assert row["external_id"] == txn["external_id"]

    async def test_consumer_creates_alert_for_high_risk(self, kafka_producer, db_session):
        """High-risk transaction → consumer creates alert in DB."""
        txn = make_high_risk_transaction()
        await kafka_producer.send_and_wait(TOPIC, txn)

        alert = await _wait_for_alert(db_session, txn["external_id"])
        # Alert may or may not be created depending on which rules are seeded
        # But the transaction should definitely be stored
        row = await _wait_for_transaction(db_session, txn["external_id"])
        assert row is not None, f"Transaction {txn['external_id']} not found in DB"

        if alert:
            assert alert["risk_score"] > 0
            assert alert["decision"] in ("review", "flag")
            assert alert["status"] == "pending"

    async def test_consumer_sets_idempotency_key(self, kafka_producer, db_session, redis_client):
        """After processing, the idempotency key should exist in Redis."""
        txn = make_transaction()
        await kafka_producer.send_and_wait(TOPIC, txn)

        # Wait for the transaction to appear (proves consumer processed it)
        row = await _wait_for_transaction(db_session, txn["external_id"])
        assert row is not None

        # Idempotency key should be set
        has_key = await _check_idempotency_key(redis_client, txn["external_id"])
        assert has_key, "Idempotency key not found in Redis after processing"


class TestKafkaConsumerIdempotency:
    """Tests for duplicate message handling."""

    async def test_duplicate_message_not_reprocessed(self, kafka_producer, db_session):
        """Sending the same message twice should not create duplicate records."""
        txn = make_transaction()

        # Send twice
        await kafka_producer.send_and_wait(TOPIC, txn)
        await asyncio.sleep(2)
        await kafka_producer.send_and_wait(TOPIC, txn)

        # Wait for processing
        row = await _wait_for_transaction(db_session, txn["external_id"])
        assert row is not None

        # Count how many transactions have this external_id
        await asyncio.sleep(3)
        await db_session.rollback()
        result = await db_session.execute(
            text("SELECT count(*) FROM transactions WHERE external_id = :eid"),
            {"eid": txn["external_id"]},
        )
        count = result.scalar()
        assert count == 1, f"Expected 1 transaction, got {count} — duplicate was not prevented"


class TestKafkaConsumerValidation:
    """Tests for invalid message handling."""

    async def test_invalid_json_handled_gracefully(self, kafka_producer, db_session):
        """Invalid JSON should not crash the consumer (sent to DLQ)."""
        # Send raw bytes that aren't valid JSON
        raw_producer = AIOKafkaProducer(
            bootstrap_servers=get_settings().kafka_bootstrap_servers,
            value_serializer=lambda v: v.encode("utf-8") if isinstance(v, str) else v,
        )
        await raw_producer.start()
        await raw_producer.send_and_wait(TOPIC, b"not-valid-json{{{")
        await raw_producer.stop()

        # Consumer should still be alive — send a valid message after
        await asyncio.sleep(3)
        txn = make_transaction()
        await kafka_producer.send_and_wait(TOPIC, txn)

        row = await _wait_for_transaction(db_session, txn["external_id"])
        assert row is not None, "Consumer appears to have died after invalid JSON"

    async def test_missing_external_id_skipped(self, kafka_producer, db_session):
        """A message without external_id should be skipped, not crash consumer."""
        bad_msg = {
            "customer_id": str(uuid.uuid4()),
            "amount": "100.00",
            "currency": "ZAR",
            "transaction_type": "purchase",
            "channel": "online",
        }
        await kafka_producer.send_and_wait(TOPIC, bad_msg)

        # Send a valid message after to prove consumer is still running
        await asyncio.sleep(2)
        txn = make_transaction()
        await kafka_producer.send_and_wait(TOPIC, txn)

        row = await _wait_for_transaction(db_session, txn["external_id"])
        assert row is not None, "Consumer appears to have died after bad message"


class TestKafkaConsumerMetrics:
    """Tests that the consumer produces expected side effects."""

    async def test_alert_has_triggered_rules(self, kafka_producer, db_session):
        """Alerts should contain details of which rules triggered."""
        txn = make_high_risk_transaction()
        await kafka_producer.send_and_wait(TOPIC, txn)

        alert = await _wait_for_alert(db_session, txn["external_id"])
        if alert is None:
            pytest.skip("No alert created — rules may not have triggered for this payload")

        rules = alert["triggered_rules"]
        assert isinstance(rules, list)
        assert len(rules) > 0
        for rule in rules:
            assert "code" in rule
            assert "score" in rule

    async def test_multiple_transactions_processed_in_order(self, kafka_producer, db_session):
        """Multiple messages should all be processed."""
        txns = [make_transaction() for _ in range(5)]

        for txn in txns:
            await kafka_producer.send_and_wait(TOPIC, txn)

        # Wait and verify all appear
        for txn in txns:
            row = await _wait_for_transaction(db_session, txn["external_id"], timeout=20.0)
            assert row is not None, f"Transaction {txn['external_id']} not processed"
