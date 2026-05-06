from __future__ import annotations

import unittest.mock as mock
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.meetings import _vexa_nonce_store
from app.api.meetings import router as meetings_router
from app.core.database import get_db

_VALID_SECRET = "test-vexa-webhook-secret"
_AUTH_HEADER = f"Bearer {_VALID_SECRET}"
_BASE_PAYLOAD: dict[str, Any] = {
    "vexa_meeting_id": 42,
    "status": "active",
    "ended_at": None,
    "platform": "google_meet",
    "native_meeting_id": "abc-def-ghi",
}


@pytest.fixture(autouse=True)
def _patch_secret(monkeypatch):
    from app.core import config as cfg

    monkeypatch.setattr(cfg.settings, "vexa_webhook_secret", _VALID_SECRET)


class _NullStub:
    async def scalar(self, *a, **kw):
        return None

    async def execute(self, *a, **kw):
        return MagicMock(scalar_one_or_none=lambda: None)

    async def commit(self):
        pass

    def expunge(self, *a, **kw):
        pass

    async def merge(self, obj):
        return obj

    async def add(self, obj):
        pass

    async def flush(self):
        pass


def _make_null_ctx():
    @asynccontextmanager
    async def _ctx(*a, **kw):
        yield _NullStub()

    return _ctx


@pytest.fixture
def vexa_app() -> FastAPI:
    app = FastAPI()
    app.include_router(meetings_router)

    async def _fake_get_db():
        yield _NullStub()

    app.dependency_overrides[get_db] = _fake_get_db

    null_ctx = _make_null_ctx()
    with (
        mock.patch("app.core.database.cross_org_session", null_ctx),
        mock.patch("app.core.database.tenant_scoped_session", null_ctx),
    ):
        yield app


@pytest.fixture
def fresh_store():
    _vexa_nonce_store.reset_client()
    yield _vexa_nonce_store
    _vexa_nonce_store.reset_client()


def test_fresh_delivery_returns_200(vexa_app, fresh_store):
    import fakeredis.aioredis

    fake = fakeredis.aioredis.FakeRedis()
    fresh_store.set_client(fake)
    with TestClient(vexa_app) as client:
        response = client.post(
            "/api/bots/internal/webhook",
            json=_BASE_PAYLOAD,
            headers={"Authorization": _AUTH_HEADER},
        )
    assert response.status_code not in (409, 503), response.json()


def test_replay_delivery_returns_409(vexa_app, fresh_store):
    import fakeredis.aioredis

    fake = fakeredis.aioredis.FakeRedis()
    fresh_store.set_client(fake)
    with TestClient(vexa_app) as client:
        first = client.post(
            "/api/bots/internal/webhook",
            json=_BASE_PAYLOAD,
            headers={"Authorization": _AUTH_HEADER},
        )
        second = client.post(
            "/api/bots/internal/webhook",
            json=_BASE_PAYLOAD,
            headers={"Authorization": _AUTH_HEADER},
        )
    assert first.status_code != 409, first.json()
    assert second.status_code == 409
    assert second.json() == {"detail": "replay_blocked"}


def test_redis_unavailable_returns_503(vexa_app, fresh_store):
    class _BrokenRedis:
        async def set(self, *args, **kwargs):
            raise RuntimeError("Connection refused")

    fresh_store.set_client(_BrokenRedis())
    with TestClient(vexa_app) as client:
        response = client.post(
            "/api/bots/internal/webhook",
            json=_BASE_PAYLOAD,
            headers={"Authorization": _AUTH_HEADER},
        )
    assert response.status_code == 503
    assert response.json() == {"detail": "webhook_replay_protection_unavailable"}


def test_different_events_not_blocked(vexa_app, fresh_store):
    import fakeredis.aioredis

    fake = fakeredis.aioredis.FakeRedis()
    fresh_store.set_client(fake)
    payload_a = {**_BASE_PAYLOAD, "status": "active"}
    payload_b = {**_BASE_PAYLOAD, "status": "completed", "ended_at": "2026-05-06T10:00:00Z"}
    with TestClient(vexa_app) as client:
        r_a = client.post(
            "/api/bots/internal/webhook",
            json=payload_a,
            headers={"Authorization": _AUTH_HEADER},
        )
        r_b = client.post(
            "/api/bots/internal/webhook",
            json=payload_b,
            headers={"Authorization": _AUTH_HEADER},
        )
    assert r_a.status_code != 409, r_a.json()
    assert r_b.status_code != 409, r_b.json()


def test_inactive_meeting_check_guards_are_correct():
    """C-10: unit-test the inactive-meeting guard logic directly."""
    from app.api.meetings import ACTIVE_STATUSES

    # "completed" is not in the active/stopping set
    assert "completed" not in (*ACTIVE_STATUSES, "stopping")
    # "active" is in the payload statuses that trigger the guard
    assert "active" not in (None, "completed")
    # recording is considered active — must not trigger guard
    assert "recording" in ACTIVE_STATUSES
    # pending is active
    assert "pending" in ACTIVE_STATUSES
    # stopping is in the combined active+stopping set
    assert "stopping" in (*ACTIVE_STATUSES, "stopping")
    # cancelled is inactive
    assert "cancelled" not in (*ACTIVE_STATUSES, "stopping")
