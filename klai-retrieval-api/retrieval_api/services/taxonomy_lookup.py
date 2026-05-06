"""Taxonomy tree + coverage helpers for query-time taxonomy filtering.

Reads from the actual portal taxonomy schema:
  - ``portal_taxonomy_nodes`` (per-KB taxonomy tree, ``kb_id`` FK)
  - ``portal_knowledge_bases`` (slug + org_id resolution)
  - ``portal_orgs`` (zitadel_org_id → id resolution)

Multi-KB by design: the tree fetch accepts a list of slugs and returns
a single flat list with ``kb_slug`` annotated on each node. Taxonomy
node IDs are globally unique (single PK across all KBs of all tenants),
so the merged tree is collision-free — the LLM classifier can return
any subset of valid IDs and the retrieval-api ANY-of filter does the
rest at query time.

Coverage proxy: a KB has "taxonomy coverage" iff it has ≥ 1 node
defined. Binary signal — KBs with curated taxonomy → 1.0; KBs without
→ 0.0. Future v2 can refine to "fraction of chunks tagged" if the
binary signal proves too coarse.

All functions are fail-open: any DB error returns empty / zero so the
hook proceeds without taxonomy narrowing (SPEC-RAG-TAXONOMY-001 REQ-2).

Caching lives one layer up in the LiteLLM hook (Redis-backed,
cross-process) — this module only owns the DB lookup.
"""

from __future__ import annotations

from typing import TypedDict

import structlog

logger = structlog.get_logger()


class TaxonomyNode(TypedDict):
    id: int
    kb_slug: str
    name: str
    slug: str
    parent_id: int | None


# ---------------------------------------------------------------------------
# SQL queries
# ---------------------------------------------------------------------------

# Single-trip multi-KB tree fetch. Returns one row per node, annotated
# with the KB slug so callers can group by KB if needed for prompt
# disambiguation. ``ANY($2::text[])`` accepts a list of slugs.
_TREES_SQL = """
    SELECT
        n.id,
        kb.slug AS kb_slug,
        n.name,
        n.slug AS node_slug,
        n.parent_id
    FROM portal_taxonomy_nodes n
    JOIN portal_knowledge_bases kb ON kb.id = n.kb_id
    JOIN portal_orgs org ON org.id = kb.org_id
    WHERE org.zitadel_org_id = $1
      AND kb.slug = ANY($2::text[])
    ORDER BY kb.slug, n.parent_id NULLS FIRST, n.sort_order, n.id
"""

# Coverage = does the KB have taxonomy nodes defined? Binary signal.
# Single-trip multi-KB lookup so the hook can decide per-KB.
_COVERAGE_SQL = """
    SELECT
        kb.slug AS kb_slug,
        COUNT(n.id) AS node_count
    FROM portal_knowledge_bases kb
    JOIN portal_orgs org ON org.id = kb.org_id
    LEFT JOIN portal_taxonomy_nodes n ON n.kb_id = kb.id
    WHERE org.zitadel_org_id = $1
      AND kb.slug = ANY($2::text[])
    GROUP BY kb.slug
"""


async def get_taxonomy_trees(
    org_id: str,
    kb_slugs: list[str],
) -> dict[str, list[TaxonomyNode]]:
    """Return ``{kb_slug: [node, ...]}`` for the requested KBs.

    Empty input → empty dict. Missing KBs are simply absent from the
    result. Fail-open on any DB error: returns ``{}`` so the hook
    proceeds without taxonomy narrowing.
    """
    if not kb_slugs:
        return {}

    from retrieval_api.services.events import get_pool

    pool = get_pool()
    if pool is None:
        logger.debug("taxonomy_lookup_skipped", reason="no_db_pool", org_id=org_id)
        return {}

    try:
        rows = await pool.fetch(_TREES_SQL, org_id, list(kb_slugs))
    except Exception:
        # SPEC-SEC-HYGIENE-001 REQ-43.3 / TRY401: exc_info=True preserves the
        # traceback that error=str(exc)[:200] dropped. F6 audit cleanup.
        logger.warning(
            "taxonomy_tree_fetch_failed",
            org_id=org_id,
            kb_slugs=kb_slugs,
            exc_info=True,
        )
        return {}

    grouped: dict[str, list[TaxonomyNode]] = {}
    for row in rows:
        node: TaxonomyNode = {
            "id": int(row["id"]),
            "kb_slug": row["kb_slug"],
            "name": row["name"],
            "slug": row["node_slug"],
            "parent_id": int(row["parent_id"]) if row["parent_id"] is not None else None,
        }
        grouped.setdefault(row["kb_slug"], []).append(node)
    return grouped


async def get_kb_taxonomy_coverage(
    org_id: str,
    kb_slugs: list[str],
) -> dict[str, float]:
    """Return ``{kb_slug: coverage_ratio}`` for the requested KBs.

    v1: binary signal — 1.0 if the KB has ≥ 1 taxonomy node, 0.0 otherwise.
    Missing KBs default to 0.0. Fail-open on any DB error.
    """
    if not kb_slugs:
        return {}

    from retrieval_api.services.events import get_pool

    pool = get_pool()
    if pool is None:
        return {slug: 0.0 for slug in kb_slugs}

    try:
        rows = await pool.fetch(_COVERAGE_SQL, org_id, list(kb_slugs))
    except Exception:
        # SPEC-SEC-HYGIENE-001 REQ-43.3 / TRY401: exc_info=True preserves the
        # traceback that error=str(exc)[:200] dropped. F6 audit cleanup.
        logger.warning(
            "taxonomy_coverage_fetch_failed",
            org_id=org_id,
            kb_slugs=kb_slugs,
            exc_info=True,
        )
        return {slug: 0.0 for slug in kb_slugs}

    coverage: dict[str, float] = {slug: 0.0 for slug in kb_slugs}
    for row in rows:
        coverage[row["kb_slug"]] = 1.0 if int(row["node_count"]) > 0 else 0.0
    return coverage
