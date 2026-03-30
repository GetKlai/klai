"""Tests for evidence tier scoring (SPEC-EVIDENCE-001).

Covers:
- TASK-004: EvidenceProfile configuration object (R6)
- TASK-005: Scoring functions (R1, R3, R5)
- TASK-006: apply() + U-shape ordering (R7, R2)
"""

from __future__ import annotations

import os
import time
from unittest.mock import patch

import pytest


# -- TASK-004: EvidenceProfile structure (R6) ----------------------------------


class TestEvidenceProfile:
    def test_default_profile_has_all_content_types(self):
        """DEFAULT_EVIDENCE_PROFILE contains weights for all known content types."""
        from retrieval_api.services.evidence_tier import DEFAULT_EVIDENCE_PROFILE

        expected_types = {
            "kb_article", "pdf_document", "meeting_transcript",
            "1on1_transcript", "web_crawl", "graph_edge", "unknown",
        }
        assert set(DEFAULT_EVIDENCE_PROFILE["content_type_weights"].keys()) == expected_types

    def test_default_profile_weights_are_between_0_and_1(self):
        """All content type weights must be in [0, 1]."""
        from retrieval_api.services.evidence_tier import DEFAULT_EVIDENCE_PROFILE

        for ct, weight in DEFAULT_EVIDENCE_PROFILE["content_type_weights"].items():
            assert 0.0 <= weight <= 1.0, f"{ct} weight {weight} out of range"

    def test_default_profile_temporal_decay_keys(self):
        """Temporal decay profile has the expected age buckets."""
        from retrieval_api.services.evidence_tier import DEFAULT_EVIDENCE_PROFILE

        expected = {"lt_30", "d30_180", "d180_365", "gt_365"}
        assert set(DEFAULT_EVIDENCE_PROFILE["temporal_decay"].keys()) == expected

    def test_kb_article_has_highest_weight(self):
        """kb_article should be the highest-weighted content type."""
        from retrieval_api.services.evidence_tier import DEFAULT_EVIDENCE_PROFILE

        weights = DEFAULT_EVIDENCE_PROFILE["content_type_weights"]
        assert weights["kb_article"] >= max(
            v for k, v in weights.items() if k != "kb_article"
        )

    def test_default_profile_specific_values(self):
        """Verify specific weight values from the SPEC."""
        from retrieval_api.services.evidence_tier import DEFAULT_EVIDENCE_PROFILE

        w = DEFAULT_EVIDENCE_PROFILE["content_type_weights"]
        assert w["kb_article"] == 1.00
        assert w["pdf_document"] == 0.90
        assert w["meeting_transcript"] == 0.80
        assert w["1on1_transcript"] == 0.80
        assert w["web_crawl"] == 0.65
        assert w["graph_edge"] == 0.70
        assert w["unknown"] == 0.55


# -- TASK-005: Scoring functions (R1, R3, R5) ----------------------------------


class TestContentTypeWeight:
    def test_known_type_returns_weight(self):
        from retrieval_api.services.evidence_tier import (
            DEFAULT_EVIDENCE_PROFILE,
            _content_type_weight,
        )

        assert _content_type_weight("kb_article", DEFAULT_EVIDENCE_PROFILE) == 1.00
        assert _content_type_weight("web_crawl", DEFAULT_EVIDENCE_PROFILE) == 0.65

    def test_unknown_type_returns_unknown_weight(self):
        from retrieval_api.services.evidence_tier import (
            DEFAULT_EVIDENCE_PROFILE,
            _content_type_weight,
        )

        assert _content_type_weight("nonexistent_type", DEFAULT_EVIDENCE_PROFILE) == 0.55

    def test_none_type_returns_unknown_weight(self):
        from retrieval_api.services.evidence_tier import (
            DEFAULT_EVIDENCE_PROFILE,
            _content_type_weight,
        )

        assert _content_type_weight(None, DEFAULT_EVIDENCE_PROFILE) == 0.55

    def test_feature_flag_disabled_returns_1(self):
        from retrieval_api.services.evidence_tier import (
            DEFAULT_EVIDENCE_PROFILE,
            _content_type_weight,
        )

        with patch.dict(os.environ, {"EVIDENCE_CONTENT_TYPE_ENABLED": "false"}):
            assert _content_type_weight("web_crawl", DEFAULT_EVIDENCE_PROFILE) == 1.0


class TestAssertionWeight:
    def test_always_returns_1(self):
        """v1: assertion weight is always 1.00 regardless of mode."""
        from retrieval_api.services.evidence_tier import (
            DEFAULT_EVIDENCE_PROFILE,
            _assertion_weight,
        )

        assert _assertion_weight("factual", DEFAULT_EVIDENCE_PROFILE) == 1.00
        assert _assertion_weight("procedural", DEFAULT_EVIDENCE_PROFILE) == 1.00
        assert _assertion_weight(None, DEFAULT_EVIDENCE_PROFILE) == 1.00
        assert _assertion_weight("hypothesis", DEFAULT_EVIDENCE_PROFILE) == 1.00


class TestTemporalDecay:
    def test_recent_chunk_no_decay(self):
        """Chunks < 30 days old get no decay (factor 1.00)."""
        from retrieval_api.services.evidence_tier import (
            DEFAULT_EVIDENCE_PROFILE,
            _temporal_decay,
        )

        recent = int(time.time()) - (10 * 86400)  # 10 days ago
        assert _temporal_decay(recent, DEFAULT_EVIDENCE_PROFILE) == 1.00

    def test_30_to_180_days_decay(self):
        """Chunks 30-180 days old get 0.95 decay."""
        from retrieval_api.services.evidence_tier import (
            DEFAULT_EVIDENCE_PROFILE,
            _temporal_decay,
        )

        age = int(time.time()) - (90 * 86400)  # 90 days ago
        assert _temporal_decay(age, DEFAULT_EVIDENCE_PROFILE) == 0.95

    def test_180_to_365_days_decay(self):
        """Chunks 180-365 days old get 0.90 decay."""
        from retrieval_api.services.evidence_tier import (
            DEFAULT_EVIDENCE_PROFILE,
            _temporal_decay,
        )

        age = int(time.time()) - (250 * 86400)  # 250 days ago
        assert _temporal_decay(age, DEFAULT_EVIDENCE_PROFILE) == 0.90

    def test_gt_365_days_decay(self):
        """Chunks > 365 days old get 0.85 decay."""
        from retrieval_api.services.evidence_tier import (
            DEFAULT_EVIDENCE_PROFILE,
            _temporal_decay,
        )

        age = int(time.time()) - (400 * 86400)  # 400 days ago
        assert _temporal_decay(age, DEFAULT_EVIDENCE_PROFILE) == 0.85

    def test_none_ingested_at_returns_1(self):
        """None ingested_at returns 1.0 (no decay)."""
        from retrieval_api.services.evidence_tier import (
            DEFAULT_EVIDENCE_PROFILE,
            _temporal_decay,
        )

        assert _temporal_decay(None, DEFAULT_EVIDENCE_PROFILE) == 1.00

    def test_feature_flag_disabled_returns_1(self):
        from retrieval_api.services.evidence_tier import (
            DEFAULT_EVIDENCE_PROFILE,
            _temporal_decay,
        )

        old = int(time.time()) - (400 * 86400)
        with patch.dict(os.environ, {"EVIDENCE_TEMPORAL_DECAY_ENABLED": "false"}):
            assert _temporal_decay(old, DEFAULT_EVIDENCE_PROFILE) == 1.0


# -- TASK-006: apply() + U-shape ordering (R7, R2) ----------------------------


def _make_chunk(chunk_id: str, score: float, content_type: str = "kb_article",
                ingested_at: int | None = None) -> dict:
    """Create a chunk dict as returned by the reranker."""
    return {
        "chunk_id": chunk_id,
        "text": f"Text for {chunk_id}",
        "score": score,
        "reranker_score": score,
        "artifact_id": f"art-{chunk_id}",
        "content_type": content_type,
        "context_prefix": None,
        "scope": "org",
        "valid_at": None,
        "invalid_at": None,
        "ingested_at": ingested_at or int(time.time()),
        "assertion_mode": "factual",
    }


class TestApply:
    def test_empty_list(self):
        from retrieval_api.services.evidence_tier import apply

        result = apply([])
        assert result == []

    def test_single_chunk(self):
        from retrieval_api.services.evidence_tier import apply

        chunks = [_make_chunk("c1", 0.9)]
        result = apply(chunks)
        assert len(result) == 1
        assert result[0]["final_score"] is not None

    def test_final_score_formula(self):
        """final_score = reranker_score * content_type_weight * assertion_weight * temporal_decay."""
        from retrieval_api.services.evidence_tier import apply

        chunks = [_make_chunk("c1", 0.80, content_type="kb_article")]
        result = apply(chunks)
        # kb_article = 1.00, assertion = 1.00, temporal (recent) = 1.00
        assert result[0]["final_score"] == pytest.approx(0.80)

    def test_content_type_affects_score(self):
        """web_crawl (0.65) should score lower than kb_article (1.00)."""
        from retrieval_api.services.evidence_tier import apply

        chunks = [
            _make_chunk("c1", 0.80, content_type="kb_article"),
            _make_chunk("c2", 0.80, content_type="web_crawl"),
        ]
        result = apply(chunks)
        scores = {r["chunk_id"]: r["final_score"] for r in result}
        assert scores["c1"] > scores["c2"]

    def test_evidence_tier_metadata_included(self):
        """Each chunk gets evidence_tier_metadata dict."""
        from retrieval_api.services.evidence_tier import apply

        chunks = [_make_chunk("c1", 0.80)]
        result = apply(chunks)
        meta = result[0]["evidence_tier_metadata"]
        assert "content_type_weight" in meta
        assert "assertion_weight" in meta
        assert "temporal_decay" in meta


class TestUShapeOrdering:
    def test_no_reorder_lt_3(self):
        """Lists with < 3 items should not be reordered."""
        from retrieval_api.services.evidence_tier import _order_for_llm

        chunks = [_make_chunk("c1", 0.9), _make_chunk("c2", 0.8)]
        for c in chunks:
            c["final_score"] = c["score"]
        result = _order_for_llm(chunks)
        assert [r["chunk_id"] for r in result] == ["c1", "c2"]

    def test_u_shape_3_chunks(self):
        """3 chunks: [strongest, weakest, second-strongest]."""
        from retrieval_api.services.evidence_tier import _order_for_llm

        chunks = [
            _make_chunk("c1", 0.9),
            _make_chunk("c2", 0.7),
            _make_chunk("c3", 0.8),
        ]
        for c in chunks:
            c["final_score"] = c["score"]
        result = _order_for_llm(chunks)
        ids = [r["chunk_id"] for r in result]
        # Sorted desc: c1(0.9), c3(0.8), c2(0.7)
        # U-shape: even indices front [c1, c2], odd indices reversed [c3]
        # Result: [c1, c2, c3]
        assert ids[0] == "c1"   # strongest at front
        assert ids[-1] == "c3"  # second strongest at back

    def test_u_shape_6_chunks(self):
        """6 chunks: strongest at position 0, second-strongest at last position."""
        from retrieval_api.services.evidence_tier import _order_for_llm

        chunks = [_make_chunk(f"c{i}", 0.9 - i * 0.1) for i in range(6)]
        for c in chunks:
            c["final_score"] = c["score"]
        result = _order_for_llm(chunks)
        ids = [r["chunk_id"] for r in result]
        # Sorted desc: c0(0.9), c1(0.8), c2(0.7), c3(0.6), c4(0.5), c5(0.4)
        # Even indices: c0, c2, c4 -> front
        # Odd indices reversed: c5, c3, c1 -> back
        # Result: [c0, c2, c4, c5, c3, c1]
        assert ids[0] == "c0"  # strongest at front
        assert ids[-1] == "c1"  # second strongest at back

    def test_u_shape_empty(self):
        from retrieval_api.services.evidence_tier import _order_for_llm

        assert _order_for_llm([]) == []

    def test_u_shape_single(self):
        from retrieval_api.services.evidence_tier import _order_for_llm

        chunks = [_make_chunk("c1", 0.9)]
        chunks[0]["final_score"] = 0.9
        result = _order_for_llm(chunks)
        assert len(result) == 1
