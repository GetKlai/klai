"""Tests for confidence_band emit + link-expand reranker boost.

SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-1, REQ-3.

Covers the pure helper functions ``_compute_confidence_band`` and
``_apply_link_expand_boost`` from ``retrieval_api.api.retrieve``. The
end-to-end pipeline (band emit on a real /retrieve response) is exercised
by the existing integration tests + the eval harness post-deploy.
"""

from __future__ import annotations

import pytest

from retrieval_api.api.retrieve import (
    _apply_link_expand_boost,
    _compute_confidence_band,
)

# ---------------------------------------------------------------------------
# REQ-1 — _compute_confidence_band
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("max_score", "expected"),
    [
        (0.95, "high"),
        (0.61, "high"),
        (0.60, "high"),  # >= high threshold
        (0.59, "medium"),
        (0.40, "medium"),
        (0.30, "medium"),  # >= low threshold but < high
        (0.29, "low"),
        (0.18, "low"),  # the Voys-Salesforce 2026-05-07 turn-1 score
        (0.05, "low"),
    ],
)
def test_band_returns_correct_bucket(max_score: float, expected: str) -> None:
    """Various reranker scores map to the right band."""
    chunks = [{"reranker_score": max_score}, {"reranker_score": max_score - 0.05}]
    band = _compute_confidence_band(
        chunks,
        high_threshold=0.60,
        low_threshold=0.30,
        reranker_enabled=True,
    )
    assert band == expected


def test_band_uses_max_not_first() -> None:
    """Band must be computed on max(scores), not first chunk's score."""
    chunks = [
        {"reranker_score": 0.10},  # first chunk has low score
        {"reranker_score": 0.80},  # but second is high
    ]
    band = _compute_confidence_band(
        chunks,
        high_threshold=0.60,
        low_threshold=0.30,
        reranker_enabled=True,
    )
    assert band == "high"


def test_band_unknown_when_reranker_disabled() -> None:
    """When reranker is disabled, scores are not meaningful → unknown."""
    chunks = [{"reranker_score": 0.95}]  # would be high if enabled
    band = _compute_confidence_band(
        chunks,
        high_threshold=0.60,
        low_threshold=0.30,
        reranker_enabled=False,
    )
    assert band == "unknown"


def test_band_unknown_when_chunks_empty() -> None:
    """Empty served list → unknown (no signal to base a decision on)."""
    band = _compute_confidence_band(
        [],
        high_threshold=0.60,
        low_threshold=0.30,
        reranker_enabled=True,
    )
    assert band == "unknown"


def test_band_unknown_when_all_scores_none() -> None:
    """Reranker fallback path sets reranker_score=None on every chunk →
    unknown is the correct answer (we can't trust qdrant's RRF score on its
    own to drive the abstention layer).
    """
    chunks = [{"reranker_score": None}, {"reranker_score": None}]
    band = _compute_confidence_band(
        chunks,
        high_threshold=0.60,
        low_threshold=0.30,
        reranker_enabled=True,
    )
    assert band == "unknown"


def test_band_ignores_none_among_valid_scores() -> None:
    """If some chunks have None and others have real scores, use the real
    scores. (Mixed state shouldn't happen in practice but guard against it.)
    """
    chunks = [
        {"reranker_score": None},
        {"reranker_score": 0.45},
        {"reranker_score": None},
    ]
    band = _compute_confidence_band(
        chunks,
        high_threshold=0.60,
        low_threshold=0.30,
        reranker_enabled=True,
    )
    assert band == "medium"


def test_band_handles_zero_score() -> None:
    """Edge: max_score=0 must produce 'low' (any low_threshold > 0)."""
    chunks = [{"reranker_score": 0.0}]
    band = _compute_confidence_band(
        chunks,
        high_threshold=0.60,
        low_threshold=0.30,
        reranker_enabled=True,
    )
    assert band == "low"


def test_band_handles_int_score() -> None:
    """Robustness: reranker upstream may return int 0 or 1; isinstance
    check must accept both int and float.
    """
    chunks = [{"reranker_score": 1}, {"reranker_score": 0}]
    band = _compute_confidence_band(
        chunks,
        high_threshold=0.60,
        low_threshold=0.30,
        reranker_enabled=True,
    )
    assert band == "high"


# ---------------------------------------------------------------------------
# REQ-3 — _apply_link_expand_boost
# ---------------------------------------------------------------------------


def test_boost_noop_when_disabled() -> None:
    """link_expand_enabled=False → no boost regardless of factor."""
    chunks = [
        {"chunk_id": "a", "_link_expanded": True, "reranker_score": 0.5},
        {"chunk_id": "b", "reranker_score": 0.7},
    ]
    out = _apply_link_expand_boost(chunks, boost=1.20, enabled=False)
    assert out[0]["reranker_score"] == 0.5
    assert out[1]["reranker_score"] == 0.7


def test_boost_noop_when_factor_is_one() -> None:
    """boost=1.0 (default) is a deliberate no-op; ships safe."""
    chunks = [
        {"chunk_id": "a", "_link_expanded": True, "reranker_score": 0.5},
        {"chunk_id": "b", "reranker_score": 0.7},
    ]
    out = _apply_link_expand_boost(chunks, boost=1.00, enabled=True)
    assert out[0]["reranker_score"] == 0.5
    assert out[1]["reranker_score"] == 0.7


def test_boost_applies_to_link_expanded_only() -> None:
    """Shadow (no final_rank_score present): pre-contract behavior — the
    boost mutates reranker_score directly; unflagged chunks stay untouched."""
    chunks = [
        {"chunk_id": "a", "_link_expanded": True, "reranker_score": 0.5},
        {"chunk_id": "b", "reranker_score": 0.5},  # no flag
    ]
    out = _apply_link_expand_boost(chunks, boost=1.20, enabled=True)
    boosted = next(c for c in out if c["chunk_id"] == "a")
    not_boosted = next(c for c in out if c["chunk_id"] == "b")
    assert boosted["reranker_score"] == pytest.approx(0.6)
    assert "final_rank_score" not in boosted
    assert not_boosted["reranker_score"] == 0.5


def test_boost_caps_at_one() -> None:
    """A 1.20 boost on 0.95 would compute 1.14 — must cap at 1.0."""
    chunks = [{"chunk_id": "a", "_link_expanded": True, "reranker_score": 0.95}]
    out = _apply_link_expand_boost(chunks, boost=1.20, enabled=True)
    assert out[0]["reranker_score"] == 1.0


def test_boost_resorts_results() -> None:
    """After boost, the list must be re-sorted so source-aware-select sees
    the new order.
    """
    chunks = [
        {"chunk_id": "a", "reranker_score": 0.55},  # was top
        {"chunk_id": "b", "_link_expanded": True, "reranker_score": 0.50},  # boosted to 0.60
        {"chunk_id": "c", "reranker_score": 0.40},
    ]
    out = _apply_link_expand_boost(chunks, boost=1.20, enabled=True)
    assert [c["chunk_id"] for c in out] == ["b", "a", "c"]
    assert out[0]["reranker_score"] == pytest.approx(0.60)


def test_boost_targets_final_rank_score_when_contract_active() -> None:
    """Active (final_rank_score present, REQ-RANK-01): the boost writes the
    single ranking truth and leaves reranker_score untouched."""
    chunks = [
        {"chunk_id": "a", "reranker_score": 0.55, "final_rank_score": 0.55},
        {
            "chunk_id": "b",
            "_link_expanded": True,
            "reranker_score": 0.50,
            "final_rank_score": 0.50,
        },
    ]
    out = _apply_link_expand_boost(chunks, boost=1.20, enabled=True)
    boosted = next(c for c in out if c["chunk_id"] == "b")
    assert boosted["final_rank_score"] == pytest.approx(0.60)
    assert boosted["reranker_score"] == 0.50
    assert [c["chunk_id"] for c in out] == ["b", "a"]


def test_boost_skips_chunks_with_none_score() -> None:
    """Reranker-fallback chunks have reranker_score=None — boost must skip
    them to avoid TypeError on multiplication.
    """
    chunks = [
        {"chunk_id": "a", "_link_expanded": True, "reranker_score": None, "score": 0.5},
        {"chunk_id": "b", "_link_expanded": True, "reranker_score": 0.5, "score": 0.5},
    ]
    out = _apply_link_expand_boost(chunks, boost=1.20, enabled=True)
    a = next(c for c in out if c["chunk_id"] == "a")
    b = next(c for c in out if c["chunk_id"] == "b")
    assert a["reranker_score"] is None
    assert "final_rank_score" not in a
    assert b["reranker_score"] == pytest.approx(0.6)


def test_boost_returns_same_list_for_chaining() -> None:
    """Helper mutates and returns input list (matches surrounding pipeline)."""
    chunks: list[dict] = []
    out = _apply_link_expand_boost(chunks, boost=1.10, enabled=True)
    assert out is chunks


def test_band_prefers_final_rank_score_when_present() -> None:
    """Active mode: boosts write final_rank_score; the band must reflect
    them (REQ-3 'boosted scores must be reflected')."""
    band = _compute_confidence_band(
        [{"reranker_score": 0.55, "final_rank_score": 0.70}],
        high_threshold=0.60,
        low_threshold=0.30,
        reranker_enabled=True,
    )
    assert band == "high"


def test_band_stays_unknown_on_reranker_fallback_despite_final_rank_score() -> None:
    """Active mode + reranker fallback: final_rank_score falls back to the
    RRF score, but no chunk was cross-encoder-scored — the band must stay
    'unknown' instead of misreading RRF-scale values as low confidence."""
    band = _compute_confidence_band(
        [{"reranker_score": None, "final_rank_score": 0.02, "score": 0.02}],
        high_threshold=0.60,
        low_threshold=0.30,
        reranker_enabled=True,
    )
    assert band == "unknown"


# ---------------------------------------------------------------------------
# Config validation (covered separately in test_models.py / test_settings)
# but a smoke-check here ensures the helpers themselves accept the validated
# range.
# ---------------------------------------------------------------------------


def test_band_thresholds_at_boundaries() -> None:
    """high_threshold=1.0 means only score=1.0 maps to 'high'."""
    band = _compute_confidence_band(
        [{"reranker_score": 1.0}],
        high_threshold=1.0,
        low_threshold=0.0,
        reranker_enabled=True,
    )
    assert band == "high"

    band = _compute_confidence_band(
        [{"reranker_score": 0.99}],
        high_threshold=1.0,
        low_threshold=0.0,
        reranker_enabled=True,
    )
    # 0.99 < 1.0 → not high, but >= 0.0 → medium
    assert band == "medium"
