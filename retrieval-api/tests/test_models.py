"""Tests for Pydantic models and validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from retrieval_api.models import ChunkResult, RetrieveMetadata, RetrieveRequest, RetrieveResponse


class TestRetrieveRequest:
    def test_defaults(self):
        req = RetrieveRequest(query="hello", org_id="org-1")
        assert req.scope == "org"
        assert req.top_k == 8
        assert req.user_id is None
        assert req.notebook_id is None
        assert req.conversation_history == []

    def test_all_fields(self):
        req = RetrieveRequest(
            query="test",
            org_id="org-1",
            scope="personal",
            user_id="user-1",
            notebook_id="nb-1",
            top_k=3,
            conversation_history=[{"role": "user", "content": "hi"}],
        )
        assert req.scope == "personal"
        assert req.user_id == "user-1"
        assert req.top_k == 3

    def test_invalid_scope(self):
        with pytest.raises(ValidationError):
            RetrieveRequest(query="hello", org_id="org-1", scope="invalid")

    def test_missing_required_fields(self):
        with pytest.raises(ValidationError):
            RetrieveRequest(query="hello")  # missing org_id


class TestChunkResult:
    def test_minimal(self):
        chunk = ChunkResult(chunk_id="c1", text="hello", score=0.5)
        assert chunk.artifact_id is None
        assert chunk.reranker_score is None

    def test_full(self):
        chunk = ChunkResult(
            chunk_id="c1",
            artifact_id="a1",
            content_type="policy",
            text="hello",
            context_prefix="Prefix: ",
            score=0.9,
            reranker_score=0.95,
            scope="org",
            valid_at="2024-01-01",
            invalid_at=None,
        )
        assert chunk.reranker_score == 0.95


class TestRetrieveResponse:
    def test_structure(self):
        resp = RetrieveResponse(
            query_resolved="test query",
            retrieval_bypassed=False,
            chunks=[],
            metadata=RetrieveMetadata(
                candidates_retrieved=10,
                reranked_to=5,
                retrieval_ms=42.0,
            ),
        )
        assert resp.metadata.rerank_ms is None
        assert resp.metadata.gate_margin is None
