"""Tests for SPEC-TI-010C B-8: X-Caller-Service enforcement on stats endpoints.

Both GET /ingest/v1/source-count and GET /ingest/v1/graph-stats must:
- Return 403 when X-Caller-Service header is absent
- Return 403 when X-Caller-Service is not in KNOWN_CALLER_SERVICES
- Accept requests from known callers (e.g. portal-api)
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("KNOWLEDGE_INGEST_SECRET", "test-secret-value-123")
os.environ.setdefault("PORTAL_INTERNAL_TOKEN", "test-portal-internal-token-456")
os.environ.setdefault("GITEA_WEBHOOK_SECRET", "test-gitea-webhook-secret-789")

_INTERNAL_HEADER = {"X-Internal-Secret": os.environ["KNOWLEDGE_INGEST_SECRET"]}
_VALID_CALLER_HEADER = {**_INTERNAL_HEADER, "X-Caller-Service": "portal-api"}


@pytest.fixture
def client():
    """Test client with mocked startup dependencies."""
    mock_pool = MagicMock()
    mock_pool.close = AsyncMock(return_value=None)
    mock_pool.fetchval = AsyncMock(return_value=0)

    with (
        patch("knowledge_ingest.qdrant_store.ensure_collection", new_callable=AsyncMock),
        patch("knowledge_ingest.db.get_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch("knowledge_ingest.db.close_pool", new_callable=AsyncMock),
        patch("knowledge_ingest.config.settings.enrichment_enabled", False),
    ):
        from fastapi.testclient import TestClient
        from knowledge_ingest.app import app

        with TestClient(app, raise_server_exceptions=False) as c:
            yield c


# ---------------------------------------------------------------------------
# GET /ingest/v1/source-count
# ---------------------------------------------------------------------------


class TestSourceCountCallerService:
    def test_missing_caller_service_returns_403(self, client):
        """source-count without X-Caller-Service must return 403."""
        resp = client.get(
            "/ingest/v1/source-count",
            headers=_INTERNAL_HEADER,
            params={"org_id": "org-1", "kb_slug": "kb-1"},
        )
        assert resp.status_code == 403, (
            "SPEC-TI-010C B-8: source-count must reject requests without X-Caller-Service"
        )

    def test_unknown_caller_service_returns_403(self, client):
        """source-count with an unknown caller service must return 403."""
        resp = client.get(
            "/ingest/v1/source-count",
            headers={**_INTERNAL_HEADER, "X-Caller-Service": "unknown-attacker"},
            params={"org_id": "org-1", "kb_slug": "kb-1"},
        )
        assert resp.status_code == 403, (
            "SPEC-TI-010C B-8: source-count must reject unknown X-Caller-Service values"
        )

    def test_known_caller_service_passes(self, client):
        """source-count with portal-api caller service must succeed."""
        mock_pool = MagicMock()
        mock_pool.fetchval = AsyncMock(return_value=5)

        with patch("knowledge_ingest.db.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            resp = client.get(
                "/ingest/v1/source-count",
                headers=_VALID_CALLER_HEADER,
                params={"org_id": "org-1", "kb_slug": "kb-1"},
            )
        assert resp.status_code == 200, (
            f"portal-api is a KNOWN_CALLER_SERVICES entry — should pass: {resp.text}"
        )

    def test_known_callers_accepted(self, client):
        """All known caller services must be accepted by source-count."""
        from klai_identity_assert import KNOWN_CALLER_SERVICES

        mock_pool = MagicMock()
        mock_pool.fetchval = AsyncMock(return_value=0)

        with patch("knowledge_ingest.db.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            for service in KNOWN_CALLER_SERVICES:
                resp = client.get(
                    "/ingest/v1/source-count",
                    headers={**_INTERNAL_HEADER, "X-Caller-Service": service},
                    params={"org_id": "org-1", "kb_slug": "kb-1"},
                )
                assert resp.status_code == 200, (
                    f"KNOWN_CALLER_SERVICES member '{service}' was rejected by source-count"
                )


# ---------------------------------------------------------------------------
# GET /ingest/v1/graph-stats
# ---------------------------------------------------------------------------


class TestGraphStatsCallerService:
    def test_missing_caller_service_returns_403(self, client):
        """graph-stats without X-Caller-Service must return 403."""
        resp = client.get(
            "/ingest/v1/graph-stats",
            headers=_INTERNAL_HEADER,
            params={"org_id": "org-1"},
        )
        assert resp.status_code == 403, (
            "SPEC-TI-010C B-8: graph-stats must reject requests without X-Caller-Service"
        )

    def test_unknown_caller_service_returns_403(self, client):
        """graph-stats with an unknown caller service must return 403."""
        resp = client.get(
            "/ingest/v1/graph-stats",
            headers={**_INTERNAL_HEADER, "X-Caller-Service": "malicious-service"},
            params={"org_id": "org-1"},
        )
        assert resp.status_code == 403, (
            "SPEC-TI-010C B-8: graph-stats must reject unknown X-Caller-Service values"
        )

    def test_known_caller_service_passes(self, client):
        """graph-stats with portal-api caller service must succeed (graphiti disabled)."""
        with patch("knowledge_ingest.config.settings.graphiti_enabled", False):
            resp = client.get(
                "/ingest/v1/graph-stats",
                headers=_VALID_CALLER_HEADER,
                params={"org_id": "org-1"},
            )
        # When graphiti is disabled the endpoint returns 200 with null counts
        assert resp.status_code == 200, (
            f"portal-api is a KNOWN_CALLER_SERVICES entry — should pass: {resp.text}"
        )
