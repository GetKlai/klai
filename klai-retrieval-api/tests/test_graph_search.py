"""Tests for retrieval_api.services.graph_search and RRF merge."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from retrieval_api.services import graph_search
from retrieval_api.api.retrieve import _rrf_merge


def _make_graph_result(uuid: str, fact: str, score: float = 0.8) -> MagicMock:
    r = MagicMock()
    r.uuid = uuid
    r.fact = fact
    r.score = score
    return r


@pytest.mark.asyncio
async def test_search_disabled():
    """Returns empty list immediately when GRAPHITI_ENABLED=false (AC-8)."""
    with patch("retrieval_api.services.graph_search.settings") as mock_settings:
        mock_settings.graphiti_enabled = False
        result = await graph_search.search("query", "org-1")
    assert result == []


@pytest.mark.asyncio
async def test_search_success():
    """Returns converted chunk-compatible dicts on success."""
    mock_graphiti = AsyncMock()
    mock_graphiti.search = AsyncMock(
        return_value=[_make_graph_result("e1", "Mark decided X", 0.9)]
    )

    with (
        patch("retrieval_api.services.graph_search.settings") as mock_settings,
        patch("retrieval_api.services.graph_search._get_graphiti", return_value=mock_graphiti),
    ):
        mock_settings.graphiti_enabled = True
        mock_settings.graph_search_timeout = 5.0
        result = await graph_search.search("query", "org-1", top_k=10)

    assert len(result) == 1
    assert result[0]["chunk_id"] == "graph:e1"
    assert result[0]["text"] == "Mark decided X"
    assert result[0]["score"] == 0.9
    assert result[0]["content_type"] == "graph_edge"
    mock_graphiti.search.assert_called_once()
    assert mock_graphiti.search.call_args.kwargs.get("group_ids") == ["org-1"]


@pytest.mark.asyncio
async def test_search_timeout():
    """Returns empty list on timeout — graceful degradation (AC-7)."""
    mock_graphiti = AsyncMock()
    mock_graphiti.search = AsyncMock(side_effect=asyncio.TimeoutError)

    with (
        patch("retrieval_api.services.graph_search.settings") as mock_settings,
        patch("retrieval_api.services.graph_search._get_graphiti", return_value=mock_graphiti),
    ):
        mock_settings.graphiti_enabled = True
        mock_settings.graph_search_timeout = 5.0
        result = await graph_search.search("query", "org-1")

    assert result == []


@pytest.mark.asyncio
async def test_search_exception():
    """Returns empty list on generic exception — graceful degradation (AC-7)."""
    mock_graphiti = AsyncMock()
    mock_graphiti.search = AsyncMock(side_effect=RuntimeError("connection refused"))

    with (
        patch("retrieval_api.services.graph_search.settings") as mock_settings,
        patch("retrieval_api.services.graph_search._get_graphiti", return_value=mock_graphiti),
    ):
        mock_settings.graphiti_enabled = True
        mock_settings.graph_search_timeout = 5.0
        result = await graph_search.search("query", "org-1")

    assert result == []


def test_rrf_merge_combines_results():
    """RRF merge produces combined result set with updated scores (AC-5)."""
    qdrant = [
        {"chunk_id": "q1", "text": "a", "score": 0.9, "artifact_id": None,
         "content_type": None, "context_prefix": None, "scope": "org",
         "valid_at": None, "invalid_at": None},
        {"chunk_id": "q2", "text": "b", "score": 0.8, "artifact_id": None,
         "content_type": None, "context_prefix": None, "scope": "org",
         "valid_at": None, "invalid_at": None},
    ]
    graph = [
        {"chunk_id": "graph:g1", "text": "c", "score": 0.7, "artifact_id": None,
         "content_type": "graph_edge", "context_prefix": None, "scope": "org",
         "valid_at": None, "invalid_at": None},
    ]
    merged = _rrf_merge(qdrant, graph)

    assert len(merged) == 3
    chunk_ids = [r["chunk_id"] for r in merged]
    assert "q1" in chunk_ids
    assert "q2" in chunk_ids
    assert "graph:g1" in chunk_ids
    # q1 should rank highest (top of both Qdrant rank)
    assert merged[0]["chunk_id"] == "q1"


def test_rrf_merge_empty_graph():
    """RRF with empty graph results preserves Qdrant order (AC-5)."""
    qdrant = [
        {"chunk_id": "q1", "text": "a", "score": 0.9, "artifact_id": None,
         "content_type": None, "context_prefix": None, "scope": "org",
         "valid_at": None, "invalid_at": None},
        {"chunk_id": "q2", "text": "b", "score": 0.8, "artifact_id": None,
         "content_type": None, "context_prefix": None, "scope": "org",
         "valid_at": None, "invalid_at": None},
    ]
    merged = _rrf_merge(qdrant, [])

    assert len(merged) == 2
    assert merged[0]["chunk_id"] == "q1"
    assert merged[1]["chunk_id"] == "q2"


def test_rrf_merge_deduplication():
    """Chunk appearing in both lists is deduplicated in output."""
    shared = {"chunk_id": "shared", "text": "x", "score": 0.5, "artifact_id": None,
              "content_type": None, "context_prefix": None, "scope": "org",
              "valid_at": None, "invalid_at": None}
    qdrant = [dict(shared)]
    graph = [dict(shared)]
    merged = _rrf_merge(qdrant, graph)

    chunk_ids = [r["chunk_id"] for r in merged]
    assert chunk_ids.count("shared") == 1
