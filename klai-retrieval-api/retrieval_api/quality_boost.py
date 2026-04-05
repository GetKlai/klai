"""Quality score boost for retrieval results.

# @MX:NOTE: [AUTO] Applies feedback-based quality boost after RRF+rerank. SPEC-KB-015.
# @MX:SPEC: SPEC-KB-015 REQ-KB-015-19 through REQ-KB-015-21

Cold start guard: only boosts when feedback_count >= 3.
Missing fields default to quality_score=0.5, feedback_count=0 (no boost).
"""

# Cold-start threshold: 3 votes before boost activates.
# Industry standard (Wilson lower bound) recommends 5-10, but Klai's user base
# is too small to reach that in practice — chunks would never get boosted.
# 3 filters out accidental single clicks while still being reachable.
# Re-evaluate if MAU grows significantly.
_COLD_START_MIN_VOTES = 3

# Boost magnitude: max ±10% score adjustment.
# Validated against Vespa/Elasticsearch LTR research (0.1-0.2 range).
_BOOST_FACTOR = 0.2


def quality_boost(reranked: list[dict]) -> list[dict]:
    """Apply quality score boost to reranked results.

    Formula: boosted_score = score * (1 + 0.2 * (quality_score - 0.5))

    Only applied when feedback_count >= 3 (cold start guard).
    Missing quality_score defaults to 0.5 (neutral, no boost).
    Missing feedback_count defaults to 0 (no boost).

    Re-sorts results by boosted score descending.
    """
    if not reranked:
        return reranked

    for r in reranked:
        fc = r.get("feedback_count", 0)
        qs = r.get("quality_score", 0.5)
        if isinstance(fc, (int, float)) and fc >= _COLD_START_MIN_VOTES:
            r["score"] = r["score"] * (1 + _BOOST_FACTOR * (qs - 0.5))

    reranked.sort(key=lambda c: c["score"], reverse=True)
    return reranked
