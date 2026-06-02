"""Tests for taxonomy-node cleanup calls from portal to knowledge-ingest."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_remove_taxonomy_node_from_chunks_calls_ingest_endpoint():
    from app.services.knowledge_ingest_client import remove_taxonomy_node_from_chunks

    mock_resp = MagicMock()
    mock_resp.json = MagicMock(return_value={"chunks_updated": 12})
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("app.services.knowledge_ingest_client.httpx.AsyncClient", return_value=mock_client):
        result = await remove_taxonomy_node_from_chunks("org1", "support", 5)

    assert result == {"chunks_updated": 12}
    mock_client.post.assert_called_once_with(
        "/ingest/v1/taxonomy/remove-node",
        json={"org_id": "org1", "kb_slug": "support", "node_id": 5},
    )
