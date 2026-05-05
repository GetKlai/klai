"""Internal taxonomy endpoints for the LiteLLM hook.

GET /internal/v1/taxonomy/tree   — taxonomy node list for a KB
GET /internal/v1/taxonomy/coverage — coverage ratio for a KB

Auth: same X-Internal-Secret as /retrieve (enforced by AuthMiddleware globally).
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Query

from retrieval_api.services.taxonomy_lookup import (
    get_kb_taxonomy_coverage,
    get_taxonomy_tree,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/internal/v1/taxonomy")


@router.get("/tree")
async def taxonomy_tree(
    org_id: str = Query(..., description="Zitadel org ID"),
    kb_slug: str = Query(..., description="KB slug"),
) -> list[dict]:
    """Return the flat taxonomy node list for the KB.

    Returns [] when the KB has no taxonomy or on any DB error (fail-open).
    """
    tree = await get_taxonomy_tree(org_id, kb_slug)
    # Convert TypedDicts to plain dicts for JSON serialization
    return [dict(node) for node in tree]


@router.get("/coverage")
async def taxonomy_coverage(
    org_id: str = Query(..., description="Zitadel org ID"),
    kb_slug: str = Query(..., description="KB slug"),
) -> dict:
    """Return the taxonomy tagging coverage ratio for the KB.

    Returns {"coverage": 0.0} on any DB error (fail-open).
    """
    coverage = await get_kb_taxonomy_coverage(org_id, kb_slug)
    return {"coverage": coverage}
