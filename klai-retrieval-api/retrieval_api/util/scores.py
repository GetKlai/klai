"""Single owner of the post-rerank ranking-score precedence.

SPEC-RAG-EVIDENCE-INTEGRITY-001 REQ-RANK-01: after the reranker there is ONE
ranking truth — ``final_rank_score``. The field is only present when the
ranking contract is active (``_set_final_rank_scores`` in retrieve.py); in
shadow mode it is absent so every pipeline stage falls back to its
pre-contract sort key and serving stays byte-identical to the old behavior.

# @MX:ANCHOR: [AUTO] Every post-rerank sort in the pipeline (diversity,
# quality_boost, page-context boost, link-expand boost, shadow preview) MUST
# key through this helper. Divergent per-module fallback chains were the
# root cause of the RRF/reranker order scramble this SPEC fixes.
# @MX:REASON: SPEC-RAG-EVIDENCE-INTEGRITY-001 REQ-RANK-01 — one ranking truth.
"""

from __future__ import annotations


def ranking_score(chunk: dict, *fallback_keys: str) -> float:
    """Return ``final_rank_score`` when present, else the first numeric fallback.

    ``fallback_keys`` is each call site's PRE-contract sort key chain (e.g.
    ``("score",)`` for the diversity sort, ``("reranker_score", "score")``
    for the boost re-sorts) so shadow mode reproduces the legacy ordering
    exactly. isinstance-based on purpose: a legitimate ``0.0`` score must
    sort as 0.0, not fall through like the old ``or``-chains did.
    """
    value = chunk.get("final_rank_score")
    if isinstance(value, (int, float)):
        return float(value)
    for key in fallback_keys:
        value = chunk.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0
