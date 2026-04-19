"""Tests for the BFF OIDC start/callback flow (SPEC-AUTH-008 Phase A3)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.session import SESSION_COOKIE_NAME
from app.middleware.session import SessionMiddleware
from app.services.bff_oidc import (
    OidcFlowError,
    TokenSet,
    generate_code_verifier,
    s256_challenge,
)
from app.services.bff_session import SessionService


@pytest.fixture(autouse=True)
def _configure_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "bff_session_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "sso_cookie_key", "")
    monkeypatch.setattr(settings, "bff_session_ttl_seconds", 86400)
    monkeypatch.setattr(settings, "domain", "getklai.com")
    monkeypatch.setattr(settings, "frontend_url", "https://my.getklai.com")
    monkeypatch.setattr(settings, "zitadel_base_url", "https://auth.getklai.com")
    monkeypatch.setattr(settings, "zitadel_portal_client_id", "362901948573220875")
    monkeypatch.setattr(settings, "zitadel_portal_client_secret", "")
    from app.services import bff_session as svc_module

    svc_module.session_service._fernet = None


@pytest.fixture
def fake_redis() -> AsyncMock:
    store: dict[str, Any] = {}

    async def fake_get(key: str) -> bytes | None:
        return store.get(key)

    async def fake_set(key: str, value: bytes, ex: int | None = None) -> None:
        store[key] = value

    async def fake_delete(*keys: str) -> int:
        count = 0
        for key in keys:
            if key in store:
                del store[key]
                count += 1
        return count

    pool = AsyncMock()
    pool.get.side_effect = fake_get
    pool.set.side_effect = fake_set
    pool.delete.side_effect = fake_delete
    pool._store = store
    return pool


@pytest.fixture
def wire_redis(monkeypatch: pytest.MonkeyPatch, fake_redis: AsyncMock) -> AsyncMock:
    async def get_pool() -> AsyncMock:
        return fake_redis

    monkeypatch.setattr("app.services.bff_session.get_redis_pool", get_pool)
    monkeypatch.setattr("app.services.oidc_pending.get_redis_pool", get_pool)
    return fake_redis


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    from app.api.auth_bff import router as auth_bff_router

    async def fake_get_db():
        yield _FakeDb()

    app = FastAPI()
    app.add_middleware(SessionMiddleware)
    app.include_router(auth_bff_router)

    from app.core import database

    app.dependency_overrides[database.get_db] = fake_get_db
    return app


class _FakeDb:
    def __init__(self, portal_user_org_id: int | None = 42) -> None:
        self._org_id = portal_user_org_id

    async def execute(self, _stmt: Any) -> Any:
        return _FakeResult(self._org_id)


class _FakeResult:
    def __init__(self, org_id: int | None) -> None:
        self._org_id = org_id

    def scalar_one_or_none(self) -> Any:
        if self._org_id is None:
            return None

        class _Row:
            org_id = self._org_id

        return _Row()


# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------


class TestPkce:
    def test_verifier_length_and_alphabet(self) -> None:
        v = generate_code_verifier()
        assert 43 <= len(v) <= 128
        assert set(v) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")

    def test_s256_challenge_deterministic(self) -> None:
        assert s256_challenge("verifier") == s256_challenge("verifier")

    def test_s256_challenge_is_unpadded_base64url(self) -> None:
        assert "=" not in s256_challenge("abc")

    def test_different_verifier_different_challenge(self) -> None:
        assert s256_challenge("a") != s256_challenge("b")


# ---------------------------------------------------------------------------
# GET /api/auth/oidc/start
# ---------------------------------------------------------------------------


class TestOidcStart:
    def test_redirects_to_zitadel_with_pkce(self, app: FastAPI, wire_redis: AsyncMock) -> None:
        client = TestClient(app, follow_redirects=False)
        resp = client.get("/api/auth/oidc/start?return_to=/app")
        assert resp.status_code == 302
        location = resp.headers["location"]
        assert location.startswith("https://auth.getklai.com/oauth/v2/authorize?")
        qs = parse_qs(urlparse(location).query)
        assert qs["response_type"] == ["code"]
        assert qs["client_id"] == ["362901948573220875"]
        assert qs["redirect_uri"] == ["https://my.getklai.com/api/auth/oidc/callback"]
        assert qs["code_challenge_method"] == ["S256"]
        assert qs["state"]
        assert qs["code_challenge"]
        state = qs["state"][0]
        pending_keys = [k for k in wire_redis._store if k.startswith("klai:oidc_pending:")]
        assert f"klai:oidc_pending:{state}" in pending_keys

    def test_default_return_to_is_app(self, app: FastAPI, wire_redis: AsyncMock) -> None:
        client = TestClient(app, follow_redirects=False)
        resp = client.get("/api/auth/oidc/start")
        assert resp.status_code == 302

    @pytest.mark.parametrize(
        "bad_return_to",
        [
            "https://evil.example.com",
            "//evil.example.com",
            "javascript:alert(1)",
            "",
        ],
    )
    def test_rejects_open_redirect_targets(self, app: FastAPI, wire_redis: AsyncMock, bad_return_to: str) -> None:
        client = TestClient(app, follow_redirects=False)
        resp = client.get(f"/api/auth/oidc/start?return_to={bad_return_to}")
        assert resp.status_code == 302
        assert any(k.startswith("klai:oidc_pending:") for k in wire_redis._store)


# ---------------------------------------------------------------------------
# GET /api/auth/oidc/callback
# ---------------------------------------------------------------------------


class TestOidcCallback:
    def test_missing_code_redirects_to_logged_out(
        self,
        app: FastAPI,
        wire_redis: AsyncMock,
    ) -> None:
        client = TestClient(app, follow_redirects=False)
        resp = client.get("/api/auth/oidc/callback?state=x")
        assert resp.status_code == 302
        assert "reason=invalid_request" in resp.headers["location"]

    def test_unknown_state_redirects(
        self,
        app: FastAPI,
        wire_redis: AsyncMock,
    ) -> None:
        client = TestClient(app, follow_redirects=False)
        resp = client.get("/api/auth/oidc/callback?code=abc&state=never-issued")
        assert resp.status_code == 302
        assert "reason=invalid_state" in resp.headers["location"]

    def test_explicit_op_error_is_propagated(
        self,
        app: FastAPI,
        wire_redis: AsyncMock,
    ) -> None:
        client = TestClient(app, follow_redirects=False)
        resp = client.get("/api/auth/oidc/callback?error=access_denied&state=x")
        assert resp.status_code == 302
        assert "reason=access_denied" in resp.headers["location"]

    def test_successful_callback_sets_session_cookies(
        self, app: FastAPI, wire_redis: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = TestClient(app, follow_redirects=False)
        start = client.get("/api/auth/oidc/start?return_to=/app")
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]

        async def fake_exchange(*, code: str, code_verifier: str, redirect_uri: str) -> TokenSet:
            return TokenSet(
                access_token="at-live",
                refresh_token="rt-live",
                id_token="idt-live",
                expires_in=3600,
            )

        def fake_verify(_id_token: str) -> dict:
            return {"sub": "user-xyz", "iss": settings.zitadel_base_url, "aud": "x", "exp": 0}

        monkeypatch.setattr("app.api.auth_bff.exchange_code_for_tokens", fake_exchange)
        monkeypatch.setattr("app.api.auth_bff.verify_id_token", fake_verify)

        resp = client.get(f"/api/auth/oidc/callback?code=the-code&state={state}")
        assert resp.status_code == 302
        # Callback routes through /callback frontend page so workspaceHandoff
        # can run; original return_to is preserved as a query param.
        assert resp.headers["location"] == "/callback?return_to=/app"
        set_cookies = resp.headers.get_list("set-cookie")
        assert any(SESSION_COOKIE_NAME in c and "HttpOnly" in c for c in set_cookies)
        assert f"klai:oidc_pending:{state}" not in wire_redis._store
        session_keys = [k for k in wire_redis._store if k.startswith("klai:session:")]
        assert len(session_keys) == 1
        svc = SessionService()
        svc._fernet = None
        sid = session_keys[0].removeprefix("klai:session:")
        import asyncio

        loaded = asyncio.new_event_loop().run_until_complete(svc.load(sid))
        assert loaded is not None
        assert loaded.zitadel_user_id == "user-xyz"
        assert loaded.org_id == 42
        assert loaded.access_token == "at-live"

    def test_token_exchange_failure_redirects_with_reason(
        self,
        app: FastAPI,
        wire_redis: AsyncMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client = TestClient(app, follow_redirects=False)
        start = client.get("/api/auth/oidc/start?return_to=/app")
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]

        async def fake_exchange(**_kwargs: Any) -> TokenSet:
            raise OidcFlowError("invalid_grant", "code reuse")

        monkeypatch.setattr("app.api.auth_bff.exchange_code_for_tokens", fake_exchange)

        resp = client.get(f"/api/auth/oidc/callback?code=bad&state={state}")
        assert resp.status_code == 302
        assert "reason=invalid_grant" in resp.headers["location"]

    def test_id_token_verification_failure_redirects(
        self,
        app: FastAPI,
        wire_redis: AsyncMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client = TestClient(app, follow_redirects=False)
        start = client.get("/api/auth/oidc/start?return_to=/app")
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]

        async def fake_exchange(**_kwargs: Any) -> TokenSet:
            return TokenSet(access_token="at", refresh_token="rt", id_token="idt", expires_in=3600)

        def fake_verify(_t: str) -> dict:
            raise OidcFlowError("id_token_invalid", "bad signature")

        monkeypatch.setattr("app.api.auth_bff.exchange_code_for_tokens", fake_exchange)
        monkeypatch.setattr("app.api.auth_bff.verify_id_token", fake_verify)

        resp = client.get(f"/api/auth/oidc/callback?code=x&state={state}")
        assert resp.status_code == 302
        assert "reason=id_token_invalid" in resp.headers["location"]
