"""SPEC-TI-006 / C-9 -- Moneybird webhook replay-protection tests.

Verifies that the WebhookNonceStore integration in app/api/webhooks.py
correctly blocks replayed events (409), accepts fresh events (200), and
returns 503 when Redis is unavailable.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.webhooks import _moneybird_nonce_store
from app.api.webhooks import router as webhooks_router
from app.core.database import get_db

_VALID_TOKEN = "test-moneybird-webhook-token"

# Shared payload for a mandate-request event that passes auth
_MANDATE_PAYLOAD = {
    "webhook_token": _VALID_TOKEN,
    "entity_type": "Contact",
    "event": "contact_mandate_request_succeeded",
    "entity": {"id": "123"},
    "entity_id": "entity-abc",
    "created_at": "2026-05-06T10:00:00Z",
}


@pytest.fixture
def moneybird_app() -> FastAPI:
    """Minimal FastAPI app with the webhooks router and a no-op DB stub."""
    app = FastAPI()
    app.include_router(webhooks_router)

    async def _fake_db():
        class _Stub:
            async def execute(self, *a, **kw):
                return MagicMock(scalar_one_or_none=lambda: None)

            async def commit(self):
                pass

        yield _Stub()

    app.dependency_overrides[get_db] = _fake_db
    return app


@pytest.fixture
def fresh_store():
    """Reset the module-level nonce store before/after each test."""
    _moneybird_nonce_store.reset_client()
    yield _moneybird_nonce_store
    _moneybird_nonce_store.reset_client()


def test_fresh_delivery_returns_200(moneybird_app: FastAPI, fresh_store: Any) -> None:
    """A new delivery_id passes the replay check and returns 200."""
    import fakeredis.aioredis

    fake = fakeredis.aioredis.FakeRedis()
    fresh_store.set_client(fake)

    with TestClient(moneybird_app) as client:
        response = client.post("/api/webhooks/moneybird", json=_MANDATE_PAYLOAD)

    assert response.status_code == 200


def test_replay_delivery_returns_409(moneybird_app: FastAPI, fresh_store: Any) -> None:
    """The same nonce parts presented twice returns 409 replay_blocked."""
    import fakeredis.aioredis

    fake = fakeredis.aioredis.FakeRedis()
    fresh_store.set_client(fake)

    with TestClient(moneybird_app) as client:
        first = client.post("/api/webhooks/moneybird", json=_MANDATE_PAYLOAD)
        second = client.post("/api/webhooks/moneybird", json=_MANDATE_PAYLOAD)

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json() == {"detail": "replay_blocked"}


def test_redis_unavailable_returns_503(moneybird_app: FastAPI, fresh_store: Any) -> None:
    """When Redis is unreachable, the handler returns 503 (fail-closed)."""

    class _BrokenRedis:
        async def set(self, *args, **kwargs):
            raise RuntimeError("Connection refused")

    fresh_store.set_client(_BrokenRedis())

    with TestClient(moneybird_app) as client:
        response = client.post("/api/webhooks/moneybird", json=_MANDATE_PAYLOAD)

    assert response.status_code == 503
    assert response.json() == {"detail": "webhook_replay_protection_unavailable"}
