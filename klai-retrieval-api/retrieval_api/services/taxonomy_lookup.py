"""Taxonomy tree and coverage helpers for query-time taxonomy filtering.

Fetches the KB taxonomy tree and coverage stats from the klai database via the
shared asyncpg pool (events.py pool). Results are cached in-process with TTLs:

  - Taxonomy tree: 60 s per (org_id, kb_slug)
  - Coverage ratio: 300 s per (org_id, kb_slug)

All functions are fail-open: on any DB error they return empty / 0.0 so
retrieval proceeds without taxonomy narrowing (SPEC-RAG-TAXONOMY-001 REQ-2).
"""

from __future__ import annotations

import time
from typing import TypedDict

import structlog

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# In-process TTL caches
# ---------------------------------------------------------------------------

# (org_id, kb_slug) -> (expires_at: float, value: list[dict])
_tree_cache: dict[tuple[str, str], tuple[float, list[dict]]] = {}
_TREE_TTL: float = 60.0  # seconds

# (org_id, kb_slug) -> (expires_at: float, coverage: float)
_coverage_cache: dict[tuple[str, str], tuple[float, float]] = {}
_COVERAGE_TTL: float = 300.0  # 5 minutes


class TaxonomyNode(TypedDict):
    id: int
    name: str
    parent_id: int | None
    depth: int


# ---------------------------------------------------------------------------
# SQL queries
# ---------------------------------------------------------------------------

# Returns the flat taxonomy tree for a KB.
# Tables: portal_orgs (for zitadel_org_id→id resolution), portal_kb_nodes
_TREE_SQL = """
    SELECT
        n.id,
        n.name,
        n.parent_id,
        n.depth
    FROM portal_kb_nodes n
    JOIN portal_kbs kb ON kb.id = n.kb_id
    JOIN portal_orgs org ON org.id = kb.org_id
    WHERE org.zitadel_org_id = $1
      AND kb.slug = $2
    ORDER BY n.depth, n.id
"""

# Coverage = fraction of chunks in the KB that have at least one taxonomy_node_id tagged.
# We proxy "tagged" chunks via `knowledge.artifacts` rows that have taxonomy_node_ids
# set. The query is intentionally simple and approximate.
_COVERAGE_SQL = """
    SELECT
        COUNT(*) FILTER (
            WHERE a.extra->'taxonomy_node_ids' IS NOT NULL
              AND jsonb_array_length(a.extra->'taxonomy_node_ids') > 0
        )::float
            / NULLIF(COUNT(*), 0) AS coverage_ratio
    FROM knowledge.artifacts a
    JOIN portal_kbs kb ON kb.slug = a.kb_slug
    JOIN portal_orgs org ON org.id = kb.org_id
    WHERE org.zitadel_org_id = $1
      AND a.kb_slug = $2
"""


async def get_taxonomy_tree(org_id: str, kb_slug: str) -> list[TaxonomyNode]:
    """Return the flat taxonomy node list for the KB, cached 60 s.

    Returns an empty list on any error (fail-open per REQ-2).
    """
    cache_key = (org_id, kb_slug)
    now = time.monotonic()

    entry = _tree_cache.get(cache_key)
    if entry is not None:
        expires_at, cached_tree = entry
        if now < expires_at:
            return cached_tree

    # Cache miss — fetch from DB
    try:
        from retrieval_api.services.events import _pool

        if _pool is None:
            logger.debug(
                "taxonomy_lookup_skipped",
                reason="no_db_pool",
                org_id=org_id,
                kb_slug=kb_slug,
            )
            return []

        rows = await _pool.fetch(_TREE_SQL, org_id, kb_slug)
        tree: list[TaxonomyNode] = [
            TaxonomyNode(
                id=row["id"],
                name=row["name"],
                parent_id=row["parent_id"],
                depth=row["depth"],
            )
            for row in rows
        ]
    except Exception:
        logger.warning(
            "taxonomy_tree_fetch_failed",
            org_id=org_id,
            kb_slug=kb_slug,
            exc_info=True,
        )
        return []

    _tree_cache[cache_key] = (now + _TREE_TTL, tree)
    return tree


async def get_kb_taxonomy_coverage(org_id: str, kb_slug: str) -> float:
    """Return the fraction of KB artifacts that have taxonomy tags, cached 5 min.

    Returns 0.0 on any error (fail-open — caller skips filter when coverage is low).
    """
    cache_key = (org_id, kb_slug)
    now = time.monotonic()

    entry = _coverage_cache.get(cache_key)
    if entry is not None:
        expires_at, cached_cov = entry
        if now < expires_at:
            return cached_cov

    try:
        from retrieval_api.services.events import _pool

        if _pool is None:
            return 0.0

        row = await _pool.fetchrow(_COVERAGE_SQL, org_id, kb_slug)
        coverage: float = (
            float(row["coverage_ratio"]) if row and row["coverage_ratio"] is not None else 0.0
        )
    except Exception:
        logger.warning(
            "taxonomy_coverage_fetch_failed",
            org_id=org_id,
            kb_slug=kb_slug,
            exc_info=True,
        )
        coverage = 0.0

    _coverage_cache[cache_key] = (now + _COVERAGE_TTL, coverage)
    return coverage
