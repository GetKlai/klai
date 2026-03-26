"""Tests for search service."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from retrieval_api.models import RetrieveRequest
from retrieval_api.services import search


def _make_search_result(id_: str, text: str, score: float, **extra_payload):
    """Create a mock Qdrant search result."""
    payload = {"text": text, **extra_payload}
    return SimpleNamespace(id=id_, score=score, payload=payload)


class TestSearch:
    @pytest.fixture(autouse=True)
    def reset_client(self):
        search._client = None
        yield
        search._client = None

    @pytest.mark.asyncio
    async def test_notebook_search(self):
        """Notebook scope uses simple dense search on klai_focus."""
        mock_client = AsyncMock()
        mock_client.search.return_value = [
            _make_search_result("c1", "focus content", 0.9),
        ]
        # klai_focus uses "content" not "text"
        mock_client.search.return_value[0].payload = {
            "content": "focus content",
            "tenant_id": "org-1",
            "notebook_id": "nb-1",
        }

        with patch.object(search, "_get_client", return_value=mock_client):
            req = RetrieveRequest(
                query="test",
                org_id="org-1",
                scope="notebook",
                notebook_id="nb-1",
            )
            results = await search.hybrid_search([0.1, 0.2], req, 10)

        assert len(results) == 1
        assert results[0]["text"] == "focus content"
        assert results[0]["scope"] == "notebook"

    @pytest.mark.asyncio
    async def test_org_search(self):
        """Org scope uses dense cosine search on klai_knowledge (single unnamed vector)."""
        mock_client = AsyncMock()
        mock_client.search.return_value = [
            _make_search_result("c1", "knowledge chunk", 0.8, org_id="org-1"),
        ]

        with patch.object(search, "_get_client", return_value=mock_client):
            req = RetrieveRequest(query="test", org_id="org-1", scope="org")
            results = await search.hybrid_search([0.1, 0.2], req, 10)

        assert len(results) == 1
        assert results[0]["text"] == "knowledge chunk"
        mock_client.search.assert_called_once()

    @pytest.mark.asyncio
    async def test_broad_search_merges(self):
        """Broad scope merges results from both collections."""
        mock_client = AsyncMock()
        # Both notebook and knowledge use client.search
        knowledge_result = _make_search_result("k1", "knowledge", 0.8)
        focus_result = SimpleNamespace(
            id="f1",
            score=0.9,
            payload={"content": "focus", "tenant_id": "org-1"},
        )
        # search is called twice: once for knowledge, once for notebook
        mock_client.search.side_effect = [
            [knowledge_result],
            [focus_result],
        ]

        with patch.object(search, "_get_client", return_value=mock_client):
            req = RetrieveRequest(
                query="test",
                org_id="org-1",
                scope="broad",
                notebook_id="nb-1",
            )
            results = await search.hybrid_search([0.1, 0.2], req, 10)

        assert len(results) == 2
        # Should be sorted by score desc
        assert results[0]["score"] >= results[1]["score"]
