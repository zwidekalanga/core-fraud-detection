"""Shared test fixtures for fraud-detection service."""

import os

# Set test JWT secret before any app imports trigger Settings() validation.
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-for-unit-tests")

import uuid  # noqa: E402
from collections.abc import AsyncGenerator  # noqa: E402
from datetime import UTC, datetime  # noqa: E402
from decimal import Decimal  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import AsyncMock, MagicMock  # noqa: E402

import fakeredis.aioredis  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.main import app  # noqa: E402
from app.models.alert import AlertStatus, Decision  # noqa: E402
from tests.helpers.token_factory import create_access_token

# ---------------------------------------------------------------------------
# Fake Redis (drop-in async replacement)
# ---------------------------------------------------------------------------


def _make_fake_redis():
    """Create a fakeredis instance that behaves like redis.asyncio.Redis."""
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


# ---------------------------------------------------------------------------
# Mock DB session
# ---------------------------------------------------------------------------


def _make_mock_session():
    """Create a mock async DB session.

    The mock supports ``async with factory() as session`` used by
    ``get_db_session`` and by the readiness probe (``SELECT 1``).
    """
    session = AsyncMock()
    # session.execute returns a result whose .scalar() works
    result_mock = MagicMock()
    result_mock.scalar.return_value = 1
    session.execute.return_value = result_mock
    session.close = AsyncMock()
    return session


def _make_mock_session_factory():
    """Return a callable that mimics ``async_sessionmaker().__call__()``."""
    mock_session = _make_mock_session()
    factory = MagicMock()
    ctx = AsyncMock()
    ctx.__aenter__.return_value = mock_session
    factory.return_value = ctx
    return factory, mock_session


# ---------------------------------------------------------------------------
# HTTP client fixture (FastAPI app with mocked infra)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client wired to the FastAPI app.

    Infrastructure (DB, Redis) is mocked so tests run without devstack.
    """
    session_factory, _ = _make_mock_session_factory()
    fake_redis = _make_fake_redis()

    app.state.engine = MagicMock()
    app.state.session_factory = session_factory
    app.state.redis = fake_redis

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    await fake_redis.aclose()


# ---------------------------------------------------------------------------
# Mock DB session fixture (for tests that manipulate the session directly)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def db_session():
    """Provide a mock database session for integration tests."""
    return _make_mock_session()


# ---------------------------------------------------------------------------
# Redis fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def redis_client():
    """Provide a fake Redis client."""
    client = _make_fake_redis()
    yield client
    await client.aclose()


# ---------------------------------------------------------------------------
# Auth helpers — generate JWT tokens directly (no login endpoint needed)
# ---------------------------------------------------------------------------


def _make_token(role: str, username: str) -> str:
    """Create a valid JWT access token for testing."""
    return create_access_token(
        user_id=str(uuid.uuid4()),
        role=role,
        username=username,
        email=f"{username}@test.capitec.co.za",
    )


def _auth_headers(role: str, username: str) -> dict[str, str]:
    """Return Authorization header dict with a valid JWT."""
    token = _make_token(role, username)
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture()
async def admin_client(client) -> AsyncGenerator[AsyncClient, None]:
    """HTTP client pre-authenticated as admin user."""
    client.headers.update(_auth_headers("admin", "admin"))
    yield client
    client.headers.pop("Authorization", None)


@pytest_asyncio.fixture()
async def analyst_client(client) -> AsyncGenerator[AsyncClient, None]:
    """HTTP client pre-authenticated as analyst user."""
    client.headers.update(_auth_headers("analyst", "analyst"))
    yield client
    client.headers.pop("Authorization", None)


@pytest_asyncio.fixture()
async def viewer_client(client) -> AsyncGenerator[AsyncClient, None]:
    """HTTP client pre-authenticated as viewer user."""
    client.headers.update(_auth_headers("viewer", "viewer"))
    yield client
    client.headers.pop("Authorization", None)


# ---------------------------------------------------------------------------
# Mock ORM model factories
# ---------------------------------------------------------------------------

_NOW = datetime.now(UTC)


def _make_rule_model(**overrides):
    """Return a SimpleNamespace that looks like a FraudRule ORM instance."""
    data = {
        "code": f"TST_{uuid.uuid4().int % 1000:03d}",
        "name": "Test Rule",
        "description": "A rule for testing",
        "category": "amount",
        "severity": "medium",
        "score": 25,
        "enabled": True,
        "conditions": {"field": "amount", "operator": "greater_than", "value": 50000},
        "effective_from": None,
        "effective_to": None,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _make_transaction_model(**overrides):
    """Return a SimpleNamespace that looks like a Transaction ORM instance."""
    data = {
        "id": str(uuid.uuid4()),
        "external_id": f"TXN-{uuid.uuid4().hex[:12]}",
        "customer_id": str(uuid.uuid4()),
        "amount": Decimal("999999.99"),
        "currency": "ZAR",
        "transaction_type": "purchase",
        "channel": "online",
        "merchant_name": "Test Store",
        "location_country": "ZA",
        "transaction_time": _NOW,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _make_alert_model(**overrides):
    """Return a SimpleNamespace that looks like a FraudAlert ORM instance."""
    txn = _make_transaction_model()
    data = {
        "id": str(uuid.uuid4()),
        "transaction_id": txn.id,
        "customer_id": txn.customer_id,
        "risk_score": 85,
        "decision": Decision.FLAG,
        "decision_tier": "high",
        "decision_tier_description": "High risk",
        "status": AlertStatus.PENDING,
        "triggered_rules": [
            {
                "code": "AMT_001",
                "name": "High Amount",
                "category": "amount",
                "severity": "high",
                "score": 85,
            }
        ],
        "processing_time_ms": 12.5,
        "reference_number": None,
        "reviewed_by": None,
        "reviewed_by_username": None,
        "reviewed_at": None,
        "review_notes": None,
        "created_at": _NOW,
        "updated_at": _NOW,
        "transaction": txn,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


# ---------------------------------------------------------------------------
# Factory helpers (payload dicts for HTTP requests)
# ---------------------------------------------------------------------------


def make_transaction(**overrides) -> dict:
    """Build a valid transaction payload with sensible defaults."""
    data = {
        "external_id": f"TXN-{uuid.uuid4().hex[:12]}",
        "customer_id": str(uuid.uuid4()),
        "amount": "150.00",
        "currency": "ZAR",
        "transaction_type": "purchase",
        "channel": "online",
        "merchant_id": "MERCH-001",
        "merchant_name": "Test Store",
        "merchant_category": "retail",
        "location_country": "ZA",
        "location_city": "Cape Town",
        "device_fingerprint": "fp-abc123",
        "ip_address": "41.0.0.1",
    }
    data.update(overrides)
    return data


def make_high_risk_transaction(**overrides) -> dict:
    """Build a transaction likely to trigger fraud rules (high amount, risky country)."""
    data = make_transaction(
        amount="999999.99",
        location_country="KP",
        merchant_category="gambling",
        device_fingerprint=None,
        channel="online",
    )
    data.update(overrides)
    return data


def make_rule_payload(**overrides) -> dict:
    """Build a valid rule creation payload."""
    seq = str(uuid.uuid4().int)[:3]
    code = f"TST_{seq}"
    data = {
        "code": code,
        "name": "Test Rule",
        "description": "A rule for testing",
        "category": "amount",
        "severity": "medium",
        "score": 25,
        "enabled": True,
        "conditions": {
            "field": "amount",
            "operator": "greater_than",
            "value": 50000,
        },
    }
    data.update(overrides)
    return data
