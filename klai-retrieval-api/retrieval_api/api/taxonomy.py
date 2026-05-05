"""Internal taxonomy endpoints for the LiteLLM hook.

GET /internal/v1/taxonomy/trees      — multi-KB taxonomy node list
GET /internal/v1/taxonomy/coverage   — multi-KB coverage ratio map

Both endpoints accept ``kb_slugs`` as a repeated query parameter:
  /internal/v1/taxonomy/trees?org_id=…&kb_slugs=support&kb_slugs=billing

Auth: same X-Internal-Secret as /retrieve (enforced by AuthMiddleware globally).

Note: the legacy single-KB ``/tree`` and ``/coverage`` endpoints are
retained as thin wrappers around the multi-KB helpers for any caller
that hasn't migrated yet.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Query

from retrieval_api.services.taxonomy_lookup import (
    get_kb_taxonomy_coverage,
    get_taxonomy_trees,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/internal/v1/taxonomy")

# Module-level Query singletons — avoid ruff B008 ("function call in default
# argument") while keeping FastAPI's parameter-source declaration centralised.
_ORG_ID_Q = Query(..., description="Zitadel org ID")
_KB_SLUGS_Q = Query(..., description="One or more KB slugs")
_KB_SLUG_Q = Query(..., description="KB slug")


@router.get("/trees")
async def taxonomy_trees(
    org_id: str = _ORG_ID_Q,
    kb_slugs: list[str] = _KB_SLUGS_Q,
) -> dict[str, list[dict]]:
    """Return ``{kb_slug: [node, ...]}`` for the requested KBs.

    Empty mapping when no KB has taxonomy or on any DB error (fail-open).
    """
    grouped = await get_taxonomy_trees(org_id, kb_slugs)
    return {slug: [dict(n) for n in nodes] for slug, nodes in grouped.items()}


@router.get("/coverage")
async def taxonomy_coverage(
    org_id: str = _ORG_ID_Q,
    kb_slugs: list[str] = _KB_SLUGS_Q,
) -> dict[str, float]:
    """Return ``{kb_slug: coverage_ratio}`` for the requested KBs."""
    return await get_kb_taxonomy_coverage(org_id, kb_slugs)


# ---------------------------------------------------------------------------
# Legacy single-KB endpoints (kept for backward compat with any in-flight
# clients; thin wrappers around the multi-KB helpers).
# ---------------------------------------------------------------------------


@router.get("/tree")
async def taxonomy_tree_legacy(
    org_id: str = _ORG_ID_Q,
    kb_slug: str = _KB_SLUG_Q,
) -> list[dict]:
    grouped = await get_taxonomy_trees(org_id, [kb_slug])
    return [dict(n) for n in grouped.get(kb_slug, [])]


@router.get("/coverage-single")
async def taxonomy_coverage_legacy(
    org_id: str = _ORG_ID_Q,
    kb_slug: str = _KB_SLUG_Q,
) -> dict:
    cov = await get_kb_taxonomy_coverage(org_id, [kb_slug])
    return {"coverage": cov.get(kb_slug, 0.0)}
