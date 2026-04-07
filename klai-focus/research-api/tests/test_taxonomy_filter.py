"""Tests for _get_taxonomy_filter — taxonomy-aware retrieval (SPEC-KB-027 R1).

We patch the two helper coroutines (_classify_query, _get_coverage_ratio) that
_get_taxonomy_filter calls via asyncio.gather. This avoids HTTP entirely and tests
the orchestration logic cleanly: coverage gating, empty-list handling, error fallback.

Verifies:
- Returns node IDs when coverage >= 30% and classify returns IDs
- Returns None when coverage < 30%
- Returns None when asyncio.gather raises (timeout, network error)
- Returns None when knowledge_ingest_url is empty
- Returns None when node_ids is empty list despite sufficient coverage
- Returns node IDs at exact coverage threshold (30%)
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mock_settings(
    knowledge_ingest_url: str = "http://knowledge-ingest:8000",
    taxonomy_retrieval_min_coverage: float = 0.3,
) -> MagicMock:
    s = MagicMock()
    s.knowledge_ingest_url = knowledge_ingest_url
    s.taxonomy_retrieval_min_coverage = taxonomy_retrieval_min_coverage
    return s


class TestGetTaxonomyFilter:
    @pytest.mark.asyncio
    async def test_returns_node_ids_when_coverage_sufficient(self):
        """Returns [5, 7] when classify returns those IDs and coverage = 60% (>= 30%)."""
        with patch("app.services.retrieval_client.settings", _mock_settings()):
            with patch("app.services.retrieval_client._classify_query", new=AsyncMock(return_value=[5, 7])):
                with patch("app.services.retrieval_client._get_coverage_ratio", new=AsyncMock(return_value=0.6)):
                    from app.services.retrieval_client import _get_taxonomy_filter
                    result = await _get_taxonomy_filter("test query", "my-kb", "org1")

        assert result == [5, 7]

    @pytest.mark.asyncio
    async def test_returns_none_when_coverage_too_low(self):
        """Returns None when coverage = 10% (< 30% threshold)."""
        with patch("app.services.retrieval_client.settings", _mock_settings()):
            with patch("app.services.retrieval_client._classify_query", new=AsyncMock(return_value=[5])):
                with patch("app.services.retrieval_client._get_coverage_ratio", new=AsyncMock(return_value=0.1)):
                    from app.services.retrieval_client import _get_taxonomy_filter
                    result = await _get_taxonomy_filter("test query", "my-kb", "org1")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_timeout(self):
        """Returns None when calls time out — retrieval continues without filter."""
        with patch("app.services.retrieval_client.settings", _mock_settings()):
            with patch("app.services.retrieval_client._classify_query", side_effect=asyncio.TimeoutError):
                with patch("app.services.retrieval_client._get_coverage_ratio", new=AsyncMock(return_value=0.6)):
                    from app.services.retrieval_client import _get_taxonomy_filter
                    result = await _get_taxonomy_filter("test query", "my-kb", "org1")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self):
        """Returns None when classify/coverage raise any exception."""
        with patch("app.services.retrieval_client.settings", _mock_settings()):
            with patch("app.services.retrieval_client._classify_query", side_effect=Exception("connection refused")):
                with patch("app.services.retrieval_client._get_coverage_ratio", new=AsyncMock(return_value=0.6)):
                    from app.services.retrieval_client import _get_taxonomy_filter
                    result = await _get_taxonomy_filter("test query", "my-kb", "org1")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_knowledge_ingest_url_empty(self):
        """Returns None immediately when knowledge_ingest_url is not configured."""
        with patch("app.services.retrieval_client.settings", _mock_settings(knowledge_ingest_url="")):
            from app.services.retrieval_client import _get_taxonomy_filter
            result = await _get_taxonomy_filter("test query", "my-kb", "org1")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_classify_returns_empty(self):
        """Returns None when classify returns empty list despite sufficient coverage."""
        with patch("app.services.retrieval_client.settings", _mock_settings()):
            with patch("app.services.retrieval_client._classify_query", new=AsyncMock(return_value=[])):
                with patch("app.services.retrieval_client._get_coverage_ratio", new=AsyncMock(return_value=0.7)):
                    from app.services.retrieval_client import _get_taxonomy_filter
                    result = await _get_taxonomy_filter("test query", "my-kb", "org1")

        assert result is None

    @pytest.mark.asyncio
    async def test_exact_coverage_threshold_passes(self):
        """Returns node IDs when coverage exactly equals the minimum threshold (30%)."""
        with patch("app.services.retrieval_client.settings", _mock_settings()):
            with patch("app.services.retrieval_client._classify_query", new=AsyncMock(return_value=[3])):
                with patch("app.services.retrieval_client._get_coverage_ratio", new=AsyncMock(return_value=0.3)):
                    from app.services.retrieval_client import _get_taxonomy_filter
                    result = await _get_taxonomy_filter("test query", "my-kb", "org1")

        assert result == [3]
