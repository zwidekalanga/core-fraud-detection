"""Contract tests for cross-service JWT format (T-06).

Validates that the JWT claims produced by core-banking (simulated via
test token factory) can be decoded and validated by core-fraud-detection's
``decode_token()`` function. This ensures both services agree on:

- Required claims: sub, role, username, email, type, exp
- Token types: "access" vs "refresh"
- Signing algorithm: HS256
- Shared secret key

If core-banking changes its token format, these tests will catch the
incompatibility before deployment.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from jose import jwt

from app.auth.security import decode_token
from app.config import get_settings
from tests.helpers.token_factory import create_access_token, create_refresh_token

# ---------------------------------------------------------------------------
# Contract: required claims
# ---------------------------------------------------------------------------


class TestJWTRequiredClaims:
    """Both services must agree on the required JWT claim set."""

    REQUIRED_CLAIMS = {"sub", "role", "type", "exp"}
    OPTIONAL_CLAIMS = {"username", "email"}

    def test_access_token_contains_required_claims(self):
        token = create_access_token(
            user_id="user-001",
            role="admin",
            username="admin",
            email="admin@capitec.co.za",
        )
        payload = decode_token(token)

        for claim in self.REQUIRED_CLAIMS:
            assert claim in payload, f"Missing required claim: {claim}"

    def test_access_token_contains_optional_claims(self):
        token = create_access_token(
            user_id="user-001",
            role="admin",
            username="admin",
            email="admin@capitec.co.za",
        )
        payload = decode_token(token)

        for claim in self.OPTIONAL_CLAIMS:
            assert claim in payload, f"Missing optional claim: {claim}"

    def test_refresh_token_contains_required_claims(self):
        token = create_refresh_token(
            user_id="user-001",
            role="analyst",
            username="analyst",
            email="analyst@capitec.co.za",
        )
        payload = decode_token(token)

        for claim in self.REQUIRED_CLAIMS:
            assert claim in payload, f"Missing required claim: {claim}"


# ---------------------------------------------------------------------------
# Contract: token types
# ---------------------------------------------------------------------------


class TestJWTTokenTypes:
    """Access and refresh tokens must be distinguishable by 'type' claim."""

    def test_access_token_type_is_access(self):
        token = create_access_token("user-001", "admin")
        payload = decode_token(token)
        assert payload["type"] == "access"

    def test_refresh_token_type_is_refresh(self):
        token = create_refresh_token("user-001", "admin")
        payload = decode_token(token)
        assert payload["type"] == "refresh"

    def test_access_and_refresh_types_differ(self):
        access = create_access_token("user-001", "admin")
        refresh = create_refresh_token("user-001", "admin")

        access_payload = decode_token(access)
        refresh_payload = decode_token(refresh)

        assert access_payload["type"] != refresh_payload["type"]


# ---------------------------------------------------------------------------
# Contract: signing algorithm
# ---------------------------------------------------------------------------


class TestJWTSigningAlgorithm:
    """Both services must use the same signing algorithm."""

    def test_algorithm_is_hs256(self):
        settings = get_settings()
        assert settings.jwt_algorithm == "HS256"

    def test_token_header_uses_hs256(self):
        token = create_access_token("user-001", "admin")
        header = jwt.get_unverified_header(token)
        assert header["alg"] == "HS256"


# ---------------------------------------------------------------------------
# Contract: claim values
# ---------------------------------------------------------------------------


class TestJWTClaimValues:
    """Claim values must match expected types and formats."""

    def test_sub_matches_user_id(self):
        user_id = str(uuid.uuid4())
        token = create_access_token(user_id, "admin", "admin", "admin@test.com")
        payload = decode_token(token)
        assert payload["sub"] == user_id

    def test_role_matches_provided_role(self):
        token = create_access_token("user-001", "analyst", "analyst")
        payload = decode_token(token)
        assert payload["role"] == "analyst"

    def test_username_matches_provided_username(self):
        token = create_access_token("user-001", "admin", "admin_user")
        payload = decode_token(token)
        assert payload["username"] == "admin_user"

    def test_email_matches_provided_email(self):
        token = create_access_token("user-001", "admin", "admin", "test@capitec.co.za")
        payload = decode_token(token)
        assert payload["email"] == "test@capitec.co.za"

    def test_exp_is_in_the_future(self):
        token = create_access_token("user-001", "admin")
        payload = decode_token(token)
        exp = datetime.fromtimestamp(payload["exp"], tz=UTC)
        assert exp > datetime.now(UTC)

    def test_all_three_roles_are_valid(self):
        """All RBAC roles used in the system must be representable in JWT."""
        for role in ("admin", "analyst", "viewer"):
            token = create_access_token("user-001", role)
            payload = decode_token(token)
            assert payload["role"] == role


# ---------------------------------------------------------------------------
# Contract: token expiry
# ---------------------------------------------------------------------------


class TestJWTTokenExpiry:
    """Token expiry must align across services."""

    def test_access_token_expires_within_expected_window(self):
        settings = get_settings()
        token = create_access_token("user-001", "admin")
        payload = decode_token(token)

        exp = datetime.fromtimestamp(payload["exp"], tz=UTC)
        now = datetime.now(UTC)
        delta = exp - now

        # Should expire within ±1 minute of configured expiry
        expected = timedelta(minutes=settings.jwt_access_token_expire_minutes)
        assert abs(delta - expected) < timedelta(minutes=1)

    def test_refresh_token_expires_later_than_access(self):
        access = create_access_token("user-001", "admin")
        refresh = create_refresh_token("user-001", "admin")

        access_exp = decode_token(access)["exp"]
        refresh_exp = decode_token(refresh)["exp"]

        assert refresh_exp > access_exp


# ---------------------------------------------------------------------------
# Contract: cross-service compatibility
# ---------------------------------------------------------------------------


class TestCrossServiceCompatibility:
    """Tokens produced by one service must be decodable by the other."""

    def test_token_from_factory_decodable_by_security_module(self):
        """Simulate core-banking producing a token and core-fraud-detection decoding it."""
        token = create_access_token(
            user_id=str(uuid.uuid4()),
            role="analyst",
            username="analyst_user",
            email="analyst@capitec.co.za",
        )
        # core-fraud-detection's decode_token should accept this
        payload = decode_token(token)
        assert payload["role"] == "analyst"
        assert payload["type"] == "access"

    def test_tampered_token_rejected(self):
        """Both services must reject tampered tokens."""
        from jose import JWTError

        token = create_access_token("user-001", "admin")
        # Tamper with the token
        tampered = token[:-5] + "XXXXX"

        with pytest.raises(JWTError):
            decode_token(tampered)

    def test_wrong_secret_rejected(self):
        """Token signed with a different secret must be rejected."""
        from jose import JWTError

        settings = get_settings()
        payload = {
            "sub": "user-001",
            "role": "admin",
            "type": "access",
            "exp": datetime.now(UTC) + timedelta(hours=1),
        }
        token = jwt.encode(payload, "wrong-secret-key", algorithm=settings.jwt_algorithm)

        with pytest.raises(JWTError):
            decode_token(token)
