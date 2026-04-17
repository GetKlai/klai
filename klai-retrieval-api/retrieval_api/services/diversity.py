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

logger = structlog.get_logger()

_UNKNOWN = "_unknown"

# Common words that appear in source labels but are too generic for matching.
_STOP_WORDS: set[str] = {
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
            t for t in re.split(r"[-./:]", label.lower()) if len(t) > 3 and t not in _STOP_WORDS
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
) -> tuple[list[dict], dict]:
    """Select top-N chunks with source-aware diversity.

    Uses reranker scores + optional router signal for source selection.

    Behaviour:
    1. If query mentions specific source(s): those sources get all slots.
       The reranker already scored them highest if they're relevant.
    2. Otherwise: greedy select with per-source cap, fallback fill if needed.

    Returns:
        (selected_chunks, metadata_dict)
    """
    if not reranked:
        return [], {
            "source_select_mode": "empty",
            "source_counts": {},
            "mentioned_sources": [],
        }

    # Step 1: detect relevant sources — from query keywords AND router decision
    mentioned = _detect_mentioned_sources(reranked, query_resolved)
    if router_selected:
        mentioned = mentioned | router_selected

    if mentioned:
        # Query is source-specific → give mentioned sources all slots.
        # Still sorted by reranker score (reranked is already score-desc).
        from_mentioned = [c for c in reranked if c.get("source_label") in mentioned]
        selected = from_mentioned[:top_n]

        # If mentioned sources don't fill top_n, add others
        if len(selected) < top_n:
            others = [c for c in reranked if c.get("source_label") not in mentioned]
            selected.extend(others[: top_n - len(selected)])

        counts = _count_sources(selected)
        logger.debug(
            "source_aware_select",
            mode="mentioned",
            mentioned=sorted(mentioned),
            selected=len(selected),
            source_counts=counts,
        )
        # Distinguish keyword-only, router-only, or both
        keyword_mentioned = _detect_mentioned_sources(reranked, query_resolved)
        mode = "mentioned"
        if router_selected and keyword_mentioned:
            mode = "keyword+router"
        elif router_selected:
            mode = "router"

        return selected, {
            "source_select_mode": mode,
            "source_counts": counts,
            "mentioned_sources": sorted(mentioned),
        }

    # Step 2: no specific source mentioned → diversify with per-source cap
    per_source: dict[str, int] = {}
    selected: list[dict] = []
    leftover: list[dict] = []

    for chunk in reranked:
        if len(selected) == top_n:
            break
        label = chunk.get("source_label") or _UNKNOWN
        count = per_source.get(label, 0)
        if count < max_per_source:
            selected.append(chunk)
            per_source[label] = count + 1
        else:
            leftover.append(chunk)

    # Fallback: fill remaining slots from leftover in score order
    if len(selected) < top_n:
        for chunk in leftover:
            if len(selected) == top_n:
                break
            selected.append(chunk)
            label = chunk.get("source_label") or _UNKNOWN
            per_source[label] = per_source.get(label, 0) + 1

    selected.sort(key=lambda x: x.get("score", 0.0), reverse=True)

    logger.debug(
        "source_aware_select",
        mode="diversify",
        selected=len(selected),
        source_counts=per_source,
    )
    return selected, {
        "source_select_mode": "diversify",
        "source_counts": dict(per_source),
        "mentioned_sources": [],
    }


def _count_sources(chunks: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for c in chunks:
        label = c.get("source_label") or _UNKNOWN
        counts[label] = counts.get(label, 0) + 1
    return counts
