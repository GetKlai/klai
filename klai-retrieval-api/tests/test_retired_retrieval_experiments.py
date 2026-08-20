"""Regression tests for retired retrieval experiments."""

from __future__ import annotations

import importlib.util
import os
from unittest.mock import AsyncMock, patch

from retrieval_api.config import Settings


def test_retired_experiments_are_not_configurable_or_importable():
    """Removed experiments cannot be accidentally re-enabled in production."""
    configured_fields = Settings.model_fields

    assert "retrieval_gate_enabled" not in configured_fields
    assert "retrieval_gate_threshold" not in configured_fields
    assert "retrieval_gate_shadow" not in configured_fields
    assert importlib.util.find_spec("retrieval_api.services.gate") is None
    assert importlib.util.find_spec("retrieval_api.services.evidence_tier") is None


def test_legacy_evidence_environment_cannot_change_served_order(client, sample_retrieve_request):
    """The normal post-rerank order is served regardless of a stale env var."""
    reranked = [
        {
            "chunk_id": "meeting",
            "text": "meeting text",
            "score": 0.90,
            "reranker_score": 0.90,
            "content_type": "meeting_transcript",
            "source_url": "https://example.test/meeting",
        },
        {
            "chunk_id": "article",
            "text": "article text",
            "score": 0.80,
            "reranker_score": 0.80,
            "content_type": "kb_article",
            "source_url": "https://example.test/article",
        },
        {
            "chunk_id": "crawl",
            "text": "crawl text",
            "score": 0.70,
            "reranker_score": 0.70,
            "content_type": "web_crawl",
            "source_url": "https://example.test/crawl",
        },
    ]

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
            "retrieval_api.api.retrieve.search.hybrid_search",
            new_callable=AsyncMock,
            return_value=reranked,
        ),
        patch(
            "retrieval_api.api.retrieve.reranker.rerank",
            new_callable=AsyncMock,
            return_value=reranked,
        ),
        patch(
            "retrieval_api.services.parent_lookup.fetch_parents",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch("retrieval_api.api.retrieve.settings") as mock_settings,
        patch.dict(os.environ, {"EVIDENCE_SHADOW_MODE": "active"}),
    ):
        mock_settings.ranking_contract_mode = "active"
        mock_settings.retrieval_candidates = 60
        mock_settings.reranker_candidates = 20
        mock_settings.reranker_enabled = True
        mock_settings.graphiti_enabled = False
        mock_settings.link_expand_enabled = False
        mock_settings.link_expand_score_boost = 1.0
        mock_settings.source_quota_enabled = False
        mock_settings.router_enabled = False
        mock_settings.retrieval_quality_floor = 0.05
        mock_settings.confidence_band_high_threshold = 0.60
        mock_settings.confidence_band_low_threshold = 0.30

        response = client.post("/retrieve", json=sample_retrieve_request)

    assert response.status_code == 200
    assert [chunk["chunk_id"] for chunk in response.json()["chunks"]] == [
        "meeting",
        "article",
        "crawl",
    ]
