"""Unit tests for dependencies.py — stateless JWT token validation and RBAC."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.security import SecurityScopes
from jose import jwt

from app.config import get_settings
from tests.helpers.token_factory import create_access_token, create_refresh_token


def _fake_request():
    """Return a minimal Request-like object with no Redis (deny-list skipped)."""
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=None)))


def _no_scopes():
    """SecurityScopes with no required scopes (any authenticated user)."""
    return SecurityScopes(scopes=[])


class TestTokenUser:
    """Tests for the TokenUser Pydantic model."""

    def test_valid_token_user(self):
        from app.schemas.auth import TokenUser

        user = TokenUser(id="user-123", username="admin", role="admin", email="a@b.com")
        assert user.id == "user-123"
        assert user.username == "admin"
        assert user.role == "admin"
        assert user.email == "a@b.com"

    def test_email_defaults_to_empty(self):
        from app.schemas.auth import TokenUser

        user = TokenUser(id="user-123", username="admin", role="admin")
        assert user.email == ""

    def test_missing_required_fields_raises(self):
        from pydantic import ValidationError

        from app.schemas.auth import TokenUser

        with pytest.raises(ValidationError):
            TokenUser(id="user-123")  # type: ignore[call-arg]

        with pytest.raises(ValidationError):
            TokenUser(username="admin", role="admin")  # type: ignore[call-arg]


class TestGetCurrentUser:
    """Tests for the stateless get_current_user dependency."""

    @pytest.fixture
    def settings(self):
        return get_settings()

    async def test_valid_access_token_returns_token_user(self, settings):  # noqa: ARG002
        from app.dependencies import get_current_user

        token = create_access_token("user-abc", "analyst", username="analyst", email="a@test.com")
        user = await get_current_user(_no_scopes(), _fake_request(), token)

        assert user.id == "user-abc"
        assert user.username == "analyst"
        assert user.role == "analyst"
        assert user.email == "a@test.com"

    async def test_valid_token_without_username_defaults_empty(self, settings):  # noqa: ARG002
        """Tokens issued before migration may lack username/email claims."""
        from app.dependencies import get_current_user

        # Create a token without username/email (old format)
        token = create_access_token("user-old", "viewer")
        user = await get_current_user(_no_scopes(), _fake_request(), token)

        assert user.id == "user-old"
        assert user.role == "viewer"
        assert user.username == ""
        assert user.email == ""

    async def test_expired_token_raises_401(self, settings):
        from app.dependencies import get_current_user

        payload = {
            "sub": "user-expired",
            "role": "admin",
            "type": "access",
            "exp": datetime.now(UTC) - timedelta(hours=1),
        }
        token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)

        with pytest.raises(HTTPException) as exc:
            await get_current_user(_no_scopes(), _fake_request(), token)
        assert exc.value.status_code == 401

    async def test_refresh_token_rejected_as_access(self, settings):  # noqa: ARG002
        """A refresh token must not be accepted by get_current_user."""
        from app.dependencies import get_current_user

        token = create_refresh_token("user-r", "admin")

        with pytest.raises(HTTPException) as exc:
            await get_current_user(_no_scopes(), _fake_request(), token)
        assert exc.value.status_code == 401

    async def test_tampered_token_raises_401(self, settings):  # noqa: ARG002
        from app.dependencies import get_current_user

        token = create_access_token("user-x", "admin")
        tampered = token[:-5] + "XXXXX"

        with pytest.raises(HTTPException) as exc:
            await get_current_user(_no_scopes(), _fake_request(), tampered)
        assert exc.value.status_code == 401

    async def test_invalid_token_string_raises_401(self):
        from app.dependencies import get_current_user

        with pytest.raises(HTTPException) as exc:
            await get_current_user(_no_scopes(), _fake_request(), "not.a.valid.jwt")
        assert exc.value.status_code == 401

    async def test_token_missing_sub_raises_401(self, settings):
        from app.dependencies import get_current_user

        payload = {
            "role": "admin",
            "type": "access",
            "exp": datetime.now(UTC) + timedelta(hours=1),
        }
        token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)

        with pytest.raises(HTTPException) as exc:
            await get_current_user(_no_scopes(), _fake_request(), token)
        assert exc.value.status_code == 401

    async def test_token_wrong_secret_raises_401(self, settings):
        from app.dependencies import get_current_user

        payload = {
            "sub": "user-y",
            "role": "admin",
            "type": "access",
            "exp": datetime.now(UTC) + timedelta(hours=1),
        }
        token = jwt.encode(payload, "wrong-secret", algorithm=settings.jwt_algorithm)

        with pytest.raises(HTTPException) as exc:
            await get_current_user(_no_scopes(), _fake_request(), token)
        assert exc.value.status_code == 401


class TestScopeEnforcement:
    """Tests for SecurityScopes-based RBAC in get_current_user."""

    async def test_matching_scope_passes(self):
        from app.dependencies import get_current_user

        token = create_access_token("user-1", "analyst", username="analyst")
        scopes = SecurityScopes(scopes=["admin", "analyst"])
        user = await get_current_user(scopes, _fake_request(), token)
        assert user.id == "user-1"

    async def test_non_matching_scope_raises_403(self):
        from app.dependencies import get_current_user

        token = create_access_token("user-1", "viewer", username="viewer")
        scopes = SecurityScopes(scopes=["admin"])

        with pytest.raises(HTTPException) as exc:
            await get_current_user(scopes, _fake_request(), token)
        assert exc.value.status_code == 403
        assert "Insufficient permissions" in exc.value.detail

    async def test_no_scopes_accepts_any_role(self):
        from app.dependencies import get_current_user

        token = create_access_token("user-1", "viewer", username="viewer")
        scopes = SecurityScopes(scopes=[])
        user = await get_current_user(scopes, _fake_request(), token)
        assert user.role == "viewer"

    async def test_all_roles_accepted_when_listed(self):
        from app.dependencies import get_current_user

        scopes = SecurityScopes(scopes=["admin", "analyst", "viewer"])
        for role in ("admin", "analyst", "viewer"):
            token = create_access_token(f"user-{role}", role, username=role)
            user = await get_current_user(scopes, _fake_request(), token)
            assert user.role == role

    async def test_www_authenticate_includes_scopes(self):
        from app.dependencies import get_current_user

        token = create_access_token("user-1", "viewer", username="viewer")
        scopes = SecurityScopes(scopes=["admin", "analyst"])

        with pytest.raises(HTTPException) as exc:
            await get_current_user(scopes, _fake_request(), token)
        assert 'scope="admin analyst"' in exc.value.headers["WWW-Authenticate"]
