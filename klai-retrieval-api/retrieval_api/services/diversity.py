"""Source-aware selection for retrieval pipeline.

SPEC-KB-021: Single post-rerank step that handles both source routing and
diversity.  Replaces the separate router (pre-search) + quota (post-rerank)
with one function that uses the actual reranker scores to decide.

Logic:
- If the query mentions a specific source → give that source all slots
- If scores are spread across sources → diversify (max N per source)
- Scores decide, not pre-computed centroids or label embeddings
"""

from __future__ import annotations

import re

import structlog

from retrieval_api.util.scores import ranking_score

logger = structlog.get_logger()

_UNKNOWN = "_unknown"

# Common words that appear in source labels but are too generic for matching.
# Shared with router.py (imported there).
STOP_WORDS: set[str] = {
    "help",
    "docs",
    "wiki",
    "info",
    "data",
    "page",
    "site",
    "team",
    "voor",
    "over",
    "alle",
    "deze",
    "onze",
    "meer",
    "door",
    "naar",
    "with",
    "from",
    "that",
    "this",
    "your",
    "about",
    "what",
    "will",
    "documentatie",
    "interne",
    "externe",
    "handleiding",
    "informatie",
    "helpcenter",
    "helpdesk",
    "support",
    "klant",
    "intern",
    "kennis",
}


def _detect_mentioned_sources(
    reranked: list[dict],
    query_resolved: str,
) -> set[str]:
    """Detect which source_labels are explicitly mentioned in the query.

    Splits each label on separators, filters stop words and short tokens,
    checks substring match in query.  Returns all matching labels.
    """
    query_lower = query_resolved.lower()
    mentioned: set[str] = set()

    seen: set[str] = set()
    for chunk in reranked:
        label = chunk.get("source_label") or _UNKNOWN
        if label in seen or label == _UNKNOWN or len(label) <= 3:
            continue
        seen.add(label)

        tokens = [
            t for t in re.split(r"[-./:]", label.lower()) if len(t) > 3 and t not in STOP_WORDS
        ]
        if any(token in query_lower for token in tokens):
            mentioned.add(label)

    return mentioned


def source_aware_select(
    reranked: list[dict],
    query_resolved: str,
    top_n: int = 5,
    max_per_source: int = 2,
    router_selected: set[str] | None = None,
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
            "mentioned_sources": [],
            "preference_applied": False,
            "preferred_labels": [],
            "boost": source_preference_boost,
            "pack_without_preference": [],
            "suppressed_count": 0,
            "max_score_inversion": 0.0,
        }

    # Step 1: detect preferred sources — from query keywords AND router decision
    keyword_mentioned = _detect_mentioned_sources(reranked, query_resolved)
    mentioned = set(keyword_mentioned)
    if router_selected:
        mentioned = mentioned | router_selected

    preference_applied = bool(
        mentioned and any(chunk.get("source_label") in mentioned for chunk in reranked)
    )

    def base_score(chunk: dict) -> float:
        return ranking_score(chunk, "reranker_score", "score")

    def preference_score(chunk: dict) -> float:
        score = base_score(chunk)
        if preference_applied and chunk.get("source_label") in mentioned:
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
        if earlier.get("source_label") not in mentioned:
            continue
        earlier_score = base_score(earlier)
        for later in ranked[index + 1 :]:
            if later.get("source_label") in mentioned:
                continue
            max_score_inversion = max(
                max_score_inversion,
                base_score(later) - earlier_score,
            )

    mode = "diversify"
    if preference_applied:
        mode = "router" if router_selected and not keyword_mentioned else "mentioned"
        if router_selected and keyword_mentioned:
            mode = "keyword+router"

    logger.debug(
        "source_aware_select",
        mode=mode,
        selected=len(selected),
        source_counts=per_source,
    )
    return selected, {
        "source_select_mode": mode,
        "source_counts": dict(per_source),
        "mentioned_sources": sorted(mentioned),
        "preference_applied": preference_applied,
        "preferred_labels": sorted(mentioned),
        "boost": source_preference_boost,
        "pack_without_preference": without_ids,
        "suppressed_count": suppressed_count,
        "max_score_inversion": max_score_inversion,
    }


def _count_sources(chunks: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for c in chunks:
        label = c.get("source_label") or _UNKNOWN
        counts[label] = counts.get(label, 0) + 1
    return counts
