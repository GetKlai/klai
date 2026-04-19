"""Legacy auth middleware tests.

Post SPEC-SEC-011, the authoritative coverage lives in
``test_middleware_auth.py``. This file is kept as a thin shim for
backwards compatibility — it re-runs the basic happy/sad path against the
real middleware (no mocking of ``knowledge_ingest.middleware.auth.settings``)
to make sure nothing else in the tree relies on the removed fail-open branch.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

TEST_SECRET = "test-secret-value-123"


@pytest.fixture
def secured_client():
    """Client against the real app — conftest already sets KNOWLEDGE_INGEST_SECRET."""
    mock_pool = MagicMock()
    mock_pool.close = AsyncMock(return_value=None)

    with (
        patch(
            "knowledge_ingest.qdrant_store.ensure_collection",
            new_callable=AsyncMock,
        ),
        patch(
            "knowledge_ingest.db.get_pool",
            new_callable=AsyncMock,
            return_value=mock_pool,
        ),
        patch("knowledge_ingest.db.close_pool", new_callable=AsyncMock),
        patch("knowledge_ingest.config.settings.enrichment_enabled", False),
    ):
        from knowledge_ingest.app import app

        with TestClient(app, raise_server_exceptions=False) as c:
            yield c


def test_health_without_secret(secured_client):
    """GET /health should always work without auth — middleware must not block it."""
    mock_resp = MagicMock(status_code=200)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(
        return_value=MagicMock(get=AsyncMock(return_value=mock_resp))
    )
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("knowledge_ingest.app.settings.graphiti_enabled", False),
        patch("qdrant_client.AsyncQdrantClient") as mock_qc,
        patch("httpx.AsyncClient", return_value=mock_ctx),
    ):
        mock_qc.return_value.get_collections = AsyncMock(return_value=[])
        resp = secured_client.get("/health")

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_request_without_secret_returns_401(secured_client):
    """Request without X-Internal-Secret should return 401."""
    resp = secured_client.post(
        "/ingest/v1/document",
        json={
            "org_id": "org1",
            "kb_slug": "test",
            "path": "test.md",
            "content": "hello",
        },
    )
    assert resp.status_code == 401
    assert "X-Internal-Secret" in resp.json()["detail"]


def test_request_with_wrong_secret_returns_401(secured_client):
    """Request with incorrect X-Internal-Secret should return 401."""
    resp = secured_client.post(
        "/ingest/v1/document",
        json={
            "org_id": "org1",
            "kb_slug": "test",
            "path": "test.md",
            "content": "hello",
        },
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
            json={
                "org_id": "org1",
                "kb_slug": "test",
                "path": "test.md",
                "content": "hello",
            },
            headers={"X-Internal-Secret": TEST_SECRET},
        )
        assert resp.status_code == 200
