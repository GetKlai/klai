"""Source-aware selection for retrieval pipeline.

SPEC-KB-021: Single post-rerank step that handles both source routing and
diversity.  Replaces the separate router (pre-search) + quota (post-rerank)
with one function that uses the actual reranker scores to decide.

Logic:
- Semantic router labels receive a bounded additive score boost
- Scores remain authoritative outside that bounded tiebreak
- Selected chunks are diversified with a per-source cap
"""

from __future__ import annotations

import structlog

from retrieval_api.util.scores import ranking_score

logger = structlog.get_logger()

_UNKNOWN = "_unknown"


def source_aware_select(
    reranked: list[dict],
    top_n: int = 5,
    max_per_source: int = 2,
    preferred_labels: set[str] | None = None,
    preferred_kb_slugs: set[str] | None = None,
    excluded_preferred_kb_slugs: set[str] | None = None,
    source_preference_boost: float = 0.05,
) -> tuple[list[dict], dict]:
    """Select top-N chunks with source-aware diversity.

    Uses reranker scores + optional router signal for source selection.

    Preferred sources receive a bounded additive ranking boost. Selection then
    uses the same per-source diversity cap for every source.

    Returns:
        (selected_chunks, metadata_dict)
    """
    if not reranked:
        return [], {
            "source_select_mode": "empty",
            "source_counts": {},
            "preference_applied": False,
            "preferred_labels": [],
            "boost": source_preference_boost,
            "pack_without_preference": [],
            "suppressed_count": 0,
            "max_score_inversion": 0.0,
        }

    preferred = set(preferred_labels or ())
    preferred_slugs = set(preferred_kb_slugs or ())
    excluded_preferred_slugs = set(excluded_preferred_kb_slugs or ())

    def is_preferred(chunk: dict) -> bool:
        if chunk.get("source_label") not in preferred:
            return False
        if chunk.get("kb_slug") in excluded_preferred_slugs:
            return False
        return not preferred_slugs or chunk.get("kb_slug") in preferred_slugs

    preference_applied = bool(preferred and any(is_preferred(chunk) for chunk in reranked))

    def base_score(chunk: dict) -> float:
        return ranking_score(chunk, "reranker_score", "score")

    def preference_score(chunk: dict) -> float:
        score = base_score(chunk)
        if preference_applied and is_preferred(chunk):
            return score + source_preference_boost
        return score

    base_ranked = sorted(reranked, key=base_score, reverse=True)
    ranked = sorted(reranked, key=preference_score, reverse=True)

    def select_diverse(
        ranked_chunks: list[dict],
        score_fn,
    ) -> tuple[list[dict], dict[str, int]]:
        per_source: dict[str, int] = {}
        selected: list[dict] = []
        leftover: list[dict] = []

        for chunk in ranked_chunks:
            if len(selected) == top_n:
                break
            label = chunk.get("source_label") or _UNKNOWN
            count = per_source.get(label, 0)
            if count < max_per_source:
                selected.append(chunk)
                per_source[label] = count + 1
            else:
                leftover.append(chunk)

        if len(selected) < top_n:
            for chunk in leftover:
                if len(selected) == top_n:
                    break
                selected.append(chunk)
                label = chunk.get("source_label") or _UNKNOWN
                per_source[label] = per_source.get(label, 0) + 1

        selected.sort(key=score_fn, reverse=True)
        return selected, per_source

    selected_without_preference, _ = select_diverse(base_ranked, base_score)
    selected, per_source = select_diverse(ranked, preference_score)

    without_ids = [str(chunk.get("chunk_id") or "") for chunk in selected_without_preference]
    selected_ids = {str(chunk.get("chunk_id") or "") for chunk in selected}
    suppressed_count = len(set(without_ids) - selected_ids)

    max_score_inversion = 0.0
    for index, earlier in enumerate(ranked):
        if not is_preferred(earlier):
            continue
        earlier_score = base_score(earlier)
        for later in ranked[index + 1 :]:
            if is_preferred(later):
                continue
            max_score_inversion = max(
                max_score_inversion,
                base_score(later) - earlier_score,
            )

    mode = "router" if preference_applied else "diversify"

    logger.debug(
        "source_aware_select",
        mode=mode,
        selected=len(selected),
        source_counts=per_source,
    )
    return selected, {
        "source_select_mode": mode,
        "source_counts": dict(per_source),
        "preference_applied": preference_applied,
        "preferred_labels": sorted(preferred),
        "boost": source_preference_boost,
        "pack_without_preference": without_ids,
        "suppressed_count": suppressed_count,
        "max_score_inversion": max_score_inversion,
    }
