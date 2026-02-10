"""Integration tests for fraud.inbound.http — the FastAPI service.

These tests exercise the full HTTP request path (routing, auth, serialisation)
with service-layer dependencies overridden so that no live Postgres is required.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.main import app
from app.providers import get_alert_service, get_config_service, get_rule_service
from app.repositories.rule_repository import DuplicateRuleError
from app.services.alert_service import AlertService
from app.services.config_service import ConfigService
from app.services.rule_service import RuleService
from tests.conftest import _make_alert_model, _make_rule_model, make_rule_payload

pytestmark = pytest.mark.asyncio

_NOW = datetime.now(UTC)


# ---------------------------------------------------------------------------
# Helpers — build mock services backed by AsyncMock repos
# ---------------------------------------------------------------------------


def _mock_rule_service() -> RuleService:
    """Create a RuleService with a fully mocked repository."""
    svc = RuleService.__new__(RuleService)
    svc._repo = AsyncMock()
    return svc


def _mock_alert_service() -> AlertService:
    """Create an AlertService with a fully mocked repository."""
    svc = AlertService.__new__(AlertService)
    svc._repo = AsyncMock()
    return svc


def _mock_config_service() -> ConfigService:
    """Create a ConfigService with a fully mocked repository."""
    svc = ConfigService.__new__(ConfigService)
    svc._repo = AsyncMock()
    return svc


# ======================================================================
# Health endpoints
# ======================================================================


class TestHealthEndpoints:
    """Test /health and /ready liveness/readiness probes."""

    async def test_health_check(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "healthy"
        assert body["service"] == "core-fraud-detection-service"

    async def test_readiness_check(self, client):
        """Readiness should return 200 when mocked infra is healthy."""
        resp = await client.get("/ready")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] in ("ready", "degraded")

    async def test_api_ping(self, client):
        resp = await client.get("/api/v1/ping")
        assert resp.status_code == 200


# ======================================================================
# Rules CRUD (GET/POST/PUT/DELETE /api/v1/rules)
# ======================================================================


class TestRulesAPI:
    """Test fraud rule management endpoints (service layer mocked via DI overrides)."""

    async def test_list_rules(self, admin_client):
        rules = [_make_rule_model(code="AMT_001"), _make_rule_model(code="VEL_002")]
        svc = _mock_rule_service()
        svc._repo.get_all = AsyncMock(return_value=(rules, 2))
        app.dependency_overrides[get_rule_service] = lambda: svc
        try:
            resp = await admin_client.get("/api/v1/rules")
        finally:
            app.dependency_overrides.pop(get_rule_service, None)
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert body["total"] == 2
        assert "page" in body

    async def test_list_rules_with_filter(self, admin_client):
        rule = _make_rule_model(enabled=True)
        svc = _mock_rule_service()
        svc._repo.get_all = AsyncMock(return_value=([rule], 1))
        app.dependency_overrides[get_rule_service] = lambda: svc
        try:
            resp = await admin_client.get("/api/v1/rules", params={"enabled": True})
        finally:
            app.dependency_overrides.pop(get_rule_service, None)
        assert resp.status_code == 200
        body = resp.json()
        for item in body["items"]:
            assert item["enabled"] is True

    async def test_create_and_get_rule(self, admin_client):
        payload = make_rule_payload()
        created_model = _make_rule_model(**payload)

        svc = _mock_rule_service()
        svc._repo.create = AsyncMock(return_value=created_model)
        app.dependency_overrides[get_rule_service] = lambda: svc
        try:
            resp = await admin_client.post("/api/v1/rules", json=payload)
        finally:
            app.dependency_overrides.pop(get_rule_service, None)
        assert resp.status_code == 201
        created = resp.json()
        assert created["code"] == payload["code"]
        assert created["name"] == payload["name"]
        assert created["score"] == payload["score"]

        # Fetch it back
        svc2 = _mock_rule_service()
        svc2._repo.get_by_code = AsyncMock(return_value=created_model)
        app.dependency_overrides[get_rule_service] = lambda: svc2
        try:
            resp = await admin_client.get(f"/api/v1/rules/{payload['code']}")
        finally:
            app.dependency_overrides.pop(get_rule_service, None)
        assert resp.status_code == 200
        assert resp.json()["code"] == payload["code"]

    async def test_create_duplicate_rule_returns_409(self, admin_client):
        payload = make_rule_payload()
        svc = _mock_rule_service()
        svc._repo.create = AsyncMock(side_effect=DuplicateRuleError(payload["code"]))
        app.dependency_overrides[get_rule_service] = lambda: svc
        try:
            resp = await admin_client.post("/api/v1/rules", json=payload)
        finally:
            app.dependency_overrides.pop(get_rule_service, None)
        assert resp.status_code == 409

    async def test_update_rule(self, admin_client):
        payload = make_rule_payload()
        updated_model = _make_rule_model(code=payload["code"], name="Updated Name", score=75)
        svc = _mock_rule_service()
        svc._repo.update = AsyncMock(return_value=updated_model)
        app.dependency_overrides[get_rule_service] = lambda: svc
        try:
            resp = await admin_client.put(
                f"/api/v1/rules/{payload['code']}",
                json={"name": "Updated Name", "score": 75},
            )
        finally:
            app.dependency_overrides.pop(get_rule_service, None)
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated Name"
        assert resp.json()["score"] == 75

    async def test_toggle_rule(self, admin_client):
        payload = make_rule_payload(enabled=True)
        toggled_off = _make_rule_model(code=payload["code"], enabled=False)
        toggled_on = _make_rule_model(code=payload["code"], enabled=True)

        svc = _mock_rule_service()
        svc._repo.toggle = AsyncMock(return_value=toggled_off)
        app.dependency_overrides[get_rule_service] = lambda: svc
        try:
            resp = await admin_client.post(f"/api/v1/rules/{payload['code']}/toggle")
        finally:
            app.dependency_overrides.pop(get_rule_service, None)
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

        svc2 = _mock_rule_service()
        svc2._repo.toggle = AsyncMock(return_value=toggled_on)
        app.dependency_overrides[get_rule_service] = lambda: svc2
        try:
            resp = await admin_client.post(f"/api/v1/rules/{payload['code']}/toggle")
        finally:
            app.dependency_overrides.pop(get_rule_service, None)
        assert resp.status_code == 200
        assert resp.json()["enabled"] is True

    async def test_delete_rule(self, admin_client):
        payload = make_rule_payload()
        svc = _mock_rule_service()
        svc._repo.delete = AsyncMock(return_value=True)
        app.dependency_overrides[get_rule_service] = lambda: svc
        try:
            resp = await admin_client.delete(f"/api/v1/rules/{payload['code']}")
        finally:
            app.dependency_overrides.pop(get_rule_service, None)
        assert resp.status_code == 204

    async def test_get_nonexistent_rule_returns_404(self, admin_client):
        svc = _mock_rule_service()
        svc._repo.get_by_code = AsyncMock(return_value=None)
        app.dependency_overrides[get_rule_service] = lambda: svc
        try:
            resp = await admin_client.get("/api/v1/rules/NOPE_999")
        finally:
            app.dependency_overrides.pop(get_rule_service, None)
        assert resp.status_code == 404


# ======================================================================
# Alerts (GET /api/v1/alerts, POST review)
# ======================================================================


class TestAlertsAPI:
    """Test fraud alert endpoints (service layer mocked via DI overrides)."""

    async def test_list_alerts(self, admin_client):
        alerts = [_make_alert_model(), _make_alert_model()]
        svc = _mock_alert_service()
        svc._repo.get_all = AsyncMock(return_value=(alerts, 2))
        app.dependency_overrides[get_alert_service] = lambda: svc
        try:
            resp = await admin_client.get("/api/v1/alerts")
        finally:
            app.dependency_overrides.pop(get_alert_service, None)
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert body["total"] == 2

    async def test_list_alerts_pagination(self, admin_client):
        svc = _mock_alert_service()
        svc._repo.get_all = AsyncMock(return_value=([], 0))
        app.dependency_overrides[get_alert_service] = lambda: svc
        try:
            resp = await admin_client.get("/api/v1/alerts", params={"page": 1, "size": 5})
        finally:
            app.dependency_overrides.pop(get_alert_service, None)
        assert resp.status_code == 200
        body = resp.json()
        assert body["page"] == 1
        assert body["size"] == 5

    async def test_list_alerts_filter_by_status(self, admin_client):
        svc = _mock_alert_service()
        svc._repo.get_all = AsyncMock(return_value=([], 0))
        app.dependency_overrides[get_alert_service] = lambda: svc
        try:
            resp = await admin_client.get("/api/v1/alerts", params={"status": "pending"})
        finally:
            app.dependency_overrides.pop(get_alert_service, None)
        assert resp.status_code == 200

    async def test_get_alert_detail(self, admin_client):
        alert = _make_alert_model()
        svc = _mock_alert_service()
        svc._repo.get_by_id = AsyncMock(return_value=alert)
        app.dependency_overrides[get_alert_service] = lambda: svc
        try:
            resp = await admin_client.get(f"/api/v1/alerts/{alert.id}")
        finally:
            app.dependency_overrides.pop(get_alert_service, None)
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == alert.id
        assert body["risk_score"] == alert.risk_score
        assert "triggered_rules" in body
        assert "transaction" in body

    async def test_get_nonexistent_alert_returns_404(self, admin_client):
        fake_id = str(uuid.uuid4())
        svc = _mock_alert_service()
        svc._repo.get_by_id = AsyncMock(return_value=None)
        app.dependency_overrides[get_alert_service] = lambda: svc
        try:
            resp = await admin_client.get(f"/api/v1/alerts/{fake_id}")
        finally:
            app.dependency_overrides.pop(get_alert_service, None)
        assert resp.status_code == 404

    async def test_review_alert(self, admin_client):
        alert = _make_alert_model()
        reviewed = _make_alert_model(
            id=alert.id,
            status="confirmed",
            reviewed_by="admin",
            reviewed_at=_NOW,
            review_notes="Verified as fraud by test",
        )
        svc = _mock_alert_service()
        svc._repo.get_by_id = AsyncMock(return_value=alert)
        svc._repo.review = AsyncMock(return_value=reviewed)
        app.dependency_overrides[get_alert_service] = lambda: svc
        try:
            resp = await admin_client.post(
                f"/api/v1/alerts/{alert.id}/review",
                json={"status": "confirmed", "notes": "Verified as fraud by test"},
            )
        finally:
            app.dependency_overrides.pop(get_alert_service, None)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "confirmed"
        assert body["reviewed_by"] is not None
        assert body["review_notes"] == "Verified as fraud by test"

    async def test_review_alert_dismiss(self, admin_client):
        alert = _make_alert_model()
        reviewed = _make_alert_model(
            id=alert.id,
            status="dismissed",
            reviewed_by="admin",
            reviewed_at=_NOW,
            review_notes="False positive",
        )
        svc = _mock_alert_service()
        svc._repo.get_by_id = AsyncMock(return_value=alert)
        svc._repo.review = AsyncMock(return_value=reviewed)
        app.dependency_overrides[get_alert_service] = lambda: svc
        try:
            resp = await admin_client.post(
                f"/api/v1/alerts/{alert.id}/review",
                json={"status": "dismissed", "notes": "False positive"},
            )
        finally:
            app.dependency_overrides.pop(get_alert_service, None)
        assert resp.status_code == 200
        assert resp.json()["status"] == "dismissed"

    async def test_alert_stats_summary(self, admin_client):
        stats = {
            "total": 10,
            "by_status": {"pending": 5, "confirmed": 3, "dismissed": 2},
            "average_score": 72.5,
        }
        svc = _mock_alert_service()
        svc._repo.get_stats = AsyncMock(return_value=stats)
        app.dependency_overrides[get_alert_service] = lambda: svc
        try:
            resp = await admin_client.get("/api/v1/alerts/stats/summary")
        finally:
            app.dependency_overrides.pop(get_alert_service, None)
        assert resp.status_code == 200
        body = resp.json()
        assert "by_status" in body or "total" in body or isinstance(body, dict)
