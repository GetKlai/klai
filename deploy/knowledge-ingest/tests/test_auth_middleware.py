"""Tests for the InternalSecretMiddleware (TASK-002)."""
import pytest
from unittest.mock import AsyncMock, patch


TEST_SECRET = "test-secret-value-123"


@pytest.fixture
def secured_client():
    """Client with knowledge_ingest_secret configured."""
    from unittest.mock import MagicMock
    mock_pool = MagicMock()
    mock_pool.close = AsyncMock(return_value=None)

    with patch("knowledge_ingest.config.settings.knowledge_ingest_secret", TEST_SECRET), \
         patch("knowledge_ingest.middleware.auth.settings") as mock_settings, \
         patch("knowledge_ingest.qdrant_store.ensure_collection", new_callable=AsyncMock), \
         patch("knowledge_ingest.db.get_pool", new_callable=AsyncMock, return_value=mock_pool), \
         patch("knowledge_ingest.db.close_pool", new_callable=AsyncMock), \
         patch("knowledge_ingest.config.settings.enrichment_enabled", False):
        mock_settings.knowledge_ingest_secret = TEST_SECRET
        from knowledge_ingest.app import app
        from fastapi.testclient import TestClient

        with TestClient(app, raise_server_exceptions=False) as c:
            yield c


def test_health_without_secret(secured_client):
    """GET /health should always work without auth."""
    resp = secured_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_request_without_secret_returns_401(secured_client):
    """Request without X-Internal-Secret should return 401."""
    resp = secured_client.post(
        "/ingest/v1/document",
        json={"org_id": "org1", "kb_slug": "test", "path": "test.md", "content": "hello"},
    )
    assert resp.status_code == 401
    assert "X-Internal-Secret" in resp.json()["detail"]


def test_request_with_wrong_secret_returns_401(secured_client):
    """Request with incorrect X-Internal-Secret should return 401."""
    resp = secured_client.post(
        "/ingest/v1/document",
        json={"org_id": "org1", "kb_slug": "test", "path": "test.md", "content": "hello"},
        headers={"X-Internal-Secret": "wrong-secret"},
    )
    assert resp.status_code == 401


def test_request_with_correct_secret_passes(secured_client):
    """Request with correct secret should pass middleware (may fail downstream)."""
    with patch(
        "knowledge_ingest.routes.ingest.ingest_document",
        new_callable=AsyncMock,
        return_value={"status": "ok", "chunks": 1, "title": "test"},
    ):
        resp = secured_client.post(
            "/ingest/v1/document",
            json={"org_id": "org1", "kb_slug": "test", "path": "test.md", "content": "hello"},
            headers={"X-Internal-Secret": TEST_SECRET},
        )
        assert resp.status_code == 200


def test_no_secret_configured_skips_auth(client):
    """When secret is empty, all requests should pass through middleware."""
    with patch(
        "knowledge_ingest.routes.ingest.ingest_document",
        new_callable=AsyncMock,
        return_value={"status": "ok", "chunks": 1, "title": "test"},
    ):
        resp = client.post(
            "/ingest/v1/document",
            json={"org_id": "org1", "kb_slug": "test", "path": "test.md", "content": "hello"},
        )
        assert resp.status_code == 200
