"""classify_gap pure-function tests.

Pinned behaviour from the original inline impl in
deploy/litellm/klai_knowledge.py. SPEC-KB-014 thresholds.
"""

from __future__ import annotations

from klai_retrieval_telemetry import RetrievalTelemetryConfig, classify_gap


def _cfg() -> RetrievalTelemetryConfig:
    return RetrievalTelemetryConfig(
        portal_api_url="http://portal-api:8000",
        portal_internal_secret="test-secret",
        portal_retrieval_log_url="http://portal-api:8000/internal/v1/retrieval-log",
        portal_gap_events_url="http://portal-api:8000/internal/v1/gap-events",
        gap_soft_threshold=0.4,
        gap_dense_threshold=0.35,
    )


def test_empty_chunks_is_hard_gap() -> None:
    assert classify_gap([], _cfg()) == "hard"


def test_all_reranker_below_soft_threshold_is_soft() -> None:
    chunks = [
        {"reranker_score": 0.1},
        {"reranker_score": 0.2},
        {"reranker_score": 0.3},
    ]
    assert classify_gap(chunks, _cfg()) == "soft"


def test_at_least_one_reranker_above_soft_threshold_is_none() -> None:
    chunks = [
        {"reranker_score": 0.1},
        {"reranker_score": 0.5},  # above 0.4
        {"reranker_score": 0.3},
    ]
    assert classify_gap(chunks, _cfg()) is None


def test_no_reranker_falls_back_to_dense_score() -> None:
    chunks = [
        {"score": 0.1},
        {"score": 0.2},
    ]
    assert classify_gap(chunks, _cfg()) == "soft"


def test_no_reranker_one_dense_above_threshold_is_none() -> None:
    chunks = [
        {"score": 0.1},
        {"score": 0.4},  # above 0.35
    ]
    assert classify_gap(chunks, _cfg()) is None


def test_reranker_present_overrides_dense() -> None:
    """If reranker_score is set on any chunk, dense scores are ignored."""
    chunks = [
        {"reranker_score": 0.1, "score": 0.99},  # dense high but reranker low
        {"reranker_score": 0.2, "score": 0.99},
    ]
    assert classify_gap(chunks, _cfg()) == "soft"


def test_threshold_boundary_exact_match_is_not_soft() -> None:
    """Score == threshold is NOT below threshold (strict less-than)."""
    chunks = [{"reranker_score": 0.4}]
    assert classify_gap(chunks, _cfg()) is None
