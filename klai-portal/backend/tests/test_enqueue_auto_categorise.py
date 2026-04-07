"""Tests for SPEC-KB-026 R5: Portal enqueue_auto_categorise client function."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestPortalEnqueueAutoCategorise:
    """R5: Portal calls enqueue_auto_categorise instead of fire-and-forget."""

    @pytest.mark.asyncio
    async def test_enqueue_auto_categorise_calls_ingest(self):
        """enqueue_auto_categorise posts to the job endpoint."""
        from app.services.knowledge_ingest_client import enqueue_auto_categorise

        mock_resp = MagicMock()
        mock_resp.status_code = 202
        mock_resp.json = MagicMock(return_value={"job_id": 42, "status": "queued"})
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("app.services.knowledge_ingest_client.httpx.AsyncClient", return_value=mock_client):
            await enqueue_auto_categorise("org1", "kb1", 5, [0.1, 0.2])

        mock_client.post.assert_called_once()
        call_json = mock_client.post.call_args.kwargs["json"]
        assert call_json["org_id"] == "org1"
        assert call_json["node_id"] == 5
        assert call_json["cluster_centroid"] == [0.1, 0.2]

    @pytest.mark.asyncio
    async def test_enqueue_auto_categorise_handles_error(self):
        """On error, logs warning but does not raise."""
        from app.services.knowledge_ingest_client import enqueue_auto_categorise

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=Exception("connection refused"))

        with patch("app.services.knowledge_ingest_client.httpx.AsyncClient", return_value=mock_client):
            # Must not raise
            await enqueue_auto_categorise("org1", "kb1", 5, [0.1])

    @pytest.mark.asyncio
    async def test_enqueue_uses_correct_endpoint(self):
        """Must POST to /ingest/v1/taxonomy/auto-categorise-job."""
        from app.services.knowledge_ingest_client import enqueue_auto_categorise

        mock_resp = MagicMock()
        mock_resp.status_code = 202
        mock_resp.json = MagicMock(return_value={"job_id": 1, "status": "queued"})
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("app.services.knowledge_ingest_client.httpx.AsyncClient", return_value=mock_client):
            await enqueue_auto_categorise("org1", "kb1", 5, None)

        call_args = mock_client.post.call_args
        assert "/ingest/v1/taxonomy/auto-categorise-job" in str(call_args)
