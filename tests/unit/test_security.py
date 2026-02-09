"""Unit tests for auth/security.py — JWT token utilities."""

from datetime import UTC, datetime, timedelta

import pytest
from jose import jwt

from app.auth.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.config import get_settings


class TestAccessToken:
    """Tests for JWT access token creation and validation."""

    def test_create_access_token_returns_string(self):
        token = create_access_token("user-123", "admin")
        assert isinstance(token, str)
        assert len(token) > 0

    def test_access_token_contains_correct_claims(self):
        token = create_access_token("user-456", "analyst", username="analyst", email="a@test.com")
        settings = get_settings()
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])

        assert payload["sub"] == "user-456"
        assert payload["role"] == "analyst"
        assert payload["username"] == "analyst"
        assert payload["email"] == "a@test.com"
        assert payload["type"] == "access"
        assert "exp" in payload

    def test_access_token_expiry(self):
        settings = get_settings()
        before = datetime.now(UTC).replace(microsecond=0)
        token = create_access_token("user-789", "viewer")

        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        exp = datetime.fromtimestamp(payload["exp"], tz=UTC)

        expected_min = before + timedelta(minutes=settings.jwt_access_token_expire_minutes)
        # Allow 2s tolerance for test execution time
        expected_max = before + timedelta(
            minutes=settings.jwt_access_token_expire_minutes, seconds=2
        )

        assert expected_min <= exp <= expected_max


class TestRefreshToken:
    """Tests for JWT refresh token creation and validation."""

    def test_create_refresh_token_returns_string(self):
        token = create_refresh_token("user-123", "admin")
        assert isinstance(token, str)

    def test_refresh_token_contains_correct_claims(self):
        token = create_refresh_token("user-456", "analyst", username="analyst", email="a@test.com")
        settings = get_settings()
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])

        assert payload["sub"] == "user-456"
        assert payload["role"] == "analyst"
        assert payload["username"] == "analyst"
        assert payload["email"] == "a@test.com"
        assert payload["type"] == "refresh"

    def test_refresh_token_has_longer_expiry_than_access(self):
        settings = get_settings()
        access = create_access_token("user-1", "admin")
        refresh = create_refresh_token("user-1", "admin")

        access_payload = jwt.decode(
            access, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
        refresh_payload = jwt.decode(
            refresh, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )

        assert refresh_payload["exp"] > access_payload["exp"]


class TestDecodeToken:
    """Tests for JWT token decoding."""

    def test_decode_valid_access_token(self):
        token = create_access_token("user-abc", "admin")
        payload = decode_token(token)

        assert payload["sub"] == "user-abc"
        assert payload["role"] == "admin"
        assert payload["type"] == "access"

    def test_decode_valid_refresh_token(self):
        token = create_refresh_token("user-def", "viewer")
        payload = decode_token(token)

        assert payload["sub"] == "user-def"
        assert payload["type"] == "refresh"

    def test_decode_invalid_token_raises(self):
        from jose import JWTError

        with pytest.raises(JWTError):
            decode_token("not.a.valid.jwt")

    def test_decode_tampered_token_raises(self):
        from jose import JWTError

        token = create_access_token("user-x", "admin")
        # Tamper with the token by changing a character
        tampered = token[:-5] + "XXXXX"

        with pytest.raises(JWTError):
            decode_token(tampered)

    def test_decode_token_wrong_secret_raises(self):
        from jose import JWTError

        settings = get_settings()
        payload = {
            "sub": "user-y",
            "role": "admin",
            "type": "access",
            "exp": datetime.now(UTC) + timedelta(hours=1),
        }
        token = jwt.encode(payload, "wrong-secret-key", algorithm=settings.jwt_algorithm)

        with pytest.raises(JWTError):
            decode_token(token)
