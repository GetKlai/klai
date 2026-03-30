"""Tests for SPEC-TAXONOMY-001: assertion_mode taxonomy alignment in retrieval-api.

RED phase: verify that assertion_mode flows through search results.
The retrieval-api search.py already passes assertion_mode through — these tests
verify the new vocabulary values are correctly returned.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from retrieval_api.models import RetrieveRequest
from retrieval_api.services import search


def _make_point(id_: str, text: str, score: float, **extra_payload):
    payload = {"text": text, **extra_payload}
    return SimpleNamespace(id=id_, score=score, payload=payload)


def _make_query_response(points: list):
    return SimpleNamespace(points=points)


class TestNewTaxonomyValuesPassthrough:
    """All 6 new assertion_mode values must be returned from search results."""

    @pytest.fixture(autouse=True)
    def reset_client(self):
        search._client = None
        yield
        search._client = None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ["fact", "claim", "speculation", "procedural", "quoted", "unknown"])
    async def test_new_assertion_mode_values_pass_through(self, mode):
        """Each new taxonomy value must appear in search results."""
        point = _make_point(
            "c1", "chunk text", 0.8,
            org_id="org-1",
            assertion_mode=mode,
        )
        mock_client = AsyncMock()
        mock_client.query_points.return_value = _make_query_response([point])

        with patch.object(search, "_get_client", return_value=mock_client):
            req = RetrieveRequest(query="test", org_id="org-1", scope="org")
            results = await search.hybrid_search([0.1, 0.2], req, 10)

        assert results[0]["assertion_mode"] == mode

    @pytest.mark.asyncio
    async def test_notebook_search_returns_assertion_mode(self):
        """Notebook search results must also include assertion_mode."""
        point = SimpleNamespace(
            id="c1",
            score=0.9,
            payload={
                "content": "focus content",
                "tenant_id": "org-1",
                "notebook_id": "nb-1",
                "assertion_mode": "fact",
            },
        )
        mock_client = AsyncMock()
        mock_client.query_points.return_value = _make_query_response([point])

        with patch.object(search, "_get_client", return_value=mock_client):
            req = RetrieveRequest(
                query="test",
                org_id="org-1",
                scope="notebook",
                notebook_id="nb-1",
            )
            results = await search.hybrid_search([0.1, 0.2], req, 10)

        assert results[0]["assertion_mode"] == "fact"
