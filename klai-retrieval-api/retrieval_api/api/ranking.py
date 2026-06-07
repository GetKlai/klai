"""Pure scoring / ranking helpers for the /retrieve pipeline.

Side-effect-free operations over the in-flight ``list[dict]`` chunk lists:
Reciprocal Rank Fusion of qdrant + graph results, the link-expand reranker
boost, and the served-result confidence band. Lifted out of ``retrieve.py``
(behavior-preserving) so the orchestrator keeps pipeline wiring, not ranking
math. Each already has dedicated unit tests; ``retrieve`` re-imports all three
so the existing call sites and the
``from retrieval_api.api.retrieve import _rrf_merge`` /
``_compute_confidence_band`` / ``_apply_link_expand_boost`` test imports are
unchanged.
"""

from __future__ import annotations

from retrieval_api.models import ConfidenceBand


def _compute_confidence_band(
    chunks: list[dict],
    *,
    high_threshold: float,
    low_threshold: float,
    reranker_enabled: bool,
) -> ConfidenceBand:
    """SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-1: bucket the served result by
    max(reranker_score). Driven by the litellm-hook anti-hallucination
    injection (REQ-2).

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
    scores = [c.get("reranker_score") for c in chunks]
    valid_scores = [s for s in scores if isinstance(s, (int, float))]
    if not valid_scores:
        return "unknown"
    max_score = max(valid_scores)
    if max_score >= high_threshold:
        return "high"
    if max_score < low_threshold:
        return "low"
    return "medium"


def _apply_link_expand_boost(
    chunks: list[dict],
    *,
    boost: float,
    enabled: bool,
) -> list[dict]:
    """SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-3: multiplicative reranker-score
    boost (capped at 1.0) for chunks whose ``_link_expanded`` flag is set.

    Applied AFTER rerank and BEFORE source-aware-select + quality-boost so
    expanded neighbours get a fair shot at the served top-K. With
    ``boost=1.0`` (default) this is a no-op; the SPEC ships safe and
    operators tune via env var once the eval baseline is captured.

    Mutates the input list in place (matches the surrounding pipeline's
    style) and returns the same list for ergonomic chaining.
    """
    if not enabled or boost <= 1.0:
        return chunks
    for chunk in chunks:
        if chunk.get("_link_expanded") and isinstance(chunk.get("reranker_score"), (int, float)):
            boosted = chunk["reranker_score"] * boost
            chunk["reranker_score"] = min(boosted, 1.0)
    # Re-sort by boosted reranker_score so downstream pickers see the new order.
    chunks.sort(
        key=lambda c: c.get("reranker_score") or c.get("score") or 0.0,
        reverse=True,
    )
    return chunks


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
