"""Integration tests for authentication and authorisation.

Token issuance (login/refresh) and user profile (/me) live in core-banking.
This service only performs *stateless JWT validation* and role-based access
control.  These tests verify RBAC enforcement on fraud-detection endpoints.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi_pagination import Page

from app.main import app
from app.providers import get_alert_service, get_rule_service
from app.services.alert_service import AlertService
from app.services.rule_service import RuleService
from tests.conftest import _auth_headers, _make_rule_model, make_rule_payload

pytestmark = pytest.mark.asyncio


# ======================================================================
# Helper
# ======================================================================


def _make_headers(role: str, username: str) -> dict[str, str]:
    """Shorthand for creating an Authorization header."""
    return _auth_headers(role, username)


def _mock_rule_service() -> RuleService:
    svc = RuleService.__new__(RuleService)
    svc._repo = AsyncMock()
    return svc


def _mock_alert_service() -> AlertService:
    svc = AlertService.__new__(AlertService)
    svc._repo = AsyncMock()
    return svc


# ======================================================================
# RBAC — Role-Based Access Control
# ======================================================================


class TestRBACEnforcement:
    """Tests that endpoints enforce correct role-based permissions."""

    @patch("app.api.v1.endpoints.alerts.sqlalchemy_paginate", new_callable=AsyncMock)
    async def test_viewer_can_list_alerts(self, mock_paginate, client):
        """Viewers should be able to read alerts."""
        mock_paginate.return_value = Page.model_validate(
            {"items": [], "total": 0, "page": 1, "size": 50, "pages": 0}
        )
        headers = _make_headers("viewer", "viewer")
        svc = _mock_alert_service()
        app.dependency_overrides[get_alert_service] = lambda: svc
        try:
            resp = await client.get("/api/v1/alerts", headers=headers)
        finally:
            app.dependency_overrides.pop(get_alert_service, None)
        assert resp.status_code == 200

    @patch("app.api.v1.endpoints.rules.sqlalchemy_paginate", new_callable=AsyncMock)
    async def test_viewer_can_list_rules(self, mock_paginate, client):
        """Viewers should be able to read rules."""
        mock_paginate.return_value = Page.model_validate(
            {"items": [], "total": 0, "page": 1, "size": 50, "pages": 0}
        )
        headers = _make_headers("viewer", "viewer")
        svc = _mock_rule_service()
        app.dependency_overrides[get_rule_service] = lambda: svc
        try:
            resp = await client.get("/api/v1/rules", headers=headers)
        finally:
            app.dependency_overrides.pop(get_rule_service, None)
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
        svc = _mock_rule_service()
        svc._repo.create = AsyncMock(return_value=created)
        app.dependency_overrides[get_rule_service] = lambda: svc
        try:
            resp = await client.post("/api/v1/rules", json=payload, headers=headers)
        finally:
            app.dependency_overrides.pop(get_rule_service, None)
        assert resp.status_code == 201

    async def test_unauthenticated_cannot_access_rules(self, client):
        """Unauthenticated requests should get 401."""
        resp = await client.get("/api/v1/rules")
        assert resp.status_code == 401

    async def test_unauthenticated_cannot_access_alerts(self, client):
        """Unauthenticated requests should get 401."""
        resp = await client.get("/api/v1/alerts")
        assert resp.status_code == 401
