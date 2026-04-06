"""
Tests for auto-categorise endpoint (SPEC-KB-024 R4).

Covers: tagging matching chunks, skipping below threshold,
no LLM calls during operation, and auth requirement.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def _patch_qdrant():
    """Provide a mock Qdrant client for auto-categorise tests."""
    mock_client = MagicMock()
    mock_client.scroll = AsyncMock(return_value=([], None))
    mock_client.set_payload = AsyncMock()

    with patch(
        "knowledge_ingest.routes.taxonomy.AsyncQdrantClient",
        return_value=mock_client,
    ):
        yield mock_client


def _make_point(point_id, artifact_id, vector, taxonomy_node_ids=None):
    """Create a mock Qdrant point with vector and payload."""
    p = MagicMock()
    p.id = point_id
    p.payload = {
        "artifact_id": artifact_id,
        "chunk_index": 0,
        "taxonomy_node_ids": taxonomy_node_ids or [],
    }
    p.vector = {"vector_chunk": vector}
    return p


def _make_point_no_vector(point_id, artifact_id, taxonomy_node_ids=None):
    """Create a mock Qdrant point without vector (for pass 2)."""
    p = MagicMock()
    p.id = point_id
    p.payload = {
        "artifact_id": artifact_id,
        "chunk_index": 0,
        "taxonomy_node_ids": taxonomy_node_ids or [],
    }
    p.vector = None
    return p


@pytest.mark.asyncio
async def test_auto_categorise_tags_matching_chunks(_patch_qdrant):
    """Chunks above threshold should get node_id added to taxonomy_node_ids."""
    mock_client = _patch_qdrant

    centroid = [1.0, 0.0, 0.0]
    # Pass 1: identify matching artifacts (with vectors)
    close_point = _make_point("p1", "art-1", [0.99, 0.01, 0.0])
    far_point = _make_point("p2", "art-2", [0.0, 1.0, 0.0])
    # Pass 2: tag all chunks of matching artifacts (no vectors needed)
    chunk_1 = _make_point_no_vector("p1", "art-1")
    chunk_2 = _make_point_no_vector("p2", "art-2")

    mock_client.scroll = AsyncMock(
        side_effect=[
            # Pass 1: similarity check
            ([close_point, far_point], None),
            # Pass 2: tag all chunks of matched docs
            ([chunk_1, chunk_2], None),
        ]
    )

    from knowledge_ingest.routes.taxonomy import _auto_categorise_impl

    result = await _auto_categorise_impl(
        org_id="org-1",
        kb_slug="test-kb",
        node_id=42,
        cluster_centroid=centroid,
        threshold=0.82,
    )

    assert result >= 1  # at least art-1 should be matched
    # set_payload should have been called for matching chunks
    assert mock_client.set_payload.call_count >= 1


@pytest.mark.asyncio
async def test_auto_categorise_skips_below_threshold(_patch_qdrant):
    """Chunks below 0.82 similarity should not be updated."""
    mock_client = _patch_qdrant

    centroid = [1.0, 0.0, 0.0]
    # All points are far from centroid
    far_point = _make_point("p1", "art-1", [0.0, 1.0, 0.0])

    mock_client.scroll = AsyncMock(
        side_effect=[
            # Pass 1: no matches found
            ([far_point], None),
            # Pass 2 never runs (early return when no matched artifacts)
        ]
    )

    from knowledge_ingest.routes.taxonomy import _auto_categorise_impl

    result = await _auto_categorise_impl(
        org_id="org-1",
        kb_slug="test-kb",
        node_id=42,
        cluster_centroid=centroid,
        threshold=0.82,
    )

    assert result == 0
    mock_client.set_payload.assert_not_called()


@pytest.mark.asyncio
async def test_auto_categorise_no_llm_calls(_patch_qdrant):
    """Auto-categorise must NOT make any LLM calls (AC7)."""
    mock_client = _patch_qdrant

    centroid = [1.0, 0.0, 0.0]
    close_point = _make_point("p1", "art-1", [0.99, 0.01, 0.0])
    chunk_1 = _make_point_no_vector("p1", "art-1")

    mock_client.scroll = AsyncMock(
        side_effect=[
            # Pass 1
            ([close_point], None),
            # Pass 2
            ([chunk_1], None),
        ]
    )

    with patch("httpx.AsyncClient") as mock_httpx:
        mock_httpx_instance = AsyncMock()
        mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_httpx_instance)
        mock_httpx.return_value.__aexit__ = AsyncMock()

        from knowledge_ingest.routes.taxonomy import _auto_categorise_impl

        await _auto_categorise_impl(
            org_id="org-1",
            kb_slug="test-kb",
            node_id=42,
            cluster_centroid=centroid,
            threshold=0.82,
        )

        # No HTTP calls to LiteLLM should have been made
        mock_httpx_instance.post.assert_not_called()


def test_auto_categorise_requires_auth(client):
    """POST without X-Internal-Token should return 401."""
    with patch("knowledge_ingest.routes.taxonomy.settings") as mock_settings:
        mock_settings.portal_internal_token = "secret-token"
        mock_settings.qdrant_url = "http://localhost:6333"
        mock_settings.qdrant_api_key = ""
        mock_settings.taxonomy_auto_categorise_threshold = 0.82
        resp = client.post(
            "/ingest/v1/taxonomy/auto-categorise",
            json={
                "org_id": "org-1",
                "kb_slug": "test-kb",
                "node_id": 42,
                "cluster_centroid": [1.0, 0.0, 0.0],
            },
        )
        assert resp.status_code == 401
