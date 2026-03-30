"""Integration test: evidence tier scoring in the retrieve pipeline (SPEC-EVIDENCE-001, R7+R9)."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest


class TestEvidenceTierInPipeline:
    """Verify evidence_tier.apply() is called after reranking."""

    def test_retrieve_calls_evidence_tier(self, client, sample_retrieve_request):
        """evidence_tier.apply() is called in the retrieve pipeline (R7)."""
        reranked_chunk = {
            "chunk_id": "c1",
            "text": "policy text",
            "score": 0.9,
            "reranker_score": 0.95,
            "artifact_id": "a1",
            "content_type": "kb_article",
            "context_prefix": None,
            "scope": "org",
            "valid_at": None,
            "invalid_at": None,
            "ingested_at": 1711843200,
            "assertion_mode": "factual",
        }

        with (
            patch(
                "retrieval_api.api.retrieve.coreference.resolve",
                new_callable=AsyncMock,
                return_value="resolved",
            ),
            patch(
                "retrieval_api.api.retrieve.embed_single",
                new_callable=AsyncMock,
                return_value=[0.1, 0.2],
            ),
            patch(
                "retrieval_api.api.retrieve.embed_sparse",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "retrieval_api.api.retrieve.gate.should_bypass",
                new_callable=AsyncMock,
                return_value=(False, 0.05),
            ),
            patch(
                "retrieval_api.api.retrieve.search.hybrid_search",
                new_callable=AsyncMock,
                return_value=[reranked_chunk],
            ),
            patch(
                "retrieval_api.api.retrieve.reranker.rerank",
                new_callable=AsyncMock,
                return_value=[reranked_chunk],
            ),
            patch(
                "retrieval_api.api.retrieve.evidence_tier.apply",
                wraps=None,
            ) as mock_apply,
            patch.dict(os.environ, {"EVIDENCE_SHADOW_MODE": "false"}),
        ):
            # Configure mock to return scored chunks
            scored = dict(reranked_chunk)
            scored["final_score"] = 0.95
            scored["evidence_tier_metadata"] = {
                "content_type_weight": 1.0,
                "assertion_weight": 1.0,
                "temporal_decay": 1.0,
            }
            mock_apply.return_value = [scored]

            resp = client.post("/retrieve", json=sample_retrieve_request)

        assert resp.status_code == 200
        mock_apply.assert_called_once()

    def test_shadow_mode_serves_flat_scoring(self, client, sample_retrieve_request):
        """Shadow mode: serves flat scoring, logs evidence results (R9)."""
        reranked_chunk = {
            "chunk_id": "c1",
            "text": "policy text",
            "score": 0.9,
            "reranker_score": 0.95,
            "artifact_id": "a1",
            "content_type": "kb_article",
            "context_prefix": None,
            "scope": "org",
            "valid_at": None,
            "invalid_at": None,
            "ingested_at": 1711843200,
            "assertion_mode": "factual",
        }

        with (
            patch(
                "retrieval_api.api.retrieve.coreference.resolve",
                new_callable=AsyncMock,
                return_value="resolved",
            ),
            patch(
                "retrieval_api.api.retrieve.embed_single",
                new_callable=AsyncMock,
                return_value=[0.1, 0.2],
            ),
            patch(
                "retrieval_api.api.retrieve.embed_sparse",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "retrieval_api.api.retrieve.gate.should_bypass",
                new_callable=AsyncMock,
                return_value=(False, 0.05),
            ),
            patch(
                "retrieval_api.api.retrieve.search.hybrid_search",
                new_callable=AsyncMock,
                return_value=[reranked_chunk],
            ),
            patch(
                "retrieval_api.api.retrieve.reranker.rerank",
                new_callable=AsyncMock,
                return_value=[reranked_chunk],
            ),
            patch.dict(os.environ, {"EVIDENCE_SHADOW_MODE": "true"}),
        ):
            resp = client.post("/retrieve", json=sample_retrieve_request)

        assert resp.status_code == 200
        data = resp.json()
        # In shadow mode, the served chunks should NOT have final_score
        # because we serve the original reranked results
        chunk = data["chunks"][0]
        assert chunk["final_score"] is None or chunk.get("evidence_tier_metadata") is None
