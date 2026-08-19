from __future__ import annotations

import time
from dataclasses import dataclass

import structlog

from retrieval_api.config import settings

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Source catalog cache: {(org_id, kb_slug_scope): (catalog, timestamp)}
# ---------------------------------------------------------------------------
_catalog_cache: dict[tuple[str, tuple[str, ...] | None], tuple[list[KBEntry], float]] = {}


async def fetch_source_catalog(org_id: str, kb_slugs: list[str] | None = None) -> list[KBEntry]:
    """Fetch distinct source_labels for an org from Qdrant via the Facet API.

    Single call — returns unique source_label values with counts.
    Requires a keyword index on source_label (created by ensure_collection).
    When the request pins knowledge bases, the catalog contains only source
    labels from those KBs. Cached per org and KB scope.
    """
    kb_scope = tuple(sorted(set(kb_slugs))) if kb_slugs else None
    cache_key = (org_id, kb_scope)
    cached = _catalog_cache.get(cache_key)
    if cached and (time.monotonic() - cached[1]) < settings.router_centroid_ttl_seconds:
        return cached[0]

    from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

    from retrieval_api.services.search import _get_client

    client = _get_client()
    must = [FieldCondition(key="org_id", match=MatchValue(value=org_id))]
    if kb_scope:
        must.append(FieldCondition(key="kb_slug", match=MatchAny(any=list(kb_scope))))
    facet_filter = Filter(must=must)

    try:
        result = await client.facet(
            collection_name=settings.qdrant_collection,
            key="source_label",
            facet_filter=facet_filter,
            limit=50,
            exact=True,
        )
        entries = [
            KBEntry(source_label=hit.value, name=hit.value)
            for hit in result.hits
            if hit.value  # skip empty/null labels
        ]
    except Exception:
        # SPEC-SEC-HYGIENE-001 REQ-43.3: exc_info=True preserves the
        # traceback that the previous `error=str(exc)` dropped (TRY401).
        logger.warning("router_facet_failed", org_id=org_id, exc_info=True)
        entries = []

    _catalog_cache[cache_key] = (entries, time.monotonic())
    logger.info(
        "router_catalog_built",
        org_id=org_id,
        kb_slugs=list(kb_scope) if kb_scope else None,
        source_labels=len(entries),
    )
    return entries


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
    layer_used: str  # "semantic" | "llm" | "none"
    margin: float | None = None
    cache_hit: bool = False


# Centroid cache: {(org_id, kb_slug_scope, catalog_labels): (centroids_dict, timestamp)}
_centroid_cache: dict[
    tuple[str, tuple[str, ...] | None, tuple[str, ...]],
    tuple[dict[str, list[float]], float],
] = {}


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


async def _default_compute_centroids(
    catalog: list[KBEntry],
    org_id: str,
    kb_slugs: list[str] | None = None,
) -> dict[str, list[float]]:
    """Compute centroids from actual chunk vectors per source_label in Qdrant.

    For each source_label, fetches a small sample of chunks and averages
    their dense vectors.  This produces a content-based centroid that
    represents what a source actually contains — not just what it's called.

    # audit-tenant-isolation-2026-05-05 finding B-1: filter MUST include org_id
    # to prevent cross-tenant centroid contamination from common source_labels
    # (Notion, Confluence, GitHub, Slack, Web).
    """
    from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

    from retrieval_api.services.search import _get_client

    client = _get_client()
    centroids: dict[str, list[float]] = {}
    kb_scope = sorted(set(kb_slugs)) if kb_slugs else None

    for entry in catalog:
        try:
            # Scroll a small sample of chunks for this source_label.
            # audit-tenant-isolation-2026-05-05 finding B-1: filter MUST include org_id
            # to prevent cross-tenant centroid contamination from common source_labels
            # (Notion, Confluence, GitHub, Slack, Web).
            must = [
                FieldCondition(key="source_label", match=MatchValue(value=entry.source_label)),
                FieldCondition(key="org_id", match=MatchValue(value=org_id)),
            ]
            if kb_scope:
                must.append(FieldCondition(key="kb_slug", match=MatchAny(any=kb_scope)))
            points, _ = await client.scroll(
                collection_name=settings.qdrant_collection,
                scroll_filter=Filter(must=must),
                limit=10,
                with_payload=False,
                with_vectors=["vector_chunk"],
            )
            if not points:
                continue

            # Average the dense vectors to create a content-based centroid
            vecs = [
                p.vector["vector_chunk"] for p in points if p.vector and "vector_chunk" in p.vector
            ]
            if not vecs:
                continue

            dim = len(vecs[0])
            avg = [sum(v[i] for v in vecs) / len(vecs) for i in range(dim)]
            centroids[entry.source_label] = avg
        except Exception:
            logger.warning("centroid_compute_failed", source_label=entry.source_label)

    return centroids


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
    kb_slugs: list[str] | None = None,
    # Centroid computation function injected for testability
    compute_centroid_fn=None,
    # LLM function injected for testability
    llm_fn=None,
) -> RoutingDecision:
    """Two-layer query router.

    Layer 1: Semantic margin (5-20ms with cache)
    Layer 2: LLM fallback (500ms timeout, default OFF)
    """
    # Layer 1: semantic margin
    # Check centroid cache
    cache_hit = False
    centroids: dict[str, list[float]] | None = None
    kb_scope = tuple(sorted(set(kb_slugs))) if kb_slugs else None
    catalog_scope = tuple(sorted(entry.source_label for entry in source_label_catalog))
    cache_key = (org_id, kb_scope, catalog_scope)
    cached = _centroid_cache.get(cache_key)
    if cached and (time.monotonic() - cached[1]) < centroid_ttl_seconds:
        centroids = cached[0]
        cache_hit = True

    if centroids is None:
        if compute_centroid_fn:
            centroids = await compute_centroid_fn(source_label_catalog, org_id)
        else:
            centroids = await _default_compute_centroids(source_label_catalog, org_id, kb_slugs)
        _centroid_cache[cache_key] = (centroids, time.monotonic())

    if centroids:
        selected, margin = layer2_semantic(query_vector, centroids, margin_single, margin_dual)
        if selected:
            return RoutingDecision(
                selected_source_labels=selected,
                layer_used="semantic",
                margin=margin,
                cache_hit=cache_hit,
            )

    # Layer 2: LLM fallback (default OFF)
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
        for cache_key in [key for key in _centroid_cache if key[0] == org_id]:
            _centroid_cache.pop(cache_key, None)
    else:
        _centroid_cache.clear()
