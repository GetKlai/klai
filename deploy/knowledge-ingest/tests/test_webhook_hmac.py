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
    with patch("knowledge_ingest.config.settings.gitea_webhook_secret", WEBHOOK_SECRET):
        with patch("knowledge_ingest.routes.ingest.settings") as mock_settings:
            mock_settings.gitea_webhook_secret = WEBHOOK_SECRET
            mock_settings.gitea_url = "http://gitea:3000"
            mock_settings.gitea_token = "test-token"
            mock_settings.chunk_size = 1500
            mock_settings.chunk_overlap = 200
            with patch(
                "knowledge_ingest.qdrant_store.ensure_collection",
                new_callable=AsyncMock,
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
