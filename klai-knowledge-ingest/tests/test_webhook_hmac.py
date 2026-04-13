"""Tests for HMAC webhook verification (TASK-009)."""
import hashlib
import hmac
import json

import pytest
from unittest.mock import AsyncMock, patch


WEBHOOK_SECRET = "gitea-webhook-secret-456"

VALID_PAYLOAD = {
    "ref": "refs/heads/main",
    "commits": [{"added": ["doc.md"], "modified": [], "removed": []}],
    "repository": {"full_name": "org-testslug/personal"},
    "pusher": {"name": "testuser", "login": "testuser"},
}


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def hmac_client():
    """Client with gitea_webhook_secret configured."""
    from unittest.mock import MagicMock

    mock_pool = MagicMock()
    mock_pool.execute = AsyncMock(return_value=None)
    mock_pool.fetch = AsyncMock(return_value=[])
    mock_pool.close = AsyncMock(return_value=None)

    with patch("knowledge_ingest.config.settings.gitea_webhook_secret", WEBHOOK_SECRET):
        with patch("knowledge_ingest.routes.ingest.settings") as mock_settings:
            mock_settings.gitea_webhook_secret = WEBHOOK_SECRET
            mock_settings.gitea_url = "http://gitea:3000"
            mock_settings.gitea_token = "test-token"
            mock_settings.chunk_size = 1500
            mock_settings.chunk_overlap = 200
            mock_settings.enrichment_enabled = False  # use immediate ingest path (no Procrastinate)
            with patch(
                "knowledge_ingest.qdrant_store.ensure_collection",
                new_callable=AsyncMock,
            ), patch(
                "knowledge_ingest.db.get_pool",
                new_callable=AsyncMock,
                return_value=mock_pool,
            ), patch(
                "knowledge_ingest.db.close_pool",
                new_callable=AsyncMock,
            ), patch(
                "knowledge_ingest.config.settings.enrichment_enabled",
                False,
            ):
                from knowledge_ingest.app import app
                from fastapi.testclient import TestClient

                with TestClient(app, raise_server_exceptions=False) as c:
                    yield c


def test_webhook_with_valid_hmac(hmac_client):
    """Valid HMAC signature should be accepted."""
    body = json.dumps(VALID_PAYLOAD).encode()
    sig = _sign(body, WEBHOOK_SECRET)

    with patch(
        "knowledge_ingest.routes.ingest._get_org_id",
        new_callable=AsyncMock,
        return_value="zitadel-org-123",
    ), patch(
        "knowledge_ingest.routes.ingest._fetch_gitea_file",
        new_callable=AsyncMock,
        return_value="# Test doc\nContent here",
    ), patch(
        "knowledge_ingest.routes.ingest.ingest_document",
        new_callable=AsyncMock,
        return_value={"status": "ok", "chunks": 1, "title": "Test doc"},
    ):
        resp = hmac_client.post(
            "/ingest/v1/webhook/gitea",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Gitea-Signature": sig,
            },
        )
        assert resp.status_code == 200


def test_webhook_without_signature_returns_401(hmac_client):
    """Missing X-Gitea-Signature should return 401."""
    body = json.dumps(VALID_PAYLOAD).encode()
    resp = hmac_client.post(
        "/ingest/v1/webhook/gitea",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 401
    assert "Signature" in resp.json()["detail"]


def test_webhook_with_wrong_signature_returns_401(hmac_client):
    """Wrong signature should return 401."""
    body = json.dumps(VALID_PAYLOAD).encode()
    resp = hmac_client.post(
        "/ingest/v1/webhook/gitea",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Gitea-Signature": "deadbeef" * 8,
        },
    )
    assert resp.status_code == 401
    assert "Invalid" in resp.json()["detail"]


_USER_UUID = "abc123-user-uuid"

PERSONAL_KB_PAYLOAD = {
    "ref": "refs/heads/main",
    "commits": [{"added": [f"users/{_USER_UUID}/my-note.md"], "modified": [], "removed": []}],
    "repository": {"full_name": "org-testslug/personal"},
    "pusher": {"name": "testuser", "login": "testuser"},
}


def test_webhook_personal_kb_extracts_user_id(hmac_client):
    """Personal KB webhook: user_id must be extracted from 'users/{uuid}/...' path."""
    body = json.dumps(PERSONAL_KB_PAYLOAD).encode()
    sig = _sign(body, WEBHOOK_SECRET)

    captured: list = []

    async def _fake_ingest(req):
        captured.append(req)
        return {"status": "ok", "chunks": 1, "title": "Note"}

    with patch(
        "knowledge_ingest.routes.ingest._get_org_id",
        new_callable=AsyncMock,
        return_value="zitadel-org-123",
    ), patch(
        "knowledge_ingest.routes.ingest._fetch_gitea_file",
        new_callable=AsyncMock,
        return_value="# Note\nContent",
    ), patch(
        "knowledge_ingest.routes.ingest.ingest_document",
        side_effect=_fake_ingest,
    ):
        resp = hmac_client.post(
            "/ingest/v1/webhook/gitea",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Gitea-Signature": sig,
            },
        )

    assert resp.status_code == 200
    assert len(captured) == 1
    req = captured[0]
    assert req.user_id == _USER_UUID, f"Expected user_id={_USER_UUID!r}, got {req.user_id!r}"
    assert req.kb_slug == f"personal-{_USER_UUID}"


def test_webhook_non_personal_kb_no_user_id(hmac_client):
    """Non-personal KB webhook: user_id must remain None even if path looks like users/..."""
    payload = {
        "ref": "refs/heads/main",
        "commits": [{"added": [f"users/{_USER_UUID}/doc.md"], "modified": [], "removed": []}],
        "repository": {"full_name": "org-testslug/team-kb"},
        "pusher": {"name": "testuser", "login": "testuser"},
    }
    body = json.dumps(payload).encode()
    sig = _sign(body, WEBHOOK_SECRET)

    captured: list = []

    async def _fake_ingest(req):
        captured.append(req)
        return {"status": "ok", "chunks": 1, "title": "Doc"}

    with patch(
        "knowledge_ingest.routes.ingest._get_org_id",
        new_callable=AsyncMock,
        return_value="zitadel-org-123",
    ), patch(
        "knowledge_ingest.routes.ingest._fetch_gitea_file",
        new_callable=AsyncMock,
        return_value="# Doc\nContent",
    ), patch(
        "knowledge_ingest.routes.ingest.ingest_document",
        side_effect=_fake_ingest,
    ):
        resp = hmac_client.post(
            "/ingest/v1/webhook/gitea",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Gitea-Signature": sig,
            },
        )

    assert resp.status_code == 200
    assert len(captured) == 1
    assert captured[0].user_id is None
