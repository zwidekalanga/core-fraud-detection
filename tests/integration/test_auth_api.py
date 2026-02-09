"""Integration tests for auth API endpoints.

These tests run against the live containers via the AsyncClient fixture,
exercising the full authentication flow: login, token refresh, and /me.
"""

import pytest

from tests.conftest import make_rule_payload

pytestmark = pytest.mark.asyncio


# ======================================================================
# Helper: login and get tokens
# ======================================================================


async def _login(client, username: str, password: str):  # noqa: ANN001
    """Authenticate and return the httpx Response."""
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": username, "password": password},
    )
    return resp


async def _auth_header(
    client, username: str = "admin", password: str = "admin123"
) -> dict[str, str]:
    """Login and return Authorization header dict."""
    resp = await _login(client, username, password)
    tokens = resp.json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


# ======================================================================
# Login (POST /api/v1/auth/login)
# ======================================================================


class TestLoginEndpoint:
    """Tests for the login endpoint."""

    async def test_login_admin_success(self, client):
        resp = await _login(client, "admin", "admin123")
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert "refresh_token" in body
        assert body["token_type"] == "bearer"
        assert body["expires_in"] > 0

    async def test_login_analyst_success(self, client):
        resp = await _login(client, "analyst", "analyst123")
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    async def test_login_viewer_success(self, client):
        resp = await _login(client, "viewer", "viewer123")
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    async def test_login_wrong_password(self, client):
        resp = await _login(client, "admin", "wrongpassword")
        assert resp.status_code == 401
        assert "Invalid" in resp.json()["detail"]

    async def test_login_nonexistent_user(self, client):
        resp = await _login(client, "nobody", "password")
        assert resp.status_code == 401

    async def test_login_empty_credentials(self, client):
        resp = await client.post(
            "/api/v1/auth/login",
            data={"username": "", "password": ""},
        )
        assert resp.status_code in (401, 422)


# ======================================================================
# Get Current User (GET /api/v1/auth/me)
# ======================================================================


class TestMeEndpoint:
    """Tests for the /me endpoint."""

    async def test_get_me_admin(self, client):
        headers = await _auth_header(client, "admin", "admin123")
        resp = await client.get("/api/v1/auth/me", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["username"] == "admin"
        assert body["role"] == "admin"
        assert body["is_active"] is True

    async def test_get_me_analyst(self, client):
        headers = await _auth_header(client, "analyst", "analyst123")
        resp = await client.get("/api/v1/auth/me", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["role"] == "analyst"

    async def test_get_me_without_token(self, client):
        resp = await client.get("/api/v1/auth/me")
        assert resp.status_code == 401

    async def test_get_me_with_invalid_token(self, client):
        headers = {"Authorization": "Bearer invalid.jwt.token"}
        resp = await client.get("/api/v1/auth/me", headers=headers)
        assert resp.status_code == 401


# ======================================================================
# Token Refresh (POST /api/v1/auth/refresh)
# ======================================================================


class TestRefreshEndpoint:
    """Tests for the token refresh endpoint."""

    async def test_refresh_token_success(self, client):
        login_resp = await _login(client, "admin", "admin123")
        refresh_token = login_resp.json()["refresh_token"]

        resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert "refresh_token" in body
        assert body["token_type"] == "bearer"

    async def test_refresh_with_access_token_fails(self, client):
        """Using an access token as a refresh token should fail."""
        login_resp = await _login(client, "admin", "admin123")
        access_token = login_resp.json()["access_token"]

        resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": access_token},
        )
        assert resp.status_code == 401

    async def test_refresh_with_invalid_token(self, client):
        resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "not.a.valid.token"},
        )
        assert resp.status_code == 401


# ======================================================================
# RBAC — Role-Based Access Control
# ======================================================================


class TestRBACEnforcement:
    """Tests that endpoints enforce correct role-based permissions."""

    async def test_viewer_can_list_alerts(self, client):
        """Viewers should be able to read alerts."""
        headers = await _auth_header(client, "viewer", "viewer123")
        resp = await client.get("/api/v1/alerts", headers=headers)
        assert resp.status_code == 200

    async def test_viewer_can_list_rules(self, client):
        """Viewers should be able to read rules."""
        headers = await _auth_header(client, "viewer", "viewer123")
        resp = await client.get("/api/v1/rules", headers=headers)
        assert resp.status_code == 200

    async def test_analyst_cannot_create_rule(self, client):
        """Analysts should NOT be able to create rules (admin only)."""
        headers = await _auth_header(client, "analyst", "analyst123")
        payload = make_rule_payload()
        resp = await client.post("/api/v1/rules", json=payload, headers=headers)
        assert resp.status_code == 403

    async def test_viewer_cannot_create_rule(self, client):
        """Viewers should NOT be able to create rules."""
        headers = await _auth_header(client, "viewer", "viewer123")
        payload = make_rule_payload()
        resp = await client.post("/api/v1/rules", json=payload, headers=headers)
        assert resp.status_code == 403

    async def test_admin_can_create_rule(self, client):
        """Admins should be able to create rules."""
        headers = await _auth_header(client, "admin", "admin123")
        payload = make_rule_payload()
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
