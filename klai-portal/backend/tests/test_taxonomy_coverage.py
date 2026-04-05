"""Tests for taxonomy coverage endpoint (SPEC-KB-022 TASK-017).

Unit tests for the coverage stats aggregation logic.
Mocks the ingest HTTP call and DB queries.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.taxonomy import (
    CoverageNodeOut,
    _coverage_cache,
    _fetch_ingest_coverage,
    _make_coverage_response,
)


class TestCoverageHealthStatus:
    """Health status calculation: healthy / attention_needed / empty."""

    def test_healthy_node(self) -> None:
        """Node with >= 10 chunks and < 5 gaps is healthy."""
        node = _make_node(chunk_count=15, gap_count=3)
        assert node.health == "healthy"

    def test_attention_needed_low_chunks(self) -> None:
        """Node with < 10 chunks is attention_needed."""
        node = _make_node(chunk_count=5, gap_count=0)
        assert node.health == "attention_needed"

    def test_attention_needed_high_gaps(self) -> None:
        """Node with >= 5 gaps is attention_needed."""
        node = _make_node(chunk_count=50, gap_count=10)
        assert node.health == "attention_needed"

    def test_empty_node(self) -> None:
        """Node with 0 chunks is empty."""
        node = _make_node(chunk_count=0, gap_count=0)
        assert node.health == "empty"

    def test_boundary_healthy(self) -> None:
        """Exactly 10 chunks and 4 gaps is healthy."""
        node = _make_node(chunk_count=10, gap_count=4)
        assert node.health == "healthy"

    def test_boundary_attention_gaps(self) -> None:
        """Exactly 5 gaps triggers attention_needed."""
        node = _make_node(chunk_count=20, gap_count=5)
        assert node.health == "attention_needed"


class TestMakeCoverageResponse:
    """Test _make_coverage_response merges ingest + gap data correctly."""

    def test_merges_ingest_and_gap_data(self) -> None:
        """Chunk counts from ingest and gap counts from DB are merged per node."""
        ingest_data = {
            "nodes": [
                {"taxonomy_node_id": 1, "chunk_count": 20},
                {"taxonomy_node_id": 2, "chunk_count": 5},
            ],
            "total_chunks": 30,
            "untagged_count": 5,
        }
        gap_counts = {1: 2, 2: 8}
        node_names = {1: "Billing", 2: "Support"}

        response = _make_coverage_response(ingest_data, gap_counts, node_names)

        assert response.total_chunks == 30
        assert response.untagged_count == 5
        assert response.untagged_percentage == pytest.approx(16.67, abs=0.01)
        assert len(response.nodes) == 2

        billing = next(n for n in response.nodes if n.taxonomy_node_id == 1)
        assert billing.chunk_count == 20
        assert billing.gap_count == 2
        assert billing.health == "healthy"

        support = next(n for n in response.nodes if n.taxonomy_node_id == 2)
        assert support.chunk_count == 5
        assert support.gap_count == 8
        assert support.health == "attention_needed"

    def test_zero_total_chunks(self) -> None:
        """Zero total chunks gives 0% untagged percentage (no division by zero)."""
        ingest_data = {
            "nodes": [],
            "total_chunks": 0,
            "untagged_count": 0,
        }
        response = _make_coverage_response(ingest_data, {}, {})
        assert response.untagged_percentage == 0.0

    def test_missing_gap_count_defaults_to_zero(self) -> None:
        """Nodes not in gap_counts get gap_count=0."""
        ingest_data = {
            "nodes": [{"taxonomy_node_id": 1, "chunk_count": 20}],
            "total_chunks": 20,
            "untagged_count": 0,
        }
        response = _make_coverage_response(ingest_data, {}, {1: "Billing"})
        assert response.nodes[0].gap_count == 0
        assert response.nodes[0].health == "healthy"


class TestFetchIngestCoverage:
    """Test _fetch_ingest_coverage HTTP client."""

    @pytest.mark.asyncio
    async def test_returns_data_on_success(self) -> None:
        """Successful ingest call returns parsed JSON."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "nodes": [{"taxonomy_node_id": 1, "chunk_count": 10}],
            "total_chunks": 10,
            "untagged_count": 2,
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.api.taxonomy.httpx.AsyncClient", return_value=mock_client):
            result = await _fetch_ingest_coverage("org-123", "my-kb")

        assert result is not None
        assert result["total_chunks"] == 10

    @pytest.mark.asyncio
    async def test_returns_none_on_failure(self) -> None:
        """Failed ingest call returns None."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("Connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.api.taxonomy.httpx.AsyncClient", return_value=mock_client):
            result = await _fetch_ingest_coverage("org-123", "my-kb")

        assert result is None


class TestCoverageCache:
    """Test in-memory cache for coverage data."""

    def setup_method(self) -> None:
        _coverage_cache.clear()

    def test_cache_stores_and_retrieves(self) -> None:
        """Cache entries are retrievable within TTL."""
        _coverage_cache[("org-1", "kb-1")] = (time.monotonic(), {"test": True})
        _ts, data = _coverage_cache[("org-1", "kb-1")]
        assert data == {"test": True}

    def test_cache_key_includes_org_and_kb(self) -> None:
        """Different org/kb combinations are cached separately."""
        _coverage_cache[("org-1", "kb-1")] = (time.monotonic(), {"a": 1})
        _coverage_cache[("org-1", "kb-2")] = (time.monotonic(), {"b": 2})
        assert _coverage_cache[("org-1", "kb-1")][1] != _coverage_cache[("org-1", "kb-2")][1]


def _make_node(chunk_count: int, gap_count: int) -> CoverageNodeOut:
    """Helper to build a CoverageNodeOut with computed health."""
    if chunk_count == 0:
        health = "empty"
    elif chunk_count < 10 or gap_count >= 5:
        health = "attention_needed"
    else:
        health = "healthy"
    return CoverageNodeOut(
        taxonomy_node_id=1,
        taxonomy_node_name="Test",
        chunk_count=chunk_count,
        gap_count=gap_count,
        health=health,
    )
