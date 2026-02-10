"""Real database integration tests using testcontainers (T-07).

These tests use a real PostgreSQL container instead of mocked repositories,
verifying actual SQL queries, constraints, and transaction behaviour.

Requires Docker to be running. Tests are skipped if Docker is unavailable.

Run with: pytest tests/integration/test_db_integration.py -v -s
"""

import asyncio
import subprocess
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

try:
    from testcontainers.postgres import PostgresContainer

    _HAS_TESTCONTAINERS = True
except ImportError:
    _HAS_TESTCONTAINERS = False

import app.models.alert as _alert_models  # noqa: F401
import app.models.config as _config_models  # noqa: F401
import app.models.rule as _rule_models  # noqa: F401
import app.models.transaction as _transaction_models  # noqa: F401
from app.models.alert import AlertStatus, Decision
from app.models.base import Base
from app.models.rule import FraudRule
from app.models.transaction import Transaction
from app.repositories.alert_repository import AlertRepository
from app.repositories.rule_repository import DuplicateRuleError, RuleRepository
from app.repositories.transaction_repository import TransactionRepository
from app.schemas.alert import ReviewerInfo
from app.schemas.rule import RuleCreate, RuleUpdate
from app.schemas.transaction import TransactionEvaluateRequest

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not _HAS_TESTCONTAINERS, reason="testcontainers not installed"),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def postgres_container():
    """Start a real PostgreSQL container for the test module.

    The container is started once and shared across all tests in this module
    for performance. Each test uses its own transaction that is rolled back.
    """
    # Check Docker is available
    result = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
    if result.returncode != 0:
        pytest.skip("Docker is not available")

    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="module")
def sync_db_url(postgres_container):
    """Get the sync database URL for the testcontainer."""
    return postgres_container.get_connection_url()


@pytest.fixture(scope="module")
def async_db_url(sync_db_url):
    """Convert sync DB URL to async (asyncpg)."""
    return sync_db_url.replace("psycopg2", "asyncpg").replace(
        "postgresql://", "postgresql+asyncpg://"
    )


@pytest.fixture(scope="module")
def _create_tables(async_db_url):
    """Create all tables in the test database (module scope)."""

    async def _setup():
        engine = create_async_engine(async_db_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # Create the alert reference sequence used by AlertRepository
            await conn.execute(text("CREATE SEQUENCE IF NOT EXISTS alert_ref_seq START 1"))
        await engine.dispose()

    asyncio.get_event_loop().run_until_complete(_setup())


@pytest.fixture()
async def db_session(async_db_url, _create_tables):
    """Provide a real async DB session. Each test gets a fresh session."""
    engine = create_async_engine(async_db_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        yield session

    await engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_txn_request(**overrides):
    data = {
        "external_id": f"TXN-{uuid.uuid4().hex[:12]}",
        "customer_id": str(uuid.uuid4()),
        "amount": Decimal("500.00"),
        "currency": "ZAR",
        "transaction_type": "purchase",
        "channel": "online",
    }
    data.update(overrides)
    return TransactionEvaluateRequest(**data)


# ---------------------------------------------------------------------------
# Tests — TransactionRepository with real DB
# ---------------------------------------------------------------------------


class TestTransactionRepositoryRealDB:
    """Test TransactionRepository against a real PostgreSQL database."""

    async def test_upsert_creates_new_transaction(self, db_session):
        repo = TransactionRepository(db_session)
        request = _make_txn_request()

        txn = await repo.upsert(request)
        await db_session.commit()

        assert txn is not None
        assert txn.external_id == request.external_id
        assert txn.amount == Decimal("500.00")
        assert txn.currency == "ZAR"

    async def test_upsert_is_idempotent(self, db_session):
        repo = TransactionRepository(db_session)
        external_id = f"TXN-{uuid.uuid4().hex[:12]}"
        request = _make_txn_request(external_id=external_id)

        txn1 = await repo.upsert(request)
        await db_session.commit()

        txn2 = await repo.upsert(request)
        await db_session.commit()

        # Same record returned both times
        assert txn1.id == txn2.id
        assert txn1.external_id == txn2.external_id

    async def test_get_by_external_id(self, db_session):
        repo = TransactionRepository(db_session)
        request = _make_txn_request()

        created = await repo.upsert(request)
        await db_session.commit()

        fetched = await repo.get_by_external_id(request.external_id)

        assert fetched is not None
        assert fetched.id == created.id

    async def test_get_by_external_id_not_found(self, db_session):
        repo = TransactionRepository(db_session)
        result = await repo.get_by_external_id("NONEXISTENT")
        assert result is None

    async def test_amount_positive_constraint(self, db_session):
        """DB constraint prevents amount <= 0."""
        txn = Transaction(
            external_id=f"TXN-{uuid.uuid4().hex[:12]}",
            customer_id=str(uuid.uuid4()),
            amount=Decimal("-1.00"),
            currency="ZAR",
            transaction_type="purchase",
            channel="online",
            transaction_time=datetime.now(UTC),
        )
        db_session.add(txn)

        with pytest.raises(IntegrityError):
            await db_session.flush()

        await db_session.rollback()


# ---------------------------------------------------------------------------
# Tests — RuleRepository with real DB
# ---------------------------------------------------------------------------


class TestRuleRepositoryRealDB:
    """Test RuleRepository CRUD against a real PostgreSQL database."""

    async def test_create_and_get_rule(self, db_session):
        repo = RuleRepository(db_session)
        code = f"TST_{uuid.uuid4().int % 1000:03d}"

        rule_data = RuleCreate(
            code=code,
            name="Test Rule",
            description="A test rule",
            category="amount",
            severity="medium",
            score=25,
            enabled=True,
            conditions={"field": "amount", "operator": "greater_than", "value": 10000},
        )

        created = await repo.create(rule_data)
        await db_session.commit()

        assert created.code == code
        assert created.score == 25

        fetched = await repo.get_by_code(code)
        assert fetched is not None
        assert fetched.name == "Test Rule"

    async def test_duplicate_rule_raises_error(self, db_session):
        repo = RuleRepository(db_session)
        code = f"DUP_{uuid.uuid4().int % 1000:03d}"

        rule_data = RuleCreate(
            code=code,
            name="First Rule",
            description="First",
            category="amount",
            severity="low",
            score=10,
            conditions={"field": "amount", "operator": "greater_than", "value": 100},
        )

        await repo.create(rule_data)
        await db_session.commit()

        with pytest.raises(DuplicateRuleError):
            await repo.create(rule_data)

    async def test_update_rule(self, db_session):
        repo = RuleRepository(db_session)
        code = f"UPD_{uuid.uuid4().int % 1000:03d}"

        rule_data = RuleCreate(
            code=code,
            name="Original",
            description="Before update",
            category="velocity",
            severity="low",
            score=10,
            conditions={"field": "txn_count_1h", "operator": "greater_than", "value": 5},
        )
        await repo.create(rule_data)
        await db_session.commit()

        updated = await repo.update(code, RuleUpdate(name="Updated", score=50))
        await db_session.commit()

        assert updated is not None
        assert updated.name == "Updated"
        assert updated.score == 50

    async def test_soft_delete_disables_rule(self, db_session):
        repo = RuleRepository(db_session)
        code = f"DEL_{uuid.uuid4().int % 1000:03d}"

        rule_data = RuleCreate(
            code=code,
            name="To Delete",
            description="Will be soft-deleted",
            category="amount",
            severity="medium",
            score=20,
            conditions={"field": "amount", "operator": "greater_than", "value": 500},
        )
        await repo.create(rule_data)
        await db_session.commit()

        deleted = await repo.delete(code)
        await db_session.commit()

        assert deleted is True

        fetched = await repo.get_by_code(code)
        assert fetched is not None
        assert fetched.enabled is False

    async def test_toggle_rule(self, db_session):
        repo = RuleRepository(db_session)
        code = f"TOG_{uuid.uuid4().int % 1000:03d}"

        rule_data = RuleCreate(
            code=code,
            name="Toggleable",
            description="Toggle test",
            category="device",
            severity="high",
            score=50,
            conditions={"field": "device_fingerprint", "operator": "equals", "value": None},
        )
        await repo.create(rule_data)
        await db_session.commit()

        toggled = await repo.toggle(code)
        await db_session.commit()

        assert toggled is not None
        assert toggled.enabled is False

        toggled_back = await repo.toggle(code)
        await db_session.commit()

        assert toggled_back.enabled is True

    async def test_score_constraint_range(self, db_session):
        """DB constraint prevents score outside 0-100."""
        rule = FraudRule(
            code=f"BAD_{uuid.uuid4().int % 1000:03d}",
            name="Bad Score",
            category="amount",
            severity="low",
            score=150,
            enabled=True,
            conditions={"field": "amount", "operator": "greater_than", "value": 100},
        )
        db_session.add(rule)

        with pytest.raises(IntegrityError):
            await db_session.flush()

        await db_session.rollback()


# ---------------------------------------------------------------------------
# Tests — AlertRepository with real DB
# ---------------------------------------------------------------------------


class TestAlertRepositoryRealDB:
    """Test AlertRepository against a real PostgreSQL database."""

    async def _create_transaction(self, db_session):
        """Helper: insert a transaction so alerts can reference it."""
        repo = TransactionRepository(db_session)
        request = _make_txn_request()
        txn = await repo.upsert(request)
        await db_session.flush()
        return txn

    async def test_create_alert(self, db_session):
        txn = await self._create_transaction(db_session)
        repo = AlertRepository(db_session)

        alert = await repo.create(
            transaction_id=txn.id,
            customer_id=txn.customer_id,
            risk_score=85,
            decision=Decision.FLAG,
            decision_tier="high",
            decision_tier_description="High risk",
            triggered_rules=[{"code": "AMT_001", "score": 85}],
            processing_time_ms=5.0,
        )
        await db_session.commit()

        assert alert.id is not None
        assert alert.status == AlertStatus.PENDING
        assert alert.risk_score == 85
        assert alert.reference_number.startswith("FRD-")

    async def test_review_alert_valid_transition(self, db_session):
        txn = await self._create_transaction(db_session)
        repo = AlertRepository(db_session)

        alert = await repo.create(
            transaction_id=txn.id,
            customer_id=txn.customer_id,
            risk_score=70,
            decision=Decision.REVIEW,
            triggered_rules=[],
        )
        await db_session.flush()

        reviewer = ReviewerInfo(id="user-001", username="analyst")
        reviewed = await repo.review(
            str(alert.id),
            status=AlertStatus.CONFIRMED,
            reviewer=reviewer,
            notes="Verified as fraud",
        )
        await db_session.commit()

        assert reviewed is not None
        assert reviewed.status == AlertStatus.CONFIRMED
        assert reviewed.reviewed_by == "user-001"
        assert reviewed.review_notes == "Verified as fraud"

    async def test_review_alert_invalid_transition_raises(self, db_session):
        txn = await self._create_transaction(db_session)
        repo = AlertRepository(db_session)

        alert = await repo.create(
            transaction_id=txn.id,
            customer_id=txn.customer_id,
            risk_score=95,
            decision=Decision.FLAG,
            triggered_rules=[],
        )
        await db_session.flush()

        # First confirm the alert
        reviewer = ReviewerInfo(id="user-001", username="admin")
        await repo.review(str(alert.id), status=AlertStatus.CONFIRMED, reviewer=reviewer)
        await db_session.flush()

        # Try to transition from CONFIRMED (terminal) — should fail
        with pytest.raises(ValueError, match="Cannot transition"):
            await repo.review(str(alert.id), status=AlertStatus.DISMISSED, reviewer=reviewer)

    async def test_get_stats(self, db_session):
        txn = await self._create_transaction(db_session)
        repo = AlertRepository(db_session)

        await repo.create(
            transaction_id=txn.id,
            customer_id=txn.customer_id,
            risk_score=80,
            decision=Decision.FLAG,
            triggered_rules=[],
        )
        await db_session.commit()

        stats = await repo.get_stats()

        assert stats["total"] >= 1
        assert isinstance(stats["by_status"], dict)
        assert isinstance(stats["average_score"], float)
