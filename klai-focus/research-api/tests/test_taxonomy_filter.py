"""Tests for _get_taxonomy_filter — taxonomy-aware retrieval (SPEC-KB-027 R1).

We patch asyncio.gather directly to control what the parallel calls return,
bypassing HTTP entirely. This tests the logic of _get_taxonomy_filter (coverage
check, empty-list handling) without requiring a live HTTP stack.

Verifies:
- Returns node IDs when coverage >= 30% and classify returns IDs
- Returns None when coverage < 30%
- Returns None when asyncio.gather raises (timeout, network error)
- Returns None when knowledge_ingest_url is empty
- Returns None when node_ids is empty list despite sufficient coverage
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
            with patch(
                "app.services.retrieval_client.asyncio.gather",
                new=AsyncMock(return_value=([5, 7], 0.6)),
            ):
                from app.services.retrieval_client import _get_taxonomy_filter
                result = await _get_taxonomy_filter("test query", "my-kb", "org1")

        assert result == [5, 7]

    @pytest.mark.asyncio
    async def test_returns_none_when_coverage_too_low(self):
        """Returns None when coverage = 10% (< 30% threshold)."""
        with patch("app.services.retrieval_client.settings", _mock_settings()):
            with patch(
                "app.services.retrieval_client.asyncio.gather",
                new=AsyncMock(return_value=([5], 0.1)),
            ):
                from app.services.retrieval_client import _get_taxonomy_filter
                result = await _get_taxonomy_filter("test query", "my-kb", "org1")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_timeout(self):
        """Returns None when calls time out — retrieval continues without filter."""
        with patch("app.services.retrieval_client.settings", _mock_settings()):
            with patch(
                "app.services.retrieval_client.asyncio.gather",
                side_effect=asyncio.TimeoutError,
            ):
                from app.services.retrieval_client import _get_taxonomy_filter
                result = await _get_taxonomy_filter("test query", "my-kb", "org1")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self):
        """Returns None when classify/coverage raise any exception."""
        with patch("app.services.retrieval_client.settings", _mock_settings()):
            with patch(
                "app.services.retrieval_client.asyncio.gather",
                side_effect=Exception("connection refused"),
            ):
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
            with patch(
                "app.services.retrieval_client.asyncio.gather",
                new=AsyncMock(return_value=([], 0.7)),
            ):
                from app.services.retrieval_client import _get_taxonomy_filter
                result = await _get_taxonomy_filter("test query", "my-kb", "org1")

        assert result is None

    @pytest.mark.asyncio
    async def test_exact_coverage_threshold_passes(self):
        """Returns node IDs when coverage exactly equals the minimum threshold (30%)."""
        with patch("app.services.retrieval_client.settings", _mock_settings()):
            with patch(
                "app.services.retrieval_client.asyncio.gather",
                new=AsyncMock(return_value=([3], 0.3)),
            ):
                from app.services.retrieval_client import _get_taxonomy_filter
                result = await _get_taxonomy_filter("test query", "my-kb", "org1")

        assert result == [3]
