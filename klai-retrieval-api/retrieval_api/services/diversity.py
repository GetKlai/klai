"""Source diversity / quota selection for retrieval pipeline.

SPEC-KB-021 Change 2: Apply per-source_label quota to reranked chunks so that
no single knowledge base dominates the top-N results — unless the query
explicitly mentions that source.
"""

from __future__ import annotations

import re

import structlog

logger = structlog.get_logger()

_UNKNOWN = "_unknown"


def source_quota_select(
    reranked: list[dict],
    query_resolved: str,
    top_n: int = 5,
    max_per_source: int = 2,
    bypass_on_mention: bool = True,
) -> tuple[list[dict], dict]:
    """Apply per-source_label quota to reranked chunks.

    Algorithm:
    1. Determine which source_labels are bypassed (query substring match,
       label length > 3, bypass_on_mention=True).  Only the FIRST matching
       label is bypassed — subsequent labels still obey the quota.
    2. Greedy pass over reranked (already sorted by score desc):
       - Include if bypassed OR per_source_count < max_per_source.
       - Otherwise push to leftover list.
       - Stop when len(selected) == top_n.
    3. Fallback: fill remaining slots from leftover in original score order.

    Returns:
        (selected_chunks, metadata)

    metadata keys:
        quota_applied          bool
        quota_per_source_counts dict[str, int]
        quota_bypass_reason    str | None
        quota_bypass_source_label str | None
    """
    # -- Step 1: Determine bypassed source_label ---------------------------------
    bypass_source_label: str | None = None
    bypass_reason: str | None = None

    if bypass_on_mention:
        query_lower = query_resolved.lower()
        # Collect unique source labels from results (preserving score order)
        unique_labels: list[str] = []
        seen: set[str] = set()
        for chunk in reranked:
            label = chunk.get("source_label") or _UNKNOWN
            if label not in seen:
                seen.add(label)
                unique_labels.append(label)

        for label in unique_labels:
            if label == _UNKNOWN:
                continue
            if len(label) <= 3:
                # Too short — substring match would cause false positives
                continue
            # Split label into tokens (split on -./:) and check if any token
            # with len > 3 appears as a substring in the query.
            # Example: "mitel-help" → tokens ["mitel", "help"]
            #          "mitel" appears in "mitel error X025 oplossen" → bypass
            tokens = [t for t in re.split(r"[-./:]", label.lower()) if len(t) > 3]
            if any(token in query_lower for token in tokens):
                bypass_source_label = label
                bypass_reason = "query_mention"
                break

    # -- Step 2: Greedy select ---------------------------------------------------
    per_source_count: dict[str, int] = {}
    selected: list[dict] = []
    leftover: list[dict] = []

    for chunk in reranked:
        if len(selected) == top_n:
            break
        label = chunk.get("source_label") or _UNKNOWN
        count = per_source_count.get(label, 0)

        if label == bypass_source_label:
            selected.append(chunk)
            per_source_count[label] = count + 1
        elif count < max_per_source:
            selected.append(chunk)
            per_source_count[label] = count + 1
        else:
            leftover.append(chunk)

    # -- Step 3: Fallback fill ---------------------------------------------------
    # When quota was too strict and we still have unfilled slots, fill from
    # leftover in original score order (already sorted desc by position in
    # reranked). This happens when there are fewer unique sources than top_n.
    if len(selected) < top_n and leftover:
        for chunk in leftover:
            if len(selected) == top_n:
                break
            selected.append(chunk)
            label = chunk.get("source_label") or _UNKNOWN
            per_source_count[label] = per_source_count.get(label, 0) + 1

    # Keep in descending score order (greedy pass preserves order for selected,
    # but fallback may insert lower-scored chunks out of position).
    selected.sort(key=lambda x: x.get("score", 0.0), reverse=True)

    logger.debug(
        "source_quota_select",
        top_n=top_n,
        selected=len(selected),
        bypass_source_label=bypass_source_label,
        per_source_counts=per_source_count,
    )

    metadata: dict = {
        "quota_applied": True,
        "quota_per_source_counts": dict(per_source_count),
        "quota_bypass_reason": bypass_reason,
        "quota_bypass_source_label": bypass_source_label,
    }
    return selected, metadata
