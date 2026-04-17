from __future__ import annotations

import time
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()


@dataclass
class KBEntry:
    """A knowledge base source with its label and description."""

    source_label: str
    name: str
    description: str | None = None


@dataclass
class RoutingDecision:
    """Result of the query router."""

    selected_source_labels: list[str] | None  # None = no filter (search all)
    layer_used: str  # "keyword" | "semantic" | "llm" | "none"
    margin: float | None = None
    cache_hit: bool = False


# Centroid cache: {org_id: (centroids_dict, timestamp)}
_centroid_cache: dict[str, tuple[dict[str, list[float]], float]] = {}


def _build_keyword_map(catalog: list[KBEntry]) -> dict[str, str]:
    """Build {term -> source_label} map from catalog entries.

    Splits each source_label and name on common separators (-./: and space),
    keeps tokens with len > 3, maps each token to its source_label.
    """
    keyword_map: dict[str, str] = {}
    for entry in catalog:
        # Split source_label on separators
        tokens: set[str] = set()
        for sep_char in "-./: ":
            for part in entry.source_label.split(sep_char):
                if len(part) > 3:
                    tokens.add(part.lower())
        # Also split name
        if entry.name:
            for word in entry.name.lower().split():
                if len(word) > 3:
                    tokens.add(word)
        # Also split description words
        if entry.description:
            for word in entry.description.lower().split():
                if len(word) > 3:
                    tokens.add(word)

        for token in tokens:
            keyword_map[token] = entry.source_label
    return keyword_map


def layer1_keyword(query_resolved: str, keyword_map: dict[str, str]) -> list[str] | None:
    """Layer 1: exact keyword matching. Returns matched source_labels or None."""
    query_lower = query_resolved.lower()
    matched: set[str] = set()
    for term, source_label in keyword_map.items():
        if term in query_lower:
            matched.add(source_label)
    return list(matched) if matched else None


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def layer2_semantic(
    query_vector: list[float],
    centroids: dict[str, list[float]],
    margin_single: float = 0.15,
    margin_dual: float = 0.08,
) -> tuple[list[str] | None, float | None]:
    """Layer 2: semantic margin matching.

    Returns (selected_source_labels, margin).
    - margin > margin_single → single source
    - margin > margin_dual → dual sources
    - else → None (no filter)
    """
    if not centroids:
        return None, None

    similarities = [
        (label, _cosine_similarity(query_vector, centroid)) for label, centroid in centroids.items()
    ]
    similarities.sort(key=lambda x: x[1], reverse=True)

    if len(similarities) < 2:
        top_label, top_sim = similarities[0]
        return [top_label], top_sim

    top1_label, top1_sim = similarities[0]
    top2_label, top2_sim = similarities[1]
    margin = top1_sim - top2_sim

    if margin > margin_single:
        return [top1_label], margin
    elif margin > margin_dual:
        return [top1_label, top2_label], margin
    else:
        return None, margin


async def route_to_sources(
    query_resolved: str,
    query_vector: list[float],
    org_id: str,
    source_label_catalog: list[KBEntry],
    *,
    margin_single: float = 0.15,
    margin_dual: float = 0.08,
    llm_fallback: bool = False,
    centroid_ttl_seconds: int = 600,
    # Centroid computation function injected for testability
    compute_centroid_fn=None,
    # LLM function injected for testability
    llm_fn=None,
) -> RoutingDecision:
    """Three-layer query router.

    Layer 1: Keyword matching (< 1ms)
    Layer 2: Semantic margin (5-20ms with cache)
    Layer 3: LLM fallback (500ms timeout, default OFF)
    """
    # Layer 1: keyword
    keyword_map = _build_keyword_map(source_label_catalog)
    matched = layer1_keyword(query_resolved, keyword_map)
    if matched:
        return RoutingDecision(
            selected_source_labels=matched,
            layer_used="keyword",
        )

    # Layer 2: semantic margin
    # Check centroid cache
    cache_hit = False
    centroids: dict[str, list[float]] | None = None
    cached = _centroid_cache.get(org_id)
    if cached and (time.time() - cached[1]) < centroid_ttl_seconds:
        centroids = cached[0]
        cache_hit = True

    if centroids is None and compute_centroid_fn:
        centroids = await compute_centroid_fn(source_label_catalog)
        _centroid_cache[org_id] = (centroids, time.time())

    if centroids:
        selected, margin = layer2_semantic(query_vector, centroids, margin_single, margin_dual)
        if selected:
            return RoutingDecision(
                selected_source_labels=selected,
                layer_used="semantic",
                margin=margin,
                cache_hit=cache_hit,
            )

    # Layer 3: LLM fallback (default OFF)
    if llm_fallback and llm_fn:
        try:
            import asyncio

            result = await asyncio.wait_for(
                llm_fn(query_resolved, source_label_catalog),
                timeout=0.5,  # 500ms hard timeout
            )
            if result:
                return RoutingDecision(
                    selected_source_labels=result,
                    layer_used="llm",
                    cache_hit=cache_hit,
                )
        except Exception:
            logger.warning("router_llm_fallback_failed", org_id=org_id)

    # No match from any layer — compute final margin for logging if possible
    final_margin: float | None = None
    if centroids:
        _, final_margin = layer2_semantic(query_vector, centroids, margin_single, margin_dual)

    return RoutingDecision(
        selected_source_labels=None,
        layer_used="none",
        margin=final_margin,
        cache_hit=cache_hit,
    )


def clear_centroid_cache(org_id: str | None = None) -> None:
    """Clear centroid cache. For testing and cache invalidation."""
    if org_id:
        _centroid_cache.pop(org_id, None)
    else:
        _centroid_cache.clear()
