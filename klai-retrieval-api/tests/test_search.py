"""Tests for search service."""

from __future__ import annotations

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


class TestSearch:
    @pytest.fixture(autouse=True)
    def reset_client(self):
        search._client = None
        yield
        search._client = None

    @pytest.mark.asyncio
    async def test_org_search(self):
        """Org scope uses dense cosine search on klai_knowledge (single unnamed vector)."""
        mock_client = AsyncMock()
        mock_client.query_points.return_value = _make_query_response([
            _make_point("c1", "knowledge chunk", 0.8, org_id="org-1"),
        ])

        with patch.object(search, "_get_client", return_value=mock_client):
            req = RetrieveRequest(query="test", org_id="org-1", scope="org")
            results = await search.hybrid_search([0.1, 0.2], req, 10)

        assert len(results) == 1
        assert results[0]["text"] == "knowledge chunk"
        mock_client.query_points.assert_called_once()

    @pytest.mark.asyncio
    async def test_kb_slugs_filter_excludes_other_kb(self):
        """kb_slugs restricts search to the specified KBs; chunks from other KBs are not returned."""
        from qdrant_client.models import MatchAny

        # Mock returns only the chunk that Qdrant would keep after applying the filter.
        # We verify that the Qdrant call includes the kb_slug MatchAny condition.
        mock_client = AsyncMock()
        mock_client.query_points.return_value = _make_query_response([
            _make_point("c1", "intern chunk", 0.8, org_id="org-1", kb_slug="intern"),
        ])

        with patch.object(search, "_get_client", return_value=mock_client):
            req = RetrieveRequest(
                query="test",
                org_id="org-1",
                scope="org",
                kb_slugs=["intern"],
            )
            results = await search.hybrid_search([0.1, 0.2], req, 10)

        # Only the "intern" chunk is returned — Qdrant filters out others server-side.
        assert len(results) == 1
        assert results[0]["text"] == "intern chunk"

        # Verify the filter sent to Qdrant contains the kb_slug MatchAny condition.
        call_args = mock_client.query_points.call_args
        prefetches = call_args.kwargs.get("prefetch") or call_args.args[1]
        # At least one prefetch must carry a filter with the kb_slug condition.
        kb_conditions = [
            cond
            for pf in prefetches
            for cond in (pf.filter.must or [])
            if getattr(cond, "key", None) == "kb_slug"
        ]
        # Each prefetch leg carries the condition, so there are >= 1 occurrences.
        assert len(kb_conditions) >= 1
        assert isinstance(kb_conditions[0].match, MatchAny)
        assert kb_conditions[0].match.any == ["intern"]

    @pytest.mark.asyncio
    async def test_knowledge_search_passes_through_evidence_metadata(self):
        """Search result dicts include ingested_at, assertion_mode from payload (R4)."""
        point = _make_point(
            "c1", "chunk text", 0.8,
            org_id="org-1",
            ingested_at=1711843200,
            assertion_mode="fact",
        )
        mock_client = AsyncMock()
        mock_client.query_points.return_value = _make_query_response([point])

        with patch.object(search, "_get_client", return_value=mock_client):
            req = RetrieveRequest(query="test", org_id="org-1", scope="org")
            results = await search.hybrid_search([0.1, 0.2], req, 10)

        assert results[0]["ingested_at"] == 1711843200
        assert results[0]["assertion_mode"] == "fact"

    @pytest.mark.asyncio
    async def test_knowledge_search_evidence_metadata_defaults_to_none(self):
        """When payload lacks evidence fields, they default to None (R4)."""
        point = _make_point("c1", "chunk text", 0.8, org_id="org-1")
        mock_client = AsyncMock()
        mock_client.query_points.return_value = _make_query_response([point])

        with patch.object(search, "_get_client", return_value=mock_client):
            req = RetrieveRequest(query="test", org_id="org-1", scope="org")
            results = await search.hybrid_search([0.1, 0.2], req, 10)

        assert results[0]["ingested_at"] is None
        assert results[0]["assertion_mode"] is None
