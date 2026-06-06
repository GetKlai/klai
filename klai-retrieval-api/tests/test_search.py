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
        mock_client.query_points.return_value = _make_query_response(
            [
                _make_point("c1", "knowledge chunk", 0.8, org_id="org-1"),
            ]
        )

        with patch.object(search, "_get_client", return_value=mock_client):
            req = RetrieveRequest(query="test", org_id="org-1", scope="org")
            results = await search.hybrid_search([0.1, 0.2], req, 10)

        assert len(results) == 1
        assert results[0]["text"] == "knowledge chunk"
        mock_client.query_points.assert_called_once()

    @pytest.mark.asyncio
    async def test_kb_slugs_filter_excludes_other_kb(self):
        """kb_slugs restricts search to the specified KBs."""
        from qdrant_client.models import MatchAny

        # Mock returns only the chunk that Qdrant would keep after applying the filter.
        # We verify that the Qdrant call includes the kb_slug MatchAny condition.
        mock_client = AsyncMock()
        mock_client.query_points.return_value = _make_query_response(
            [
                _make_point("c1", "intern chunk", 0.8, org_id="org-1", kb_slug="intern"),
            ]
        )

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
            "c1",
            "chunk text",
            0.8,
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

    @pytest.mark.asyncio
    async def test_knowledge_search_passes_heading_path(self):
        """Heading hierarchy is returned as metadata for structured evidence rendering."""
        point = _make_point(
            "c1",
            "Admin > Mensen\n\nNodig iemand uit.",
            0.8,
            heading_path="Admin > Mensen",
        )
        mock_client = AsyncMock()
        mock_client.query_points.return_value = _make_query_response([point])

        with patch.object(search, "_get_client", return_value=mock_client):
            req = RetrieveRequest(query="test", org_id="org-1", scope="org")
            results = await search.hybrid_search([0.1, 0.2], req, 10)

        assert results[0]["heading_path"] == "Admin > Mensen"

    @pytest.mark.asyncio
    async def test_raw_query_adds_literal_term_rrf_leg(self):
        """A differing raw_query adds dense + sparse RRF legs so literal-term
        matches survive an over-eager coreference/query rewrite.

        Contract: candidate retrieval must not depend solely on the rewritten
        query. Without the raw leg, an exact term the user typed (e.g. a product
        name like "Salesforce") that the rewrite paraphrased away never enters
        the candidate pool, and the reranker cannot recover it (bounded recall).
        """
        from qdrant_client.models import SparseVector

        mock_client = AsyncMock()
        mock_client.query_points.return_value = _make_query_response(
            [
                _make_point("c1", "Salesforce CRM-configuratie", 1.0, org_id="org-1"),
            ]
        )

        with patch.object(search, "_get_client", return_value=mock_client):
            req = RetrieveRequest(query="rewritten", org_id="org-1", scope="org")
            await search.hybrid_search(
                [0.1, 0.2],
                req,
                10,
                SparseVector(indices=[1], values=[0.9]),
                raw_query_vector=[0.3, 0.4],
                raw_sparse_vector=SparseVector(indices=[2], values=[0.8]),
            )

        prefetches = mock_client.query_points.call_args.kwargs["prefetch"]
        using = [pf.using for pf in prefetches]
        # resolved query: chunk + questions + sparse; raw query: chunk + sparse.
        assert using == [
            "vector_chunk",
            "vector_questions",
            "vector_sparse",
            "vector_chunk",
            "vector_sparse",
        ]

    @pytest.mark.asyncio
    async def test_raw_query_dense_leg_without_sparse(self):
        """raw_query adds a dense leg even when the raw sparse vector is absent."""
        from qdrant_client.models import SparseVector

        mock_client = AsyncMock()
        mock_client.query_points.return_value = _make_query_response(
            [
                _make_point("c1", "chunk", 0.8, org_id="org-1"),
            ]
        )

        with patch.object(search, "_get_client", return_value=mock_client):
            req = RetrieveRequest(query="rewritten", org_id="org-1", scope="org")
            await search.hybrid_search(
                [0.1, 0.2],
                req,
                10,
                SparseVector(indices=[1], values=[0.9]),
                raw_query_vector=[0.3, 0.4],
            )

        prefetches = mock_client.query_points.call_args.kwargs["prefetch"]
        using = [pf.using for pf in prefetches]
        assert using == [
            "vector_chunk",
            "vector_questions",
            "vector_sparse",
            "vector_chunk",
        ]

    @pytest.mark.asyncio
    async def test_no_raw_query_keeps_baseline_leg_shape(self):
        """Without a raw_query vector the prefetch shape is unchanged (no extra legs)."""
        from qdrant_client.models import SparseVector

        mock_client = AsyncMock()
        mock_client.query_points.return_value = _make_query_response(
            [
                _make_point("c1", "chunk", 0.8, org_id="org-1"),
            ]
        )

        with patch.object(search, "_get_client", return_value=mock_client):
            req = RetrieveRequest(query="q", org_id="org-1", scope="org")
            await search.hybrid_search([0.1, 0.2], req, 10, SparseVector(indices=[1], values=[0.9]))

        prefetches = mock_client.query_points.call_args.kwargs["prefetch"]
        using = [pf.using for pf in prefetches]
        assert using == ["vector_chunk", "vector_questions", "vector_sparse"]
