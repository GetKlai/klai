"""Tests for taxonomy_node_id in qdrant_store payload handling."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from knowledge_ingest.qdrant_store import upsert_chunks


@pytest.fixture
def mock_qdrant_client():
    client = AsyncMock()
    client.delete = AsyncMock()
    client.upsert = AsyncMock()
    return client


class TestUpsertChunksTaxonomy:
    @pytest.mark.asyncio
    async def test_taxonomy_node_id_stored_when_has_taxonomy_true(self, mock_qdrant_client):
        """When has_taxonomy=True and node_id is set, payload includes taxonomy_node_id."""
        with patch("knowledge_ingest.qdrant_store.get_client", return_value=mock_qdrant_client):
            await upsert_chunks(
                org_id="org1",
                kb_slug="kb1",
                path="doc.md",
                chunks=["chunk text"],
                vectors=[[0.1, 0.2]],
                artifact_id="art1",
                taxonomy_node_id=5,
                has_taxonomy=True,
            )

        mock_qdrant_client.upsert.assert_called_once()
        points = mock_qdrant_client.upsert.call_args.kwargs["points"]
        assert len(points) == 1
        assert points[0].payload["taxonomy_node_id"] == 5

    @pytest.mark.asyncio
    async def test_taxonomy_node_id_null_when_no_match(self, mock_qdrant_client):
        """When has_taxonomy=True but no node matched, payload has taxonomy_node_id=null."""
        with patch("knowledge_ingest.qdrant_store.get_client", return_value=mock_qdrant_client):
            await upsert_chunks(
                org_id="org1",
                kb_slug="kb1",
                path="doc.md",
                chunks=["chunk text"],
                vectors=[[0.1, 0.2]],
                artifact_id="art1",
                taxonomy_node_id=None,
                has_taxonomy=True,
            )

        points = mock_qdrant_client.upsert.call_args.kwargs["points"]
        assert "taxonomy_node_id" in points[0].payload
        assert points[0].payload["taxonomy_node_id"] is None

    @pytest.mark.asyncio
    async def test_taxonomy_node_id_absent_when_no_taxonomy(self, mock_qdrant_client):
        """When has_taxonomy=False, taxonomy_node_id is NOT stored in payload at all."""
        with patch("knowledge_ingest.qdrant_store.get_client", return_value=mock_qdrant_client):
            await upsert_chunks(
                org_id="org1",
                kb_slug="kb1",
                path="doc.md",
                chunks=["chunk text"],
                vectors=[[0.1, 0.2]],
                artifact_id="art1",
                taxonomy_node_id=None,
                has_taxonomy=False,
            )

        points = mock_qdrant_client.upsert.call_args.kwargs["points"]
        assert "taxonomy_node_id" not in points[0].payload

    @pytest.mark.asyncio
    async def test_backward_compatible_default_has_no_taxonomy(self, mock_qdrant_client):
        """Calling without taxonomy args (old callers) must not add taxonomy_node_id."""
        with patch("knowledge_ingest.qdrant_store.get_client", return_value=mock_qdrant_client):
            await upsert_chunks(
                org_id="org1",
                kb_slug="kb1",
                path="doc.md",
                chunks=["chunk"],
                vectors=[[0.1, 0.2]],
                artifact_id="art1",
            )

        points = mock_qdrant_client.upsert.call_args.kwargs["points"]
        assert "taxonomy_node_id" not in points[0].payload
