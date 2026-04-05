"""Tests for taxonomy_node_ids and tags in qdrant_store payload handling."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest.qdrant_store import upsert_chunks


@pytest.fixture
def mock_qdrant_client():
    client = AsyncMock()
    client.delete = AsyncMock()
    client.upsert = AsyncMock()
    return client


class TestUpsertChunksTaxonomyMultiLabel:
    @pytest.mark.asyncio
    async def test_taxonomy_node_ids_stored_when_has_taxonomy_true(self, mock_qdrant_client):
        """When has_taxonomy=True and node_ids are set, payload includes taxonomy_node_ids."""
        with patch("knowledge_ingest.qdrant_store.get_client", return_value=mock_qdrant_client):
            await upsert_chunks(
                org_id="org1",
                kb_slug="kb1",
                path="doc.md",
                chunks=["chunk text"],
                vectors=[[0.1, 0.2]],
                artifact_id="art1",
                taxonomy_node_ids=[5, 7],
                has_taxonomy=True,
            )

        mock_qdrant_client.upsert.assert_called_once()
        points = mock_qdrant_client.upsert.call_args.kwargs["points"]
        assert len(points) == 1
        assert points[0].payload["taxonomy_node_ids"] == [5, 7]

    @pytest.mark.asyncio
    async def test_taxonomy_node_ids_empty_when_no_match(self, mock_qdrant_client):
        """When has_taxonomy=True but no node matched, payload has taxonomy_node_ids=[]."""
        with patch("knowledge_ingest.qdrant_store.get_client", return_value=mock_qdrant_client):
            await upsert_chunks(
                org_id="org1",
                kb_slug="kb1",
                path="doc.md",
                chunks=["chunk text"],
                vectors=[[0.1, 0.2]],
                artifact_id="art1",
                taxonomy_node_ids=[],
                has_taxonomy=True,
            )

        points = mock_qdrant_client.upsert.call_args.kwargs["points"]
        assert "taxonomy_node_ids" in points[0].payload
        assert points[0].payload["taxonomy_node_ids"] == []

    @pytest.mark.asyncio
    async def test_taxonomy_node_ids_absent_when_no_taxonomy(self, mock_qdrant_client):
        """When has_taxonomy=False, taxonomy_node_ids is NOT stored in payload at all."""
        with patch("knowledge_ingest.qdrant_store.get_client", return_value=mock_qdrant_client):
            await upsert_chunks(
                org_id="org1",
                kb_slug="kb1",
                path="doc.md",
                chunks=["chunk text"],
                vectors=[[0.1, 0.2]],
                artifact_id="art1",
                taxonomy_node_ids=None,
                has_taxonomy=False,
            )

        points = mock_qdrant_client.upsert.call_args.kwargs["points"]
        assert "taxonomy_node_ids" not in points[0].payload

    @pytest.mark.asyncio
    async def test_backward_compatible_default_has_no_taxonomy(self, mock_qdrant_client):
        """Calling without taxonomy args (old callers) must not add taxonomy_node_ids."""
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
        assert "taxonomy_node_ids" not in points[0].payload
        assert "tags" not in points[0].payload

    @pytest.mark.asyncio
    async def test_tags_stored_when_provided(self, mock_qdrant_client):
        """Tags are stored in payload when provided."""
        with patch("knowledge_ingest.qdrant_store.get_client", return_value=mock_qdrant_client):
            await upsert_chunks(
                org_id="org1",
                kb_slug="kb1",
                path="doc.md",
                chunks=["chunk text"],
                vectors=[[0.1, 0.2]],
                artifact_id="art1",
                tags=["sso", "okta"],
            )

        points = mock_qdrant_client.upsert.call_args.kwargs["points"]
        assert points[0].payload["tags"] == ["sso", "okta"]

    @pytest.mark.asyncio
    async def test_tags_absent_when_empty(self, mock_qdrant_client):
        """Empty tags list does not store tags field."""
        with patch("knowledge_ingest.qdrant_store.get_client", return_value=mock_qdrant_client):
            await upsert_chunks(
                org_id="org1",
                kb_slug="kb1",
                path="doc.md",
                chunks=["chunk text"],
                vectors=[[0.1, 0.2]],
                artifact_id="art1",
                tags=[],
            )

        points = mock_qdrant_client.upsert.call_args.kwargs["points"]
        assert "tags" not in points[0].payload

    @pytest.mark.asyncio
    async def test_taxonomy_node_ids_none_defaults_to_empty_list(self, mock_qdrant_client):
        """When has_taxonomy=True but taxonomy_node_ids=None, store empty list."""
        with patch("knowledge_ingest.qdrant_store.get_client", return_value=mock_qdrant_client):
            await upsert_chunks(
                org_id="org1",
                kb_slug="kb1",
                path="doc.md",
                chunks=["chunk text"],
                vectors=[[0.1, 0.2]],
                artifact_id="art1",
                taxonomy_node_ids=None,
                has_taxonomy=True,
            )

        points = mock_qdrant_client.upsert.call_args.kwargs["points"]
        assert points[0].payload["taxonomy_node_ids"] == []
