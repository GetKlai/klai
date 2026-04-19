"""Integration tests for SessionMiddleware + auth-bff endpoints (SPEC-AUTH-008 A2)."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.auth_bff import router as auth_bff_router
from app.api.session_deps import get_session
from app.core.config import settings
from app.core.session import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    SESSION_COOKIE_NAME,
    SessionContext,
)
from app.middleware.session import SessionMiddleware
from app.services.bff_session import SessionService


@pytest.fixture(autouse=True)
def _configure_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "bff_session_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "sso_cookie_key", "")
    monkeypatch.setattr(settings, "bff_session_ttl_seconds", 86400)
    monkeypatch.setattr(settings, "domain", "getklai.com")


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
    pool._store = store  # type: ignore[attr-defined]
    return pool


@pytest.fixture
def wire_redis(monkeypatch: pytest.MonkeyPatch, fake_redis: AsyncMock) -> AsyncMock:
    """Point all SessionService callers at the fake pool."""

    async def get_pool() -> AsyncMock:
        return fake_redis

    # Patch both the service module AND the middleware import path.
    monkeypatch.setattr("app.services.bff_session.get_redis_pool", get_pool)
    # SessionService uses a singleton; reset its internal Fernet so the fresh
    # key from _configure_keys is picked up.
    from app.services import bff_session as svc_module

    svc_module.session_service._fernet = None
    return fake_redis


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(SessionMiddleware)
    app.include_router(auth_bff_router)

    @app.get("/api/app/ping")
    async def ping(session: SessionContext = Depends(get_session)) -> dict[str, str]:
        return {"user": session.zitadel_user_id, "csrf": session.csrf_token}

    @app.post("/api/app/mutate")
    async def mutate(session: SessionContext = Depends(get_session)) -> dict[str, bool]:
        return {"ok": True}

    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# Helper: create a real session in the fake Redis and return the cookies.
# ---------------------------------------------------------------------------


async def _create_session(
    wire_redis: AsyncMock,
) -> tuple[str, str]:
    svc = SessionService()
    svc._fernet = None
    record = await svc.create(
        zitadel_user_id="user-42",
        org_id=7,
        access_token="at",
        refresh_token="rt",
        access_token_expires_at=int(time.time()) + 3600,
        id_token="idt",
        user_agent="pytest",
        remote_ip="127.0.0.1",
    )
    return record.sid, record.csrf_token


# ---------------------------------------------------------------------------
# GET /api/auth/session
# ---------------------------------------------------------------------------


class TestReadSession:
    def test_returns_401_without_cookie(self, client: TestClient) -> None:
        resp = client.get("/api/auth/session")
        assert resp.status_code == 401
        assert resp.json() == {"detail": "no_session"}

    def test_returns_401_for_unknown_sid(self, client: TestClient, wire_redis: AsyncMock) -> None:
        resp = client.get(
            "/api/auth/session",
            cookies={SESSION_COOKIE_NAME: "does-not-exist"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_returns_session_info_for_valid_cookie(
        self, app: FastAPI, wire_redis: AsyncMock
    ) -> None:
        sid, csrf = await _create_session(wire_redis)
        client = TestClient(app)
        resp = client.get(
            "/api/auth/session", cookies={SESSION_COOKIE_NAME: sid}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["authenticated"] is True
        assert body["zitadel_user_id"] == "user-42"
        assert body["csrf_token"] == csrf


# ---------------------------------------------------------------------------
# POST /api/auth/logout
# ---------------------------------------------------------------------------


class TestLogout:
    def test_logout_without_session_is_204(self, client: TestClient) -> None:
        resp = client.post("/api/auth/logout")
        assert resp.status_code == 204

    def test_logout_without_session_clears_cookies_anyway(
        self, client: TestClient
    ) -> None:
        resp = client.post("/api/auth/logout")
        set_cookies = resp.headers.get_list("set-cookie")
        assert any(SESSION_COOKIE_NAME in c and "Max-Age=0" in c for c in set_cookies)
        assert any(CSRF_COOKIE_NAME in c and "Max-Age=0" in c for c in set_cookies)

    @pytest.mark.asyncio
    async def test_logout_revokes_session_and_clears_cookies(
        self, app: FastAPI, wire_redis: AsyncMock
    ) -> None:
        sid, csrf = await _create_session(wire_redis)
        client = TestClient(app)
        # Mutating logout requires the CSRF header
        resp = client.post(
            "/api/auth/logout",
            cookies={SESSION_COOKIE_NAME: sid, CSRF_COOKIE_NAME: csrf},
            headers={CSRF_HEADER_NAME: csrf},
        )
        assert resp.status_code == 204
        assert wire_redis._store == {}
        # Subsequent /session call is now unauthenticated
        after = client.get("/api/auth/session", cookies={SESSION_COOKIE_NAME: sid})
        assert after.status_code == 401


# ---------------------------------------------------------------------------
# CSRF enforcement on state-changing methods
# ---------------------------------------------------------------------------


class TestCsrfEnforcement:
    def test_get_is_csrf_safe(self, client: TestClient) -> None:
        # No session, no cookies — GET still passes through middleware cleanly
        resp = client.get("/api/app/ping")
        assert resp.status_code == 401  # 401 from get_session, not 403 CSRF

    @pytest.mark.asyncio
    async def test_post_with_session_requires_matching_csrf(
        self, app: FastAPI, wire_redis: AsyncMock
    ) -> None:
        sid, csrf = await _create_session(wire_redis)
        client = TestClient(app)
        # Missing header
        resp = client.post("/api/app/mutate", cookies={SESSION_COOKIE_NAME: sid})
        assert resp.status_code == 403
        assert resp.json()["detail"] == "csrf_invalid"
        # Wrong header
        resp = client.post(
            "/api/app/mutate",
            cookies={SESSION_COOKIE_NAME: sid},
            headers={CSRF_HEADER_NAME: "wrong"},
        )
        assert resp.status_code == 403
        # Correct header
        resp = client.post(
            "/api/app/mutate",
            cookies={SESSION_COOKIE_NAME: sid},
            headers={CSRF_HEADER_NAME: csrf},
        )
        assert resp.status_code == 200

    def test_post_without_session_bypasses_csrf(self, client: TestClient) -> None:
        # Without a session there's no csrf token to compare against, so the
        # middleware does not impose CSRF; the route itself rejects with 401.
        resp = client.post("/api/app/mutate")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_exempt_prefix_skips_csrf(
        self, app: FastAPI, wire_redis: AsyncMock
    ) -> None:
        # /internal/ and /partner/ etc. must never be CSRF-blocked even when a
        # session happens to be attached.
        sid, _csrf = await _create_session(wire_redis)
        app = FastAPI()
        app.add_middleware(SessionMiddleware)

        @app.post("/internal/webhook")
        async def webhook():  # type: ignore[no-untyped-def]
            return {"ok": True}

        client = TestClient(app)
        resp = client.post("/internal/webhook", cookies={SESSION_COOKIE_NAME: sid})
        assert resp.status_code == 200
