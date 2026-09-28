"""/health must surface a dead procrastinate lane (22-25 Sep 2026: lanes
stopped after a side-task PoolTimeout while /health stayed green)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    mock_pool = MagicMock()
    mock_pool.close = AsyncMock(return_value=None)
    with (
        patch("knowledge_ingest.qdrant_store.ensure_collection", new_callable=AsyncMock),
        patch("knowledge_ingest.db.get_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch("knowledge_ingest.db.close_pool", new_callable=AsyncMock),
        patch("knowledge_ingest.config.settings.enrichment_enabled", False),
    ):
        from knowledge_ingest.app import app

        with TestClient(app, raise_server_exceptions=False) as c:
            yield c


def _get_health(client):
    upstream = MagicMock()
    upstream.__aenter__ = AsyncMock(
        return_value=MagicMock(get=AsyncMock(return_value=MagicMock(status_code=200)))
    )
    upstream.__aexit__ = AsyncMock(return_value=False)
    with (
        patch("knowledge_ingest.app.settings.graphiti_enabled", False),
        patch("qdrant_client.AsyncQdrantClient") as qdrant,
        patch("httpx.AsyncClient", return_value=upstream),
    ):
        qdrant.return_value.get_collections = AsyncMock(return_value=[])
        return client.get("/health")


@pytest.mark.parametrize(("dead_lanes", "status"), [(set(), 200), ({"io"}, 503)])
def test_health_reports_dead_worker_lane(client, monkeypatch, dead_lanes, status):
    from knowledge_ingest.app import app

    monkeypatch.setattr(
        app.state, "worker_lifecycle", SimpleNamespace(dead_lanes=dead_lanes), raising=False
    )

    resp = _get_health(client)

    assert resp.status_code == status
    assert (resp.json()["procrastinate_workers"] == "ok") == (not dead_lanes)
