from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

_WEBHOOK_SECRET = "test-gitea-webhook-secret-789"
_DELIVERY_ID = "deliver-y-abc-123"

_VALID_PAYLOAD = {
    "ref": "refs/heads/main",
    "commits": [{"added": ["doc.md"], "modified": [], "removed": []}],
    "repository": {"full_name": "org-testslug/personal"},
    "pusher": {"name": "testuser", "login": "testuser"},
}


def _sign(body: bytes) -> str:
    return hmac.new(_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()


def _make_body(payload=None):
    return json.dumps(payload or _VALID_PAYLOAD).encode()


@pytest.fixture
def gitea_app_and_store():
    mock_pool = MagicMock()
    mock_pool.execute = AsyncMock(return_value=None)
    mock_pool.fetch = AsyncMock(return_value=[])
    mock_pool.fetchrow = AsyncMock(return_value=None)
    mock_pool.close = AsyncMock(return_value=None)

    with (
        patch("knowledge_ingest.qdrant_store.ensure_collection", new_callable=AsyncMock),
        patch("knowledge_ingest.db.get_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch("knowledge_ingest.db.close_pool", new_callable=AsyncMock),
        patch("knowledge_ingest.config.settings.enrichment_enabled", False),
        patch("knowledge_ingest.routes.ingest.settings") as mock_settings,
    ):
        mock_settings.gitea_webhook_secret = _WEBHOOK_SECRET
        mock_settings.gitea_url = "http://gitea:3000"
        mock_settings.gitea_token = "test-token"
        mock_settings.chunk_size = 1500
        mock_settings.chunk_overlap = 200
        mock_settings.enrichment_enabled = False
        mock_settings.redis_url = "redis://localhost:6379/0"

        from knowledge_ingest.app import app
        from knowledge_ingest.routes.ingest import _gitea_nonce_store

        yield app, _gitea_nonce_store, mock_pool


@pytest.fixture
def gitea_client(gitea_app_and_store: Any):
    import os

    app, nonce_store, pool = gitea_app_and_store
    nonce_store.reset_client()
    with TestClient(app, raise_server_exceptions=False) as client:
        client.headers.update({"X-Internal-Secret": os.environ["KNOWLEDGE_INGEST_SECRET"]})
        yield client, nonce_store, pool
    nonce_store.reset_client()


def test_missing_signature_returns_401(gitea_client: Any) -> None:
    client, _, _ = gitea_client
    body = _make_body()
    resp = client.post(
        "/ingest/v1/webhook/gitea",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 401
    assert "Signature" in resp.json()["detail"]


def test_forged_signature_returns_401(gitea_client: Any) -> None:
    client, _, _ = gitea_client
    body = _make_body()
    resp = client.post(
        "/ingest/v1/webhook/gitea",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Gitea-Signature": "deadbeef" * 8,
        },
    )
    assert resp.status_code == 401
    assert "Invalid" in resp.json()["detail"]


def test_valid_signature_passes_hmac_check(gitea_client: Any) -> None:
    import fakeredis.aioredis

    client, nonce_store, mock_pool = gitea_client
    fake = fakeredis.aioredis.FakeRedis()
    nonce_store.set_client(fake)

    body = _make_body()
    sig = _sign(body)
    mock_pool.fetchrow = AsyncMock(return_value=None)

    resp = client.post(
        "/ingest/v1/webhook/gitea",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Gitea-Signature": sig,
            "X-Gitea-Delivery": "unique-delivery-1",
        },
    )
    assert resp.status_code == 200
    assert resp.json().get("status") == "ignored"


def test_duplicate_delivery_id_returns_409(gitea_client: Any) -> None:
    import fakeredis.aioredis

    client, nonce_store, _ = gitea_client
    fake = fakeredis.aioredis.FakeRedis()
    nonce_store.set_client(fake)

    body = _make_body()
    sig = _sign(body)
    headers = {
        "Content-Type": "application/json",
        "X-Gitea-Signature": sig,
        "X-Gitea-Delivery": _DELIVERY_ID,
    }

    first = client.post("/ingest/v1/webhook/gitea", content=body, headers=headers)
    second = client.post("/ingest/v1/webhook/gitea", content=body, headers=headers)

    assert first.status_code != 409, first.json()
    assert second.status_code == 409
    assert second.json() == {"detail": "replay_blocked"}


def test_missing_delivery_id_skips_replay_check(gitea_client: Any) -> None:
    import fakeredis.aioredis

    client, nonce_store, mock_pool = gitea_client
    fake = fakeredis.aioredis.FakeRedis()
    nonce_store.set_client(fake)

    body = _make_body()
    sig = _sign(body)
    mock_pool.fetchrow = AsyncMock(return_value=None)

    resp = client.post(
        "/ingest/v1/webhook/gitea",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Gitea-Signature": sig,
        },
    )
    assert resp.status_code != 409, resp.json()


def test_redis_unavailable_returns_503(gitea_client: Any) -> None:
    class _BrokenRedis:
        async def set(self, *args, **kwargs):
            raise RuntimeError("Connection refused")

    client, nonce_store, _ = gitea_client
    nonce_store.set_client(_BrokenRedis())

    body = _make_body()
    sig = _sign(body)
    headers = {
        "Content-Type": "application/json",
        "X-Gitea-Signature": sig,
        "X-Gitea-Delivery": "unique-delivery-503",
    }

    resp = client.post("/ingest/v1/webhook/gitea", content=body, headers=headers)
    assert resp.status_code == 503
    assert resp.json() == {"detail": "webhook_replay_protection_unavailable"}


def test_known_repo_mapping_resolves_org_id(gitea_client: Any) -> None:
    import fakeredis.aioredis

    client, nonce_store, mock_pool = gitea_client
    fake = fakeredis.aioredis.FakeRedis()
    nonce_store.set_client(fake)

    row = {"org_id": "zitadel-org-abc123"}
    mock_pool.fetchrow = AsyncMock(return_value=row)

    body = _make_body()
    sig = _sign(body)
    headers = {
        "Content-Type": "application/json",
        "X-Gitea-Signature": sig,
        "X-Gitea-Delivery": "unique-delivery-known",
    }

    with (
        patch("knowledge_ingest.routes.ingest._get_org_id", new_callable=AsyncMock) as old_fn,
        patch(
            "knowledge_ingest.routes.ingest._fetch_gitea_file",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "knowledge_ingest.routes.ingest.ingest_document",
            new_callable=AsyncMock,
            return_value={"status": "ok", "chunks": 0},
        ),
    ):
        resp = client.post("/ingest/v1/webhook/gitea", content=body, headers=headers)
        old_fn.assert_not_called()

    assert resp.status_code == 200


def test_unknown_repo_returns_ignored_not_gitea_api_fallback(gitea_client: Any) -> None:
    import fakeredis.aioredis

    client, nonce_store, mock_pool = gitea_client
    fake = fakeredis.aioredis.FakeRedis()
    nonce_store.set_client(fake)

    mock_pool.fetchrow = AsyncMock(return_value=None)

    body = _make_body()
    sig = _sign(body)
    headers = {
        "Content-Type": "application/json",
        "X-Gitea-Signature": sig,
        "X-Gitea-Delivery": "unique-delivery-unknown",
    }

    with patch("knowledge_ingest.routes.ingest._get_org_id", new_callable=AsyncMock) as old_fn:
        resp = client.post("/ingest/v1/webhook/gitea", content=body, headers=headers)
        old_fn.assert_not_called()

    assert resp.status_code == 200
    body_json = resp.json()
    assert body_json.get("status") == "ignored"
    assert body_json.get("reason", "") != ""  # reason set: org mapping not found
