"""Tests for link expansion fields in search results (SPEC-CRAWLER-003 TASK-006)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from retrieval_api.models import RetrieveRequest
from retrieval_api.services import search


def _make_point(id_: str, text: str, score: float, **extra_payload):
    """Create a mock Qdrant ScoredPoint."""
    payload = {"text": text, **extra_payload}
    return SimpleNamespace(id=id_, score=score, payload=payload)


def _make_query_response(points: list):
    """Wrap points in a QueryResponse-like object (has .points attribute)."""
    return SimpleNamespace(points=points)


class TestLinkFieldsInSearchResults:
    """Scenario 6.1 & 6.2: _search_knowledge() includes link fields."""

    @pytest.fixture(autouse=True)
    def reset_client(self):
        search._client = None
        yield
        search._client = None

    @pytest.mark.asyncio
    async def test_search_results_include_link_fields(self):
        """6.1: source_url, links_to, incoming_link_count appear in results."""
        point = _make_point(
            "c1",
            "some text",
            0.8,
            org_id="org-1",
            source_url="https://docs.example.com/a",
            links_to=["/b", "/c"],
            incoming_link_count=5,
        )
        mock_client = AsyncMock()
        mock_client.query_points.return_value = _make_query_response([point])

        with patch.object(search, "_get_client", return_value=mock_client):
            req = RetrieveRequest(query="test", org_id="org-1", scope="org")
            results = await search.hybrid_search([0.1, 0.2], req, 10)

        assert results[0]["source_url"] == "https://docs.example.com/a"
        assert results[0]["links_to"] == ["/b", "/c"]
        assert results[0]["incoming_link_count"] == 5

    @pytest.mark.asyncio
    async def test_link_fields_default_values(self):
        """6.2: Missing link payload fields get correct defaults."""
        point = _make_point("c1", "some text", 0.8, org_id="org-1")
        mock_client = AsyncMock()
        mock_client.query_points.return_value = _make_query_response([point])

        with patch.object(search, "_get_client", return_value=mock_client):
            req = RetrieveRequest(query="test", org_id="org-1", scope="org")
            results = await search.hybrid_search([0.1, 0.2], req, 10)

        assert results[0].get("source_url") is None
        assert results[0]["links_to"] == []
        assert results[0]["incoming_link_count"] == 0


class TestFetchChunksByUrls:
    """Scenario 6.3-6.5: fetch_chunks_by_urls function."""

    @pytest.fixture(autouse=True)
    def reset_client(self):
        search._client = None
        yield
        search._client = None

    @pytest.mark.asyncio
    async def test_returns_chunks_with_score_zero(self):
        """6.3: Fetched chunks have score=0.0 and include link fields."""
        record = SimpleNamespace(
            id="r1",
            payload={
                "text": "linked content",
                "org_id": "org-1",
                "source_url": "https://docs.example.com/b",
                "links_to": ["/d"],
                "incoming_link_count": 3,
                "artifact_id": "art-1",
                "content_type": "web",
                "scope": "org",
            },
        )
        mock_client = AsyncMock()
        mock_client.scroll.return_value = ([record], None)

        with patch.object(search, "_get_client", return_value=mock_client):
            req = RetrieveRequest(query="test", org_id="org-1", scope="org")
            results = await search.fetch_chunks_by_urls(
                urls=["https://docs.example.com/b"],
                request=req,
                limit=10,
            )

        assert len(results) == 1
        assert results[0]["score"] == 0.0
        assert results[0]["text"] == "linked content"
        assert results[0]["source_url"] == "https://docs.example.com/b"
        assert results[0]["links_to"] == ["/d"]
        assert results[0]["incoming_link_count"] == 3

        # Verify scroll was called with the right collection and filter
        mock_client.scroll.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_urls_returns_empty_without_qdrant_call(self):
        """6.4: Empty URL list returns [] immediately."""
        mock_client = AsyncMock()

        with patch.object(search, "_get_client", return_value=mock_client):
            req = RetrieveRequest(query="test", org_id="org-1", scope="org")
            results = await search.fetch_chunks_by_urls(
                urls=[],
                request=req,
                limit=10,
            )

        assert results == []
        mock_client.scroll.assert_not_called()

    @pytest.mark.asyncio
    async def test_timeout_returns_empty_with_warning(self):
        """6.5: Timeout on scroll returns [] and logs a warning."""
        mock_client = AsyncMock()
        mock_client.scroll.side_effect = asyncio.TimeoutError()

        with patch.object(search, "_get_client", return_value=mock_client), \
             patch.object(search, "logger") as mock_logger:
            req = RetrieveRequest(query="test", org_id="org-1", scope="org")
            results = await search.fetch_chunks_by_urls(
                urls=["https://docs.example.com/x"],
                request=req,
                limit=10,
            )

        assert results == []
        mock_logger.warning.assert_called_once()
        assert mock_logger.warning.call_args[0][0] == "link_expand_failed"

    @pytest.mark.asyncio
    async def test_generic_exception_returns_empty(self):
        """Generic exception on scroll returns [] and logs a warning."""
        mock_client = AsyncMock()
        mock_client.scroll.side_effect = RuntimeError("connection lost")

        with patch.object(search, "_get_client", return_value=mock_client), \
             patch.object(search, "logger") as mock_logger:
            req = RetrieveRequest(query="test", org_id="org-1", scope="org")
            results = await search.fetch_chunks_by_urls(
                urls=["https://docs.example.com/x"],
                request=req,
                limit=10,
            )

        assert results == []
        mock_logger.warning.assert_called_once()
