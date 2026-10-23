"""Tests for the confidence_band helper.

SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-1.

Covers the pure helper function ``_compute_confidence_band`` from
``retrieval_api.api.retrieve``. The end-to-end pipeline (band emit on a real /retrieve
response) is exercised by the existing integration tests + the eval harness post-deploy.
"""

from __future__ import annotations

import pytest

from retrieval_api.api.retrieve import _compute_confidence_band

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
