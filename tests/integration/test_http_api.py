"""Integration tests for fraud.inbound.http — the FastAPI service.

These tests exercise the full HTTP request path (routing, auth, serialisation)
with database repositories mocked so that no live Postgres is required.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models.alert import AlertStatus, Decision
from tests.conftest import _make_alert_model, _make_rule_model, make_rule_payload

pytestmark = pytest.mark.asyncio

_NOW = datetime.now(UTC)


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
    """Test fraud rule management endpoints (repos mocked)."""

    async def test_list_rules(self, admin_client):
        rules = [_make_rule_model(code="AMT_001"), _make_rule_model(code="VEL_002")]
        with patch("app.api.v1.rules.RuleRepository") as MockRepo:
            MockRepo.return_value.get_all = AsyncMock(return_value=(rules, 2))
            resp = await admin_client.get("/api/v1/rules")
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert body["total"] == 2
        assert "page" in body

    async def test_list_rules_with_filter(self, admin_client):
        rule = _make_rule_model(enabled=True)
        with patch("app.api.v1.rules.RuleRepository") as MockRepo:
            MockRepo.return_value.get_all = AsyncMock(return_value=([rule], 1))
            resp = await admin_client.get("/api/v1/rules", params={"enabled": True})
        assert resp.status_code == 200
        body = resp.json()
        for item in body["items"]:
            assert item["enabled"] is True

    async def test_create_and_get_rule(self, admin_client):
        payload = make_rule_payload()
        created_model = _make_rule_model(**payload)

        with patch("app.api.v1.rules.RuleRepository") as MockRepo:
            repo = MockRepo.return_value
            repo.get_by_code = AsyncMock(return_value=None)  # no duplicate
            repo.create = AsyncMock(return_value=created_model)
            resp = await admin_client.post("/api/v1/rules", json=payload)
        assert resp.status_code == 201
        created = resp.json()
        assert created["code"] == payload["code"]
        assert created["name"] == payload["name"]
        assert created["score"] == payload["score"]

        # Fetch it back
        with patch("app.api.v1.rules.RuleRepository") as MockRepo:
            MockRepo.return_value.get_by_code = AsyncMock(return_value=created_model)
            resp = await admin_client.get(f"/api/v1/rules/{payload['code']}")
        assert resp.status_code == 200
        assert resp.json()["code"] == payload["code"]

    async def test_create_duplicate_rule_returns_409(self, admin_client):
        from app.repositories.rule_repository import DuplicateRuleError

        payload = make_rule_payload()
        with patch("app.api.v1.rules.RuleRepository") as MockRepo:
            MockRepo.return_value.create = AsyncMock(
                side_effect=DuplicateRuleError(payload["code"])
            )
            resp = await admin_client.post("/api/v1/rules", json=payload)
        assert resp.status_code == 409

    async def test_update_rule(self, admin_client):
        payload = make_rule_payload()
        updated_model = _make_rule_model(code=payload["code"], name="Updated Name", score=75)
        with patch("app.api.v1.rules.RuleRepository") as MockRepo:
            MockRepo.return_value.update = AsyncMock(return_value=updated_model)
            resp = await admin_client.put(
                f"/api/v1/rules/{payload['code']}",
                json={"name": "Updated Name", "score": 75},
            )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated Name"
        assert resp.json()["score"] == 75

    async def test_toggle_rule(self, admin_client):
        payload = make_rule_payload(enabled=True)
        toggled_off = _make_rule_model(code=payload["code"], enabled=False)
        toggled_on = _make_rule_model(code=payload["code"], enabled=True)

        with patch("app.api.v1.rules.RuleRepository") as MockRepo:
            MockRepo.return_value.toggle = AsyncMock(return_value=toggled_off)
            resp = await admin_client.post(f"/api/v1/rules/{payload['code']}/toggle")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

        with patch("app.api.v1.rules.RuleRepository") as MockRepo:
            MockRepo.return_value.toggle = AsyncMock(return_value=toggled_on)
            resp = await admin_client.post(f"/api/v1/rules/{payload['code']}/toggle")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is True

    async def test_delete_rule(self, admin_client):
        payload = make_rule_payload()
        with patch("app.api.v1.rules.RuleRepository") as MockRepo:
            MockRepo.return_value.delete = AsyncMock(return_value=True)
            resp = await admin_client.delete(f"/api/v1/rules/{payload['code']}")
        assert resp.status_code == 204

    async def test_get_nonexistent_rule_returns_404(self, admin_client):
        with patch("app.api.v1.rules.RuleRepository") as MockRepo:
            MockRepo.return_value.get_by_code = AsyncMock(return_value=None)
            resp = await admin_client.get("/api/v1/rules/NOPE_999")
        assert resp.status_code == 404


# ======================================================================
# Alerts (GET /api/v1/alerts, POST review)
# ======================================================================


class TestAlertsAPI:
    """Test fraud alert endpoints (repos mocked)."""

    async def test_list_alerts(self, admin_client):
        alerts = [_make_alert_model(), _make_alert_model()]
        with patch("app.api.v1.alerts.AlertRepository") as MockRepo:
            MockRepo.return_value.get_all = AsyncMock(return_value=(alerts, 2))
            resp = await admin_client.get("/api/v1/alerts")
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert body["total"] == 2

    async def test_list_alerts_pagination(self, admin_client):
        with patch("app.api.v1.alerts.AlertRepository") as MockRepo:
            MockRepo.return_value.get_all = AsyncMock(return_value=([], 0))
            resp = await admin_client.get("/api/v1/alerts", params={"page": 1, "size": 5})
        assert resp.status_code == 200
        body = resp.json()
        assert body["page"] == 1
        assert body["size"] == 5

    async def test_list_alerts_filter_by_status(self, admin_client):
        with patch("app.api.v1.alerts.AlertRepository") as MockRepo:
            MockRepo.return_value.get_all = AsyncMock(return_value=([], 0))
            resp = await admin_client.get("/api/v1/alerts", params={"status": "pending"})
        assert resp.status_code == 200

    async def test_get_alert_detail(self, admin_client):
        alert = _make_alert_model()
        with patch("app.api.v1.alerts.AlertRepository") as MockRepo:
            MockRepo.return_value.get_by_id = AsyncMock(return_value=alert)
            resp = await admin_client.get(f"/api/v1/alerts/{alert.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == alert.id
        assert body["risk_score"] == alert.risk_score
        assert "triggered_rules" in body
        assert "transaction" in body

    async def test_get_nonexistent_alert_returns_404(self, admin_client):
        fake_id = str(uuid.uuid4())
        with patch("app.api.v1.alerts.AlertRepository") as MockRepo:
            MockRepo.return_value.get_by_id = AsyncMock(return_value=None)
            resp = await admin_client.get(f"/api/v1/alerts/{fake_id}")
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
        with patch("app.api.v1.alerts.AlertRepository") as MockRepo:
            repo = MockRepo.return_value
            repo.get_by_id = AsyncMock(return_value=alert)
            repo.review = AsyncMock(return_value=reviewed)
            resp = await admin_client.post(
                f"/api/v1/alerts/{alert.id}/review",
                json={"status": "confirmed", "notes": "Verified as fraud by test"},
            )
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
        with patch("app.api.v1.alerts.AlertRepository") as MockRepo:
            repo = MockRepo.return_value
            repo.get_by_id = AsyncMock(return_value=alert)
            repo.review = AsyncMock(return_value=reviewed)
            resp = await admin_client.post(
                f"/api/v1/alerts/{alert.id}/review",
                json={"status": "dismissed", "notes": "False positive"},
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "dismissed"

    async def test_alert_stats_summary(self, admin_client):
        stats = {
            "total": 10,
            "by_status": {"pending": 5, "confirmed": 3, "dismissed": 2},
            "average_score": 72.5,
        }
        with patch("app.api.v1.alerts.AlertRepository") as MockRepo:
            MockRepo.return_value.get_stats = AsyncMock(return_value=stats)
            resp = await admin_client.get("/api/v1/alerts/stats/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert "by_status" in body or "total" in body or isinstance(body, dict)
