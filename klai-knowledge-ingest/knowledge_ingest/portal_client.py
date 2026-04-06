"""
Portal API client for taxonomy operations.

- fetch_taxonomy_nodes: cached (5 min) per (org_id, kb_slug)
- submit_taxonomy_proposal: POST proposal to portal review queue

Missing PORTAL_INTERNAL_TOKEN → returns empty list / skips submission with warning.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx
import structlog

from knowledge_ingest.config import settings
from knowledge_ingest.taxonomy_classifier import TaxonomyNode

logger = structlog.get_logger()

# 5-minute in-memory cache: key = (org_id, kb_slug), value = (timestamp, nodes)
_taxonomy_cache: dict[tuple[str, str], tuple[float, list[TaxonomyNode]]] = {}
_CACHE_TTL = 300.0  # 5 minutes


@dataclass
class TaxonomyProposal:
    proposal_type: str  # "new_node"
    suggested_name: str
    document_count: int
    sample_titles: list[str]
    description: str = ""


async def fetch_taxonomy_nodes(kb_slug: str, org_id: str) -> list[TaxonomyNode]:
    """Fetch taxonomy nodes for a KB from the portal.

    Result is cached for 5 minutes per (org_id, kb_slug).
    Returns empty list when PORTAL_INTERNAL_TOKEN is not configured or portal is unreachable.
    """
    if not settings.portal_internal_token:
        logger.warning("taxonomy_nodes_skipped", reason="missing PORTAL_INTERNAL_TOKEN")
        return []

    cache_key = (org_id, kb_slug)
    cached = _taxonomy_cache.get(cache_key)
    if cached is not None:
        ts, nodes = cached
        if time.monotonic() - ts < _CACHE_TTL:
            return nodes

    try:
        nodes = await asyncio.wait_for(
            _fetch_from_portal(kb_slug, org_id),
            timeout=3.0,
        )
    except (TimeoutError, Exception) as exc:
        logger.warning(
            "taxonomy_nodes_fetch_failed",
            kb_slug=kb_slug,
            org_id=org_id,
            error=str(exc),
        )
        # Return cached stale data if available, else empty list
        if cached is not None:
            return cached[1]
        return []

    _taxonomy_cache[cache_key] = (time.monotonic(), nodes)
    return nodes


async def _fetch_from_portal(kb_slug: str, org_id: str) -> list[TaxonomyNode]:
    async with httpx.AsyncClient(timeout=3.0) as client:
        resp = await client.get(
            f"{settings.portal_url}/api/app/knowledge-bases/{kb_slug}/taxonomy/nodes/internal",
            headers={"Authorization": f"Bearer {settings.portal_internal_token}"},
            params={"zitadel_org_id": org_id},
        )
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        data = resp.json()
        items = data["nodes"] if isinstance(data, dict) else data
        return [
            TaxonomyNode(id=item["id"], name=item["name"], description=item.get("description"))
            for item in items
            if isinstance(item.get("id"), int) and item.get("name")
        ]


async def submit_taxonomy_proposal(
    kb_slug: str,
    org_id: str,
    proposal: TaxonomyProposal,
) -> None:
    """Submit a taxonomy proposal to the portal review queue.

    Silently skips if PORTAL_INTERNAL_TOKEN is not set (logs warning).
    Silently skips on portal errors (logs warning, does not fail ingest).
    """
    if not settings.portal_internal_token:
        logger.warning(
            "taxonomy_proposal_skipped",
            reason="missing PORTAL_INTERNAL_TOKEN",
            kb_slug=kb_slug,
        )
        return

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{settings.portal_url}/api/app/knowledge-bases/{kb_slug}/taxonomy/proposals",
                headers={"Authorization": f"Bearer {settings.portal_internal_token}"},
                params={"zitadel_org_id": org_id},
                json={
                    "proposal_type": proposal.proposal_type,
                    "title": proposal.suggested_name,
                    "payload": {
                        "suggested_name": proposal.suggested_name,
                        "document_count": proposal.document_count,
                        "sample_titles": proposal.sample_titles[:5],
                        "description": proposal.description,
                    },
                },
            )
            if resp.status_code not in (200, 201):
                logger.warning(
                    "taxonomy_proposal_submit_failed",
                    status=resp.status_code,
                    kb_slug=kb_slug,
                )
    except Exception as exc:
        logger.warning(
            "taxonomy_proposal_submit_error",
            kb_slug=kb_slug,
            error=str(exc),
        )


def invalidate_cache(org_id: str, kb_slug: str) -> None:
    """Remove cached taxonomy nodes for a KB (useful in tests)."""
    _taxonomy_cache.pop((org_id, kb_slug), None)
