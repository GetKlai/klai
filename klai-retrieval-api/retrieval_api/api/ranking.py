"""Pure scoring / ranking helpers for the /retrieve pipeline.

Side-effect-free operations over the in-flight ``list[dict]`` chunk lists:
Reciprocal Rank Fusion of qdrant + graph results and the served-result
confidence band. Lifted out of ``retrieve.py`` (behavior-preserving) so the
orchestrator keeps pipeline wiring, not ranking math. Each already has
dedicated unit tests; ``retrieve`` re-imports both so the existing call sites
and the ``from retrieval_api.api.retrieve import _rrf_merge`` /
``_compute_confidence_band`` test imports are unchanged.
"""

from __future__ import annotations

from retrieval_api.models import ConfidenceBand
from retrieval_api.util.scores import ranking_score


def _compute_confidence_band(
    chunks: list[dict],
    *,
    high_threshold: float,
    low_threshold: float,
    reranker_enabled: bool,
) -> ConfidenceBand:
    """SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-1: bucket the served result by
    the max post-rerank ranking score. Driven by the litellm-hook
    anti-hallucination injection (REQ-2).

    A chunk only contributes when its ``reranker_score`` is numeric — that
    is the validity signal that the cross-encoder actually scored it. The
    contributed VALUE is ``final_rank_score`` when present (ranking contract
    active: reflects the page-context boost, which writes to that
    field) and ``reranker_score`` otherwise (shadow: the
    legacy boosts mutate reranker_score directly, so it already reflects
    them — the pre-contract behavior).

    Returns:
        - ``unknown`` when reranker is disabled, every chunk's reranker_score
          is None (fallback path), or the served list is empty
        - ``high`` when max ≥ high_threshold
        - ``low`` when max < low_threshold
        - ``medium`` otherwise

    Operates on the raw served list of dicts (post quality-floor +
    source-aware-select + quality-boost), NOT on the ChunkResult objects —
    boosted scores from REQ-3 must be reflected.
    """
    if not reranker_enabled or not chunks:
        return "unknown"
    valid_scores = [
        ranking_score(c, "reranker_score")
        for c in chunks
        if isinstance(c.get("reranker_score"), (int, float))
    ]
    if not valid_scores:
        return "unknown"
    max_score = max(valid_scores)
    if max_score >= high_threshold:
        return "high"
    if max_score < low_threshold:
        return "low"
    return "medium"


def _rrf_merge(qdrant_results: list[dict], graph_results: list[dict], k: int = 60) -> list[dict]:
    """Reciprocal Rank Fusion merge of two ranked result lists (AC-5)."""
    scores: dict[str, float] = {}
    items: dict[str, dict] = {}

    for rank, result in enumerate(qdrant_results):
        cid = result["chunk_id"]
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
        items[cid] = result

    for rank, result in enumerate(graph_results):
        cid = result["chunk_id"]
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
        if cid not in items:
            items[cid] = result

    merged = sorted(items.values(), key=lambda r: scores[r["chunk_id"]], reverse=True)
    for result in merged:
        result["score"] = scores[result["chunk_id"]]
    return merged
