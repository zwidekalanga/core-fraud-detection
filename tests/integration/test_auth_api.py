"""Integration tests for authentication and authorisation.

Token issuance (login/refresh) lives in core-banking.  This service only
performs *stateless JWT validation* and role-based access control.
These tests verify the /me endpoint, token validation, and RBAC enforcement.
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import _auth_headers, _make_rule_model, make_rule_payload
from tests.helpers.token_factory import create_refresh_token

pytestmark = pytest.mark.asyncio


# ======================================================================
# Helper
# ======================================================================


def _make_headers(role: str, username: str) -> dict[str, str]:
    """Shorthand for creating an Authorization header."""
    return _auth_headers(role, username)


# ======================================================================
# GET /api/v1/auth/me — token introspection
# ======================================================================


class TestMeEndpoint:
    """Tests for the /me endpoint (the only auth endpoint on this service)."""

    async def test_get_me_admin(self, client):
        headers = _make_headers("admin", "admin")
        resp = await client.get("/api/v1/auth/me", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["username"] == "admin"
        assert body["role"] == "admin"

    async def test_get_me_analyst(self, client):
        headers = _make_headers("analyst", "analyst")
        resp = await client.get("/api/v1/auth/me", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["role"] == "analyst"

    async def test_get_me_viewer(self, client):
        headers = _make_headers("viewer", "viewer")
        resp = await client.get("/api/v1/auth/me", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["role"] == "viewer"

    async def test_get_me_without_token(self, client):
        resp = await client.get("/api/v1/auth/me")
        assert resp.status_code == 401

    async def test_get_me_with_invalid_token(self, client):
        headers = {"Authorization": "Bearer invalid.jwt.token"}
        resp = await client.get("/api/v1/auth/me", headers=headers)
        assert resp.status_code == 401

    async def test_get_me_with_refresh_token_rejected(self, client):
        """Using a refresh token (type=refresh) as an access token should fail."""
        refresh = create_refresh_token(
            user_id=str(uuid.uuid4()),
            role="admin",
            username="admin",
        )
        headers = {"Authorization": f"Bearer {refresh}"}
        resp = await client.get("/api/v1/auth/me", headers=headers)
        assert resp.status_code == 401


# ======================================================================
# RBAC — Role-Based Access Control
# ======================================================================


class TestRBACEnforcement:
    """Tests that endpoints enforce correct role-based permissions."""

    async def test_viewer_can_list_alerts(self, client):
        """Viewers should be able to read alerts."""
        headers = _make_headers("viewer", "viewer")
        with patch("app.api.v1.alerts.AlertRepository") as MockRepo:
            MockRepo.return_value.get_all = AsyncMock(return_value=([], 0))
            resp = await client.get("/api/v1/alerts", headers=headers)
        assert resp.status_code == 200

    async def test_viewer_can_list_rules(self, client):
        """Viewers should be able to read rules."""
        headers = _make_headers("viewer", "viewer")
        with patch("app.api.v1.rules.RuleRepository") as MockRepo:
            MockRepo.return_value.get_all = AsyncMock(return_value=([], 0))
            resp = await client.get("/api/v1/rules", headers=headers)
        assert resp.status_code == 200

    async def test_analyst_cannot_create_rule(self, client):
        """Analysts should NOT be able to create rules (admin only)."""
        headers = _make_headers("analyst", "analyst")
        payload = make_rule_payload()
        resp = await client.post("/api/v1/rules", json=payload, headers=headers)
        assert resp.status_code == 403

    async def test_viewer_cannot_create_rule(self, client):
        """Viewers should NOT be able to create rules."""
        headers = _make_headers("viewer", "viewer")
        payload = make_rule_payload()
        resp = await client.post("/api/v1/rules", json=payload, headers=headers)
        assert resp.status_code == 403

    async def test_admin_can_create_rule(self, client):
        """Admins should be able to create rules."""
        headers = _make_headers("admin", "admin")
        payload = make_rule_payload()
        created = _make_rule_model(**payload)
        with patch("app.api.v1.rules.RuleRepository") as MockRepo:
            repo = MockRepo.return_value
            repo.get_by_code = AsyncMock(return_value=None)
            repo.create = AsyncMock(return_value=created)
            resp = await client.post("/api/v1/rules", json=payload, headers=headers)
        assert resp.status_code == 201

    async def test_unauthenticated_cannot_access_rules(self, client):
        """Unauthenticated requests should get 401."""
        resp = await client.get("/api/v1/rules")
        assert resp.status_code == 401

    async def test_unauthenticated_cannot_access_alerts(self, client):
        """Unauthenticated requests should get 401."""
        resp = await client.get("/api/v1/alerts")
        assert resp.status_code == 401
