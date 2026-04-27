"""Tests for the session-aware Bearer dependency (SPEC-AUTH-008 Phase A4).

The bearer() dep must transparently:
  - synthesise an HTTPAuthorizationCredentials from the BFF session cookie
    when a session was resolved by SessionMiddleware
  - fall through to the real Authorization header when no session exists
  - reject bearer-only requests once BFF_ENFORCE_COOKIES is flipped in Phase D
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from fastapi import Depends, FastAPI
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

from app.api.bearer import bearer
from app.core.config import settings
from app.core.session import SESSION_COOKIE_NAME
from app.middleware.session import SessionMiddleware
from app.services.bff_session import SessionService


@pytest.fixture(autouse=True)
def _configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "bff_session_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "sso_cookie_key", "")
    monkeypatch.setattr(settings, "bff_session_ttl_seconds", 86400)
    monkeypatch.setattr(settings, "domain", "getklai.com")
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
        count = sum(1 for k in keys if k in store)
        for k in keys:
            store.pop(k, None)
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
    return fake_redis


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(SessionMiddleware)

    @app.get("/protected")
    async def protected(
        credentials: HTTPAuthorizationCredentials = Depends(bearer),
    ) -> dict[str, str]:
        return {"token": credentials.credentials, "scheme": credentials.scheme}

    return app


async def _make_session(wire_redis: AsyncMock, access_token: str = "live-at") -> str:
    svc = SessionService()
    svc._fernet = None
    record = await svc.create(
        zitadel_user_id="u1",
        org_id=1,
        access_token=access_token,
        refresh_token="rt",
        access_token_expires_at=int(time.time()) + 3600,
        id_token="idt",
        user_agent=None,
        remote_ip=None,
    )
    return record.sid


class TestBearerRequiresSession:
    @pytest.mark.asyncio
    async def test_session_synthesises_bearer_credentials(self, app: FastAPI, wire_redis: AsyncMock) -> None:
        sid = await _make_session(wire_redis, access_token="token-from-session")
        client = TestClient(app)
        resp = client.get("/protected", cookies={SESSION_COOKIE_NAME: sid})
        assert resp.status_code == 200
        body = resp.json()
        assert body["token"] == "token-from-session"
        assert body["scheme"] == "Bearer"

    def test_bearer_only_request_is_rejected(self, app: FastAPI) -> None:
        client = TestClient(app)
        resp = client.get("/protected", headers={"Authorization": "Bearer legacy-at"})
        assert resp.status_code == 401
        assert resp.json()["detail"] == "cookie_required"

    def test_missing_everything_returns_401(self, app: FastAPI) -> None:
        client = TestClient(app)
        resp = client.get("/protected")
        assert resp.status_code == 401
        assert resp.json()["detail"] == "cookie_required"
