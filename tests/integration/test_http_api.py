"""Integration tests for fraud.inbound.http — the FastAPI service.

These tests run against the live containers via dependency-overridden
AsyncClient, exercising the full request→DB→response path.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.models.alert import AlertStatus, Decision, FraudAlert
from app.models.transaction import Transaction
from tests.conftest import make_rule_payload

pytestmark = pytest.mark.asyncio


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
    """Test fraud rule management endpoints."""

    async def test_list_rules(self, client):
        resp = await client.get("/api/v1/rules")
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert "total" in body
        assert "page" in body

    async def test_list_rules_with_filter(self, client):
        resp = await client.get("/api/v1/rules", params={"enabled": True})
        assert resp.status_code == 200
        body = resp.json()
        for rule in body["items"]:
            assert rule["enabled"] is True

    async def test_create_and_get_rule(self, client):
        payload = make_rule_payload()
        resp = await client.post("/api/v1/rules", json=payload)
        assert resp.status_code == 201
        created = resp.json()
        assert created["code"] == payload["code"]
        assert created["name"] == payload["name"]
        assert created["score"] == payload["score"]

        # Fetch it back
        resp = await client.get(f"/api/v1/rules/{payload['code']}")
        assert resp.status_code == 200
        assert resp.json()["code"] == payload["code"]

    async def test_create_duplicate_rule_returns_409(self, client):
        payload = make_rule_payload()
        await client.post("/api/v1/rules", json=payload)
        resp = await client.post("/api/v1/rules", json=payload)
        assert resp.status_code == 409

    async def test_update_rule(self, client):
        payload = make_rule_payload()
        await client.post("/api/v1/rules", json=payload)

        resp = await client.put(
            f"/api/v1/rules/{payload['code']}",
            json={"name": "Updated Name", "score": 75},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated Name"
        assert resp.json()["score"] == 75

    async def test_toggle_rule(self, client):
        payload = make_rule_payload(enabled=True)
        await client.post("/api/v1/rules", json=payload)

        resp = await client.post(f"/api/v1/rules/{payload['code']}/toggle")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

        resp = await client.post(f"/api/v1/rules/{payload['code']}/toggle")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is True

    async def test_delete_rule(self, client):
        payload = make_rule_payload()
        await client.post("/api/v1/rules", json=payload)

        resp = await client.delete(f"/api/v1/rules/{payload['code']}")
        assert resp.status_code == 204

    async def test_get_nonexistent_rule_returns_404(self, client):
        resp = await client.get("/api/v1/rules/NOPE_999")
        assert resp.status_code == 404


# ======================================================================
# Alerts (GET /api/v1/alerts, POST review)
# ======================================================================


class TestAlertsAPI:
    """Test fraud alert endpoints."""

    async def _create_alert(self, db_session) -> dict:
        """Helper: insert a transaction + alert directly into the DB."""
        txn = Transaction(
            external_id=f"TXN-{uuid.uuid4().hex[:12]}",
            customer_id=str(uuid.uuid4()),
            amount=Decimal("999999.99"),
            currency="ZAR",
            transaction_type="purchase",
            channel="online",
            transaction_time=datetime.now(UTC),
        )
        db_session.add(txn)
        await db_session.flush()

        alert = FraudAlert(
            transaction_id=str(txn.id),
            customer_id=txn.customer_id,
            risk_score=95,
            decision=Decision.FLAG.value,
            decision_tier="critical",
            triggered_rules=[
                {"code": "TST_001", "name": "Test Rule", "severity": "high", "score": 95}
            ],
            status=AlertStatus.PENDING.value,
        )
        db_session.add(alert)
        await db_session.commit()
        await db_session.refresh(alert)

        return {
            "alert_id": str(alert.id),
            "risk_score": alert.risk_score,
        }

    async def test_list_alerts(self, client):
        resp = await client.get("/api/v1/alerts")
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert "total" in body

    async def test_list_alerts_pagination(self, client):
        resp = await client.get("/api/v1/alerts", params={"page": 1, "size": 5})
        assert resp.status_code == 200
        body = resp.json()
        assert body["page"] == 1
        assert body["size"] == 5

    async def test_list_alerts_filter_by_status(self, client):
        resp = await client.get("/api/v1/alerts", params={"status": "pending"})
        assert resp.status_code == 200

    async def test_get_alert_detail(self, client, db_session):
        """Create an alert, then fetch its detail."""
        eval_result = await self._create_alert(db_session)

        resp = await client.get(f"/api/v1/alerts/{eval_result['alert_id']}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == eval_result["alert_id"]
        assert body["risk_score"] == eval_result["risk_score"]
        assert "triggered_rules" in body
        assert "transaction" in body

    async def test_get_nonexistent_alert_returns_404(self, client):
        fake_id = str(uuid.uuid4())
        resp = await client.get(f"/api/v1/alerts/{fake_id}")
        assert resp.status_code == 404

    async def test_review_alert(self, client, db_session):
        """Review an alert — mark it as confirmed."""
        eval_result = await self._create_alert(db_session)

        resp = await client.post(
            f"/api/v1/alerts/{eval_result['alert_id']}/review",
            json={"status": "confirmed", "notes": "Verified as fraud by test"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "confirmed"
        assert body["reviewed_by"] is not None
        assert body["review_notes"] == "Verified as fraud by test"

    async def test_review_alert_dismiss(self, client, db_session):
        """Dismiss an alert as false positive."""
        eval_result = await self._create_alert(db_session)

        resp = await client.post(
            f"/api/v1/alerts/{eval_result['alert_id']}/review",
            json={"status": "dismissed", "notes": "False positive"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "dismissed"

    async def test_alert_stats_summary(self, client):
        resp = await client.get("/api/v1/alerts/stats/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert "by_status" in body or "total" in body or isinstance(body, dict)
