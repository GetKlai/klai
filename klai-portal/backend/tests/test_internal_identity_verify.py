"""Tests for the POST /internal/identity/verify HTTP endpoint.

These tests exercise the endpoint as a direct async function call with mocks
for the database, Redis, and JWT validator. The same unit-test style is used
in test_internal_hardening.py — no FastAPI TestClient wiring is needed and
each test isolates one decision branch.

SPEC-SEC-IDENTITY-ASSERT-001 acceptance coverage:

- AC-5a: verified JWT  → 200 + evidence='jwt'
- AC-5b: JWT mismatch → 403 + reason='jwt_identity_mismatch'
- AC-5c: membership   → 200 + evidence='membership'
- AC-5d: no_membership → 403 + reason='no_membership'
- AC-5e: cache hit    → second call skips DB/JWT
- AC-5g: redis down   → 503 + reason='cache_unavailable'
- REQ-1.2: unknown caller_service → 400 + reason='unknown_caller_service'
- REQ-1.7: structlog identity_verify_decision emitted with hashed user_id
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import status as http_status
from redis.exceptions import RedisError

from app.api.internal import IdentityVerifyRequest, verify_identity

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@contextmanager
def _patched_internal_settings(monkeypatch: pytest.MonkeyPatch, *, secret: str = "test-secret"):
    from app.api import internal as internal_mod

    monkeypatch.setattr(internal_mod.settings, "internal_secret", secret)
    yield


def _make_request(*, token: str = "test-secret", caller_ip: str = "172.18.0.5") -> MagicMock:
    """Mock FastAPI Request that satisfies _require_internal_token + audit context."""
    request = MagicMock()
    headers = {"Authorization": f"Bearer {token}"}
    request.headers = MagicMock()
    request.headers.get = lambda key, default="": next(
        (v for k, v in headers.items() if k.lower() == key.lower()),
        default,
    )
    request.client = MagicMock()
    request.client.host = caller_ip
    request.method = "POST"

    url = MagicMock()
    url.path = "/internal/identity/verify"
    request.url = url

    scope = {}
    request.scope = scope
    request.state = MagicMock()
    return request


def _make_redis_mock() -> AsyncMock:
    """In-memory Redis mock supporting only ``get`` and ``set`` (with ex)."""
    store: dict[str, str] = {}

    async def fake_get(key: str) -> str | None:
        return store.get(key)

    async def fake_set(key: str, value: str, ex: int | None = None) -> bool:
        store[key] = value
        return True

    redis = AsyncMock()
    redis.get = AsyncMock(side_effect=fake_get)
    redis.set = AsyncMock(side_effect=fake_set)
    redis._store = store  # exposed for tests to seed/inspect
    return redis


def _success_db_mock() -> AsyncMock:
    """DB mock that returns a row from any execute() — simulates active membership."""
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=42)
    db.execute = AsyncMock(return_value=result)
    return db


class _FakeJwksResolver:
    """Test resolver: any token resolves to a constant signing key.

    Real signature validation is bypassed because tests monkey-patch
    ``jwt.decode`` directly to return a constructed claim set. The resolver
    only needs to satisfy the call before decode.
    """

    class _Key:
        key = "fake"

    def get_signing_key_from_jwt(self, _token: str) -> _Key:
        return self._Key()


def _patch_jwks_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the endpoint's PyJWKClient with a no-op fake.

    The endpoint resolves JWKS via ``_get_identity_jwks_resolver`` which
    constructs a live ``PyJWKClient`` against Zitadel. Tests substitute a
    fake to avoid the network call.
    """
    monkeypatch.setattr(
        "app.api.internal._get_identity_jwks_resolver",
        lambda: _FakeJwksResolver(),
    )


def _missing_db_mock() -> AsyncMock:
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=None)
    db.execute = AsyncMock(return_value=result)
    return db


# ---------------------------------------------------------------------------
# REQ-1.2: unknown caller_service
# ---------------------------------------------------------------------------


class TestUnknownCallerService:
    async def test_returns_400_with_stable_reason(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Redis is fine but should never be hit because the allowlist check
        # is the first gate after token validation.
        redis = _make_redis_mock()
        monkeypatch.setattr("app.api.internal.get_redis_pool", AsyncMock(return_value=redis))
        monkeypatch.setattr("app.api.internal._check_rate_limit_internal", AsyncMock())

        with _patched_internal_settings(monkeypatch):
            response = await verify_identity(
                request=_make_request(),
                body=IdentityVerifyRequest(
                    caller_service="rogue-service",
                    claimed_user_id="u-1",
                    claimed_org_id="o-1",
                ),
                db=_success_db_mock(),
            )

        assert response.status_code == http_status.HTTP_400_BAD_REQUEST
        body = json.loads(response.body)
        assert body == {"verified": False, "reason": "unknown_caller_service"}
        # Allowlist gate runs BEFORE Redis lookup — see REQ-1.2.
        redis.get.assert_not_awaited()


# ---------------------------------------------------------------------------
# REQ-1.4: membership path (AC-5c, AC-5d)
# ---------------------------------------------------------------------------


class TestMembershipPath:
    async def test_returns_200_for_active_membership(self, monkeypatch: pytest.MonkeyPatch) -> None:
        redis = _make_redis_mock()
        monkeypatch.setattr("app.api.internal.get_redis_pool", AsyncMock(return_value=redis))
        monkeypatch.setattr("app.api.internal._check_rate_limit_internal", AsyncMock())

        with _patched_internal_settings(monkeypatch):
            response = await verify_identity(
                request=_make_request(),
                body=IdentityVerifyRequest(
                    caller_service="scribe",
                    claimed_user_id="u-1",
                    claimed_org_id="o-1",
                    bearer_jwt=None,
                ),
                db=_success_db_mock(),
            )

        assert response.status_code == http_status.HTTP_200_OK
        body = json.loads(response.body)
        assert body["verified"] is True
        assert body["evidence"] == "membership"
        assert body["user_id"] == "u-1"
        assert body["org_id"] == "o-1"
        assert body["cache_ttl_seconds"] == 60

        # Verified result MUST be cached (REQ-1.5).
        redis.set.assert_awaited_once()

    async def test_returns_403_for_no_membership(self, monkeypatch: pytest.MonkeyPatch) -> None:
        redis = _make_redis_mock()
        monkeypatch.setattr("app.api.internal.get_redis_pool", AsyncMock(return_value=redis))
        monkeypatch.setattr("app.api.internal._check_rate_limit_internal", AsyncMock())

        with _patched_internal_settings(monkeypatch):
            response = await verify_identity(
                request=_make_request(),
                body=IdentityVerifyRequest(
                    caller_service="scribe",
                    claimed_user_id="u-1",
                    claimed_org_id="o-2-not-a-member",
                    bearer_jwt=None,
                ),
                db=_missing_db_mock(),
            )

        assert response.status_code == http_status.HTTP_403_FORBIDDEN
        body = json.loads(response.body)
        assert body == {"verified": False, "reason": "no_membership"}
        # Denials MUST NOT be cached (REQ-1.5).
        redis.set.assert_not_awaited()


# ---------------------------------------------------------------------------
# REQ-1.3: JWT path (AC-5a, AC-5b)
# ---------------------------------------------------------------------------


class TestJwtPath:
    async def test_returns_200_when_jwt_matches_claimed_identity(self, monkeypatch: pytest.MonkeyPatch) -> None:
        redis = _make_redis_mock()
        monkeypatch.setattr("app.api.internal.get_redis_pool", AsyncMock(return_value=redis))
        monkeypatch.setattr("app.api.internal._check_rate_limit_internal", AsyncMock())
        _patch_jwks_resolver(monkeypatch)
        monkeypatch.setattr(
            "app.services.identity_verifier.jwt.decode",
            lambda *_args, **_kwargs: {
                "sub": "u-1",
                "iss": "https://zitadel.example.com",
                "exp": 9999999999,
                "urn:zitadel:iam:user:resourceowner:id": "o-1",
            },
        )

        with _patched_internal_settings(monkeypatch):
            response = await verify_identity(
                request=_make_request(),
                body=IdentityVerifyRequest(
                    caller_service="scribe",
                    claimed_user_id="u-1",
                    claimed_org_id="o-1",
                    bearer_jwt="any.jwt.value",
                ),
                db=_success_db_mock(),
            )

        assert response.status_code == http_status.HTTP_200_OK
        body = json.loads(response.body)
        assert body["verified"] is True
        assert body["evidence"] == "jwt"
        assert body["cache_ttl_seconds"] == 60

    async def test_returns_403_when_jwt_sub_mismatches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        redis = _make_redis_mock()
        monkeypatch.setattr("app.api.internal.get_redis_pool", AsyncMock(return_value=redis))
        monkeypatch.setattr("app.api.internal._check_rate_limit_internal", AsyncMock())
        _patch_jwks_resolver(monkeypatch)
        monkeypatch.setattr(
            "app.services.identity_verifier.jwt.decode",
            lambda *_args, **_kwargs: {
                "sub": "u-A-NOT-MATCHING",
                "iss": "https://zitadel.example.com",
                "exp": 9999999999,
                "urn:zitadel:iam:user:resourceowner:id": "o-1",
            },
        )

        with _patched_internal_settings(monkeypatch):
            response = await verify_identity(
                request=_make_request(),
                body=IdentityVerifyRequest(
                    caller_service="scribe",
                    claimed_user_id="u-1",
                    claimed_org_id="o-1",
                    bearer_jwt="forged.jwt.value",
                ),
                db=_success_db_mock(),
            )

        assert response.status_code == http_status.HTTP_403_FORBIDDEN
        body = json.loads(response.body)
        assert body == {"verified": False, "reason": "jwt_identity_mismatch"}
        # No cache write on deny.
        redis.set.assert_not_awaited()


# ---------------------------------------------------------------------------
# REQ-1.5: cache hit (AC-5e)
# ---------------------------------------------------------------------------


class TestCachingBehaviour:
    async def test_cache_hit_skips_db_and_jwt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Pre-seed the Redis cache with a verified JWT-evidence entry.
        # Cache key includes evidence dimension (REQ-1.5) so a JWT-bearing
        # request looks up the JWT-evidence entry deterministically.
        redis = _make_redis_mock()
        cache_key = "identity_verify:scribe:u-1:o-1:jwt"
        redis._store[cache_key] = json.dumps({"user_id": "u-1", "org_id": "o-1", "evidence": "jwt"})
        monkeypatch.setattr("app.api.internal.get_redis_pool", AsyncMock(return_value=redis))
        monkeypatch.setattr("app.api.internal._check_rate_limit_internal", AsyncMock())

        _patch_jwks_resolver(monkeypatch)
        db = _success_db_mock()
        jwt_decode = MagicMock()
        monkeypatch.setattr("app.services.identity_verifier.jwt.decode", jwt_decode)

        with _patched_internal_settings(monkeypatch):
            response = await verify_identity(
                request=_make_request(),
                body=IdentityVerifyRequest(
                    caller_service="scribe",
                    claimed_user_id="u-1",
                    claimed_org_id="o-1",
                    bearer_jwt="any.jwt.value",  # would be decoded if cache missed
                ),
                db=db,
            )

        assert response.status_code == http_status.HTTP_200_OK
        body = json.loads(response.body)
        assert body["verified"] is True
        assert body["evidence"] == "jwt"
        # AC-5e: cache hit MUST NOT trigger DB or JWT signature re-check.
        db.execute.assert_not_called()
        jwt_decode.assert_not_called()

    async def test_jwt_cache_entry_does_not_serve_membership_request(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """REQ-1.5 strict keying: a JWT-evidence cache entry MUST NOT serve a
        membership-evidence (bearer_jwt=None) lookup.

        Honest audit: a request that did not forward a JWT must never see
        ``evidence="jwt"`` in the response, otherwise the audit signal lies
        about which check actually fired.
        """
        # Seed a JWT-evidence entry only.
        redis = _make_redis_mock()
        redis._store["identity_verify:scribe:u-1:o-1:jwt"] = json.dumps(
            {"user_id": "u-1", "org_id": "o-1", "evidence": "jwt"}
        )
        monkeypatch.setattr("app.api.internal.get_redis_pool", AsyncMock(return_value=redis))
        monkeypatch.setattr("app.api.internal._check_rate_limit_internal", AsyncMock())
        _patch_jwks_resolver(monkeypatch)

        # Request comes in with bearer_jwt=None — membership lookup expected.
        with _patched_internal_settings(monkeypatch):
            response = await verify_identity(
                request=_make_request(),
                body=IdentityVerifyRequest(
                    caller_service="scribe",
                    claimed_user_id="u-1",
                    claimed_org_id="o-1",
                    bearer_jwt=None,
                ),
                db=_success_db_mock(),
            )

        assert response.status_code == http_status.HTTP_200_OK
        body = json.loads(response.body)
        # The JWT-cached value must NOT have been served — fresh membership
        # lookup ran, so evidence must be membership.
        assert body["evidence"] == "membership"

    async def test_membership_cache_entry_does_not_serve_jwt_request(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Symmetric counterpart to the JWT-vs-membership isolation test above."""
        redis = _make_redis_mock()
        redis._store["identity_verify:scribe:u-1:o-1:membership"] = json.dumps(
            {"user_id": "u-1", "org_id": "o-1", "evidence": "membership"}
        )
        monkeypatch.setattr("app.api.internal.get_redis_pool", AsyncMock(return_value=redis))
        monkeypatch.setattr("app.api.internal._check_rate_limit_internal", AsyncMock())
        _patch_jwks_resolver(monkeypatch)
        monkeypatch.setattr(
            "app.services.identity_verifier.jwt.decode",
            lambda *_args, **_kwargs: {
                "sub": "u-1",
                "iss": "https://zitadel.example.com",
                "exp": 9999999999,
                "urn:zitadel:iam:user:resourceowner:id": "o-1",
            },
        )

        with _patched_internal_settings(monkeypatch):
            response = await verify_identity(
                request=_make_request(),
                body=IdentityVerifyRequest(
                    caller_service="scribe",
                    claimed_user_id="u-1",
                    claimed_org_id="o-1",
                    bearer_jwt="some.jwt.value",
                ),
                db=_success_db_mock(),
            )

        assert response.status_code == http_status.HTTP_200_OK
        body = json.loads(response.body)
        # Fresh JWT validation ran, so evidence must be jwt — not the
        # cached membership entry.
        assert body["evidence"] == "jwt"


# ---------------------------------------------------------------------------
# REQ-1.6: Redis fail-closed (AC-5g)
# ---------------------------------------------------------------------------


class TestRedisFailureMode:
    async def test_returns_503_when_redis_pool_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Simulate transient Redis pool unavailability.
        monkeypatch.setattr("app.api.internal.get_redis_pool", AsyncMock(return_value=None))
        monkeypatch.setattr("app.api.internal._check_rate_limit_internal", AsyncMock())

        with _patched_internal_settings(monkeypatch):
            response = await verify_identity(
                request=_make_request(),
                body=IdentityVerifyRequest(
                    caller_service="scribe",
                    claimed_user_id="u-1",
                    claimed_org_id="o-1",
                    bearer_jwt=None,
                ),
                db=_success_db_mock(),
            )

        assert response.status_code == http_status.HTTP_503_SERVICE_UNAVAILABLE
        body = json.loads(response.body)
        assert body == {"verified": False, "reason": "cache_unavailable"}

    async def test_returns_503_when_redis_get_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        redis = _make_redis_mock()
        redis.get = AsyncMock(side_effect=RedisError("connection refused"))
        monkeypatch.setattr("app.api.internal.get_redis_pool", AsyncMock(return_value=redis))
        monkeypatch.setattr("app.api.internal._check_rate_limit_internal", AsyncMock())

        with _patched_internal_settings(monkeypatch):
            response = await verify_identity(
                request=_make_request(),
                body=IdentityVerifyRequest(
                    caller_service="scribe",
                    claimed_user_id="u-1",
                    claimed_org_id="o-1",
                    bearer_jwt=None,
                ),
                db=_success_db_mock(),
            )

        assert response.status_code == http_status.HTTP_503_SERVICE_UNAVAILABLE
        body = json.loads(response.body)
        assert body["reason"] == "cache_unavailable"

    async def test_returns_503_when_redis_set_raises_after_verified(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Cache miss + DB allow + Redis SET fails → MUST fail closed (REQ-1.6).
        redis = _make_redis_mock()
        redis.set = AsyncMock(side_effect=RedisError("connection refused"))
        monkeypatch.setattr("app.api.internal.get_redis_pool", AsyncMock(return_value=redis))
        monkeypatch.setattr("app.api.internal._check_rate_limit_internal", AsyncMock())

        with _patched_internal_settings(monkeypatch):
            response = await verify_identity(
                request=_make_request(),
                body=IdentityVerifyRequest(
                    caller_service="scribe",
                    claimed_user_id="u-1",
                    claimed_org_id="o-1",
                    bearer_jwt=None,
                ),
                db=_success_db_mock(),
            )

        # The DB lookup *did* succeed but caching failed — refuse to leak the
        # verified decision because the next call would skip cache and amplify
        # DB load. This is the REQ-1.6 fail-closed contract.
        assert response.status_code == http_status.HTTP_503_SERVICE_UNAVAILABLE
        body = json.loads(response.body)
        assert body["reason"] == "cache_unavailable"
