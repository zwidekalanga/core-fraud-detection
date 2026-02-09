"""Shared test fixtures for fraud-detection service."""

import uuid
from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.main import app

# ---------------------------------------------------------------------------
# Authenticated client fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def admin_client(client) -> AsyncGenerator[AsyncClient, None]:
    """HTTP client pre-authenticated as admin user."""
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": "admin", "password": "admin123"},
    )
    if resp.status_code == 200:
        token = resp.json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"
    yield client
    client.headers.pop("Authorization", None)


@pytest_asyncio.fixture()
async def analyst_client(client) -> AsyncGenerator[AsyncClient, None]:
    """HTTP client pre-authenticated as analyst user."""
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": "analyst", "password": "analyst123"},
    )
    if resp.status_code == 200:
        token = resp.json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"
    yield client
    client.headers.pop("Authorization", None)


@pytest_asyncio.fixture()
async def viewer_client(client) -> AsyncGenerator[AsyncClient, None]:
    """HTTP client pre-authenticated as viewer user."""
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": "viewer", "password": "viewer123"},
    )
    if resp.status_code == 200:
        token = resp.json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"
    yield client
    client.headers.pop("Authorization", None)


# ---------------------------------------------------------------------------
# Database fixtures — fresh engine per test to avoid event loop conflicts
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide a database session for integration tests."""
    settings = get_settings()
    engine = create_async_engine(str(settings.database_url), echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


# ---------------------------------------------------------------------------
# Redis fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def redis_client() -> AsyncGenerator[Redis, None]:
    """Provide a Redis client connected to devstack Redis."""
    settings = get_settings()
    client = Redis.from_url(str(settings.redis_url), decode_responses=True)
    yield client
    await client.aclose()


# ---------------------------------------------------------------------------
# HTTP client fixture (FastAPI TestClient via httpx)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client wired to the FastAPI app.

    Each request gets its own DB session via the app's normal dependency
    injection — no overrides needed for integration tests against live DB.
    """
    # Reset any cached singleton connections from dependencies module
    import app.dependencies as deps

    deps._engine = None
    deps._session_factory = None
    deps._redis_client = None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    # Cleanup singletons after test
    if deps._engine is not None:
        await deps._engine.dispose()  # pyright: ignore[reportGeneralTypeIssues]
        deps._engine = None
    deps._session_factory = None
    if deps._redis_client is not None:
        await deps._redis_client.aclose()  # pyright: ignore[reportGeneralTypeIssues]
        deps._redis_client = None


# ---------------------------------------------------------------------------
# Factory helpers
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
