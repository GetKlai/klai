"""Tests for search service."""

from __future__ import annotations

import time
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
        """kb_slugs restricts search to the specified KBs."""
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

    @pytest.mark.asyncio
    async def test_qdrant_temporal_filter_respects_valid_until_invalid_at_and_valid_from(self):
        """The live Qdrant filter excludes stale ingest-contract temporal payloads."""
        from qdrant_client import AsyncQdrantClient
        from qdrant_client.models import Distance, PointStruct, VectorParams

        client = AsyncQdrantClient(location=":memory:")
        await client.create_collection(
            "klai_knowledge",
            vectors_config={
                "vector_chunk": VectorParams(size=2, distance=Distance.COSINE),
                "vector_questions": VectorParams(size=2, distance=Distance.COSINE),
            },
        )

        now = int(time.time())
        vector = {"vector_chunk": [1.0, 0.0], "vector_questions": [1.0, 0.0]}
        await client.upsert(
            "klai_knowledge",
            points=[
                PointStruct(
                    id=1,
                    vector=vector,
                    payload={
                        "org_id": "org-1",
                        "kb_slug": "kb-1",
                        "text": "expired by valid_until",
                        "valid_from": now - 7200,
                        "valid_until": now - 3600,
                    },
                ),
                PointStruct(
                    id=2,
                    vector=vector,
                    payload={
                        "org_id": "org-1",
                        "kb_slug": "kb-1",
                        "text": "expired by invalid_at",
                        "invalid_at": "2000-01-01T00:00:00+00:00",
                    },
                ),
                PointStruct(
                    id=3,
                    vector=vector,
                    payload={
                        "org_id": "org-1",
                        "kb_slug": "kb-1",
                        "text": "not yet valid",
                        "valid_from": now + 3600,
                        "valid_until": now + 7200,
                    },
                ),
                PointStruct(
                    id=4,
                    vector=vector,
                    payload={
                        "org_id": "org-1",
                        "kb_slug": "kb-1",
                        "text": "active by valid_until",
                        "valid_from": now - 3600,
                        "valid_until": now + 3600,
                    },
                ),
                PointStruct(
                    id=5,
                    vector=vector,
                    payload={
                        "org_id": "org-1",
                        "kb_slug": "kb-1",
                        "text": "timeless legacy",
                    },
                ),
            ],
        )

        try:
            with patch.object(search, "_get_client", return_value=client):
                req = RetrieveRequest(
                    query="test",
                    org_id="org-1",
                    scope="org",
                    kb_slugs=["kb-1"],
                )
                results = await search.hybrid_search([1.0, 0.0], req, 10)
        finally:
            await client.close()

        assert {r["text"] for r in results} == {
            "active by valid_until",
            "timeless legacy",
        }

    @pytest.mark.asyncio
    async def test_knowledge_search_aliases_valid_from_until_to_api_fields(self):
        """Retrieval API response fields expose ingest-contract temporal payloads."""
        point = _make_point(
            "c1",
            "chunk text",
            0.8,
            org_id="org-1",
            valid_from=1_700_000_000,
            valid_until=253_402_300_800,
        )
        mock_client = AsyncMock()
        mock_client.query_points.return_value = _make_query_response([point])

        with patch.object(search, "_get_client", return_value=mock_client):
            req = RetrieveRequest(query="test", org_id="org-1", scope="org")
            results = await search.hybrid_search([0.1, 0.2], req, 10)

        assert results[0]["valid_at"] == "2023-11-14T22:13:20+00:00"
        assert results[0]["invalid_at"] is None
