"""Tests for the POST /internal/identity/verify-tenant HTTP endpoint.

Mirrors the style of test_internal_identity_verify.py. Each test isolates
one decision branch by mocking DB, Redis, and the verifier service layer.

Coverage:
- 200 + evidence='tenant_only' on happy-path verification
- 401 on invalid internal token
- 400 + unknown_caller_service when caller not in allowlist
- 403 + tenant_not_found when the org has no live portal_orgs row
- 503 + cache_unavailable when Redis is not available
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import status as http_status

from app.api.internal import IdentityVerifyTenantRequest, verify_tenant_identity

# ---------------------------------------------------------------------------
# Helpers — mirrors test_internal_identity_verify.py patterns exactly
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
    url.path = "/internal/identity/verify-tenant"
    request.url = url

    scope: dict = {}
    request.scope = scope
    request.state = MagicMock()
    return request


def _make_redis_mock() -> AsyncMock:
    """In-memory Redis mock supporting ``get`` and ``set`` (with ex)."""
    store: dict[str, str] = {}

    async def fake_get(key: str) -> str | None:
        return store.get(key)

    async def fake_set(key: str, value: str, ex: int | None = None) -> bool:
        store[key] = value
        return True

    redis = AsyncMock()
    redis.get = AsyncMock(side_effect=fake_get)
    redis.set = AsyncMock(side_effect=fake_set)
    redis._store = store
    return redis


def _allow_tenant_db_mock(slug: str = "acme") -> AsyncMock:
    """DB mock that returns a slug from any execute() call (org exists)."""
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=slug)
    db.execute = AsyncMock(return_value=result)
    return db


def _missing_org_db_mock() -> AsyncMock:
    """DB mock that returns None (org does not exist)."""
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=None)
    db.execute = AsyncMock(return_value=result)
    return db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestVerifyTenantHappyPath:
    """200 response with evidence='tenant_only' when org is live."""

    async def test_returns_200_with_tenant_only_evidence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        redis = _make_redis_mock()
        monkeypatch.setattr("app.api.internal.get_redis_pool", AsyncMock(return_value=redis))
        monkeypatch.setattr("app.api.internal._check_rate_limit_internal", AsyncMock())

        with _patched_internal_settings(monkeypatch):
            response = await verify_tenant_identity(
                request=_make_request(),
                body=IdentityVerifyTenantRequest(
                    caller_service="portal-api",
                    claimed_org_id="o-1",
                ),
                db=_allow_tenant_db_mock(),
            )

        assert response.status_code == http_status.HTTP_200_OK
        body = json.loads(response.body)
        assert body["verified"] is True
        assert body["evidence"] == "tenant_only"
        assert body["org_id"] == "o-1"
        assert body["org_slug"] == "acme"
        # user_id must NOT appear in the tenant-only response.
        assert "user_id" not in body

    async def test_caches_verified_decision_on_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A successful verification MUST write to Redis so the next call is a cache hit."""
        redis = _make_redis_mock()
        monkeypatch.setattr("app.api.internal.get_redis_pool", AsyncMock(return_value=redis))
        monkeypatch.setattr("app.api.internal._check_rate_limit_internal", AsyncMock())

        with _patched_internal_settings(monkeypatch):
            await verify_tenant_identity(
                request=_make_request(),
                body=IdentityVerifyTenantRequest(
                    caller_service="portal-api",
                    claimed_org_id="o-1",
                ),
                db=_allow_tenant_db_mock(),
            )

        # At least one key should have been written to the mock Redis store.
        assert len(redis._store) >= 1


class TestVerifyTenantInvalidToken:
    """401 when the internal bearer token is wrong."""

    async def test_returns_401_on_bad_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.api.internal._check_rate_limit_internal", AsyncMock())

        with _patched_internal_settings(monkeypatch, secret="correct-secret"):
            from fastapi import HTTPException

            with pytest.raises(HTTPException) as exc_info:
                await verify_tenant_identity(
                    request=_make_request(token="wrong-secret"),
                    body=IdentityVerifyTenantRequest(
                        caller_service="portal-api",
                        claimed_org_id="o-1",
                    ),
                    db=_allow_tenant_db_mock(),
                )

        assert exc_info.value.status_code == http_status.HTTP_401_UNAUTHORIZED


class TestVerifyTenantUnknownCallerService:
    """400 when caller_service is not in the allowlist."""

    async def test_returns_400_for_unknown_caller(self, monkeypatch: pytest.MonkeyPatch) -> None:
        redis = _make_redis_mock()
        monkeypatch.setattr("app.api.internal.get_redis_pool", AsyncMock(return_value=redis))
        monkeypatch.setattr("app.api.internal._check_rate_limit_internal", AsyncMock())

        with _patched_internal_settings(monkeypatch):
            response = await verify_tenant_identity(
                request=_make_request(),
                body=IdentityVerifyTenantRequest(
                    caller_service="rogue-service",
                    claimed_org_id="o-1",
                ),
                db=_allow_tenant_db_mock(),
            )

        assert response.status_code == http_status.HTTP_400_BAD_REQUEST
        body = json.loads(response.body)
        assert body == {"verified": False, "reason": "unknown_caller_service"}


class TestVerifyTenantNotFound:
    """403 + tenant_not_found when the org has no live portal_orgs row."""

    async def test_returns_403_on_missing_org(self, monkeypatch: pytest.MonkeyPatch) -> None:
        redis = _make_redis_mock()
        monkeypatch.setattr("app.api.internal.get_redis_pool", AsyncMock(return_value=redis))
        monkeypatch.setattr("app.api.internal._check_rate_limit_internal", AsyncMock())

        with _patched_internal_settings(monkeypatch):
            response = await verify_tenant_identity(
                request=_make_request(),
                body=IdentityVerifyTenantRequest(
                    caller_service="portal-api",
                    claimed_org_id="o-does-not-exist",
                ),
                db=_missing_org_db_mock(),
            )

        assert response.status_code == http_status.HTTP_403_FORBIDDEN
        body = json.loads(response.body)
        assert body == {"verified": False, "reason": "tenant_not_found"}


class TestVerifyTenantCacheUnavailable:
    """503 + cache_unavailable when Redis pool is not available."""

    async def test_returns_503_when_redis_pool_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.api.internal.get_redis_pool", AsyncMock(return_value=None))
        monkeypatch.setattr("app.api.internal._check_rate_limit_internal", AsyncMock())

        with _patched_internal_settings(monkeypatch):
            response = await verify_tenant_identity(
                request=_make_request(),
                body=IdentityVerifyTenantRequest(
                    caller_service="portal-api",
                    claimed_org_id="o-1",
                ),
                db=_allow_tenant_db_mock(),
            )

        assert response.status_code == http_status.HTTP_503_SERVICE_UNAVAILABLE
        body = json.loads(response.body)
        assert body == {"verified": False, "reason": "cache_unavailable"}
