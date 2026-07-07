"""Quality score boost for retrieval results.

# @MX:NOTE: [AUTO] Applies feedback-based quality boost after RRF+rerank. SPEC-KB-015.
# @MX:SPEC: SPEC-KB-015 REQ-KB-015-19 through REQ-KB-015-21

Cold start guard: only boosts when feedback_count >= 3.
Missing fields default to quality_score=0.5, feedback_count=0 (no boost).

SPEC-RAG-EVIDENCE-INTEGRITY-001 REQ-RANK-03: behavior is mode-dependent via
``contract_active``.

- ``contract_active=False`` (ranking-contract shadow, the default): the exact
  pre-contract behavior — boost mutates the raw ``score`` and the list is
  ALWAYS re-sorted by ``score``. Byte-identical serving per REQ-RANK-04.
- ``contract_active=True``: boost mutates ``final_rank_score`` (the single
  post-rerank ranking truth, REQ-RANK-01) and the list is re-sorted only
  when at least one chunk actually got boosted.
"""

from __future__ import annotations

from retrieval_api.util.scores import ranking_score

# Cold-start threshold: 3 votes before boost activates.
# Industry standard (Wilson lower bound) recommends 5-10, but Klai's user base
# is too small to reach that in practice — chunks would never get boosted.
# 3 filters out accidental single clicks while still being reachable.
# Re-evaluate if MAU grows significantly.
_COLD_START_MIN_VOTES = 3

# Boost magnitude: max ±10% score adjustment.
# Validated against Vespa/Elasticsearch LTR research (0.1-0.2 range).
_BOOST_FACTOR = 0.2


def quality_boost(reranked: list[dict], *, contract_active: bool = False) -> list[dict]:
    """Apply quality score boost to reranked results.

    Formula: boosted = base * (1 + 0.2 * (quality_score - 0.5))

    Only applied when feedback_count >= 3 (cold start guard).
    Missing quality_score defaults to 0.5 (neutral, no boost).
    Missing feedback_count defaults to 0 (no boost).
    """
    if not reranked:
        return reranked

    boosted_any = False
    for r in reranked:
        fc = r.get("feedback_count", 0)
        qs = r.get("quality_score", 0.5)
        if isinstance(fc, (int, float)) and fc >= _COLD_START_MIN_VOTES:
            factor = 1 + _BOOST_FACTOR * (qs - 0.5)
            if contract_active:
                base = r.get("final_rank_score")
                if not isinstance(base, (int, float)):
                    base = r.get("score", 0.0)
                r["final_rank_score"] = base * factor
            else:
                r["score"] = r["score"] * factor
            boosted_any = True

    if contract_active:
        if boosted_any:
            reranked.sort(key=lambda c: ranking_score(c, "score"), reverse=True)
    else:
        # Legacy path: unconditional re-sort by raw score, exactly as before
        # the ranking contract — shadow serving must not change.
        reranked.sort(key=lambda c: c["score"], reverse=True)
    return reranked
