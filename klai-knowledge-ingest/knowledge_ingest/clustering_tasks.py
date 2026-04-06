"""
Procrastinate periodic task for taxonomy clustering (SPEC-KB-024 R1).

Runs as a Procrastinate task on the taxonomy-backfill queue.
For each KB with >= 10 documents: runs HDBSCAN clustering, saves centroids,
generates proposals for unmatched clusters.
"""

from __future__ import annotations

import asyncio
import warnings
from typing import Any

import structlog

from knowledge_ingest.config import settings

warnings.filterwarnings("ignore", message="Api key is used with an insecure connection")

logger = structlog.get_logger()


def register_clustering_tasks(procrastinate_app: Any) -> None:
    """Register clustering tasks on the Procrastinate app.

    Called from enrichment_tasks.init_app().
    """
    import procrastinate

    @procrastinate_app.task(
        queue="taxonomy-backfill",
        retry=procrastinate.RetryStrategy(max_attempts=1),
    )
    async def run_taxonomy_clustering(
        org_id: str,
        kb_slug: str,
    ) -> dict:
        """Run HDBSCAN clustering for a single KB."""
        result = await _run_clustering(org_id=org_id, kb_slug=kb_slug)
        return result

    procrastinate_app.run_taxonomy_clustering = run_taxonomy_clustering  # type: ignore[attr-defined]


async def _run_clustering(org_id: str, kb_slug: str) -> dict:
    """Core clustering logic for a single KB.

    Fetches embeddings from Qdrant, runs HDBSCAN, saves centroids,
    generates proposals for unmatched clusters.
    """
    from qdrant_client import AsyncQdrantClient

    from knowledge_ingest.clustering import run_clustering_for_kb
    from knowledge_ingest.portal_client import fetch_taxonomy_nodes

    logger.info("clustering_job_started", org_id=org_id, kb_slug=kb_slug)

    client = AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key or None,
    )

    taxonomy_nodes = await fetch_taxonomy_nodes(kb_slug, org_id)

    store = await run_clustering_for_kb(
        org_id=org_id,
        kb_slug=kb_slug,
        qdrant_client=client,
        taxonomy_nodes=taxonomy_nodes,
    )

    if store is None:
        logger.info("clustering_job_skipped", org_id=org_id, kb_slug=kb_slug)
        return {"status": "skipped", "reason": "too_few_documents"}

    # Generate proposals for unmatched clusters (R3)
    unmatched = [c for c in store.clusters if c.taxonomy_node_id is None and c.size >= 5]
    proposals_submitted = 0

    if unmatched:
        proposals_submitted = await _generate_cluster_proposals(
            org_id=org_id,
            kb_slug=kb_slug,
            unmatched_clusters=unmatched,
            taxonomy_nodes=taxonomy_nodes,
        )

    logger.info(
        "clustering_job_complete",
        org_id=org_id,
        kb_slug=kb_slug,
        clusters=len(store.clusters),
        unmatched=len(unmatched),
        proposals_submitted=proposals_submitted,
    )
    return {
        "status": "completed",
        "clusters": len(store.clusters),
        "unmatched": len(unmatched),
        "proposals_submitted": proposals_submitted,
    }


async def _generate_cluster_proposals(
    org_id: str,
    kb_slug: str,
    unmatched_clusters: list,
    taxonomy_nodes: list,
) -> int:
    """Generate taxonomy proposals for unmatched clusters (SPEC-KB-024 R3).

    One LLM call per cluster to suggest a category name.
    Checks for duplicate pending proposals before submitting (AC8).
    """
    from knowledge_ingest.portal_client import submit_taxonomy_proposal

    proposals_submitted = 0

    # Check for existing pending proposals to avoid duplicates (AC8)
    existing_proposals = await _get_pending_proposals(org_id, kb_slug)

    for cluster in unmatched_clusters:
        # Skip if a proposal with similar labels is already pending
        if _has_similar_pending_proposal(cluster.content_label_summary, existing_proposals):
            logger.info(
                "clustering_proposal_dedup",
                org_id=org_id,
                kb_slug=kb_slug,
                cluster_id=cluster.cluster_id,
                labels=cluster.content_label_summary,
            )
            continue

        # R3: One LLM call to suggest category name
        category_name = await _suggest_category_name(cluster.content_label_summary)
        if not category_name:
            continue

        try:
            await submit_taxonomy_proposal(
                org_id=org_id,
                kb_slug=kb_slug,
                proposal_type="new_node",
                title=category_name,
                description=(
                    f"Auto-discovered cluster with {cluster.size} documents. "
                    f"Keywords: {', '.join(cluster.content_label_summary)}"
                ),
                payload={
                    "name": category_name,
                    "parent_id": None,
                    "cluster_centroid": cluster.centroid,
                },
            )
            proposals_submitted += 1
        except Exception:
            logger.exception(
                "clustering_proposal_failed",
                org_id=org_id,
                kb_slug=kb_slug,
                cluster_id=cluster.cluster_id,
            )

    return proposals_submitted


async def _get_pending_proposals(org_id: str, kb_slug: str) -> list[dict]:
    """Fetch pending proposals from portal to check for duplicates."""
    import httpx

    url = f"{settings.portal_url}/api/v1/{kb_slug}/taxonomy/proposals"
    headers = {}
    if settings.portal_internal_token:
        headers["x-internal-token"] = settings.portal_internal_token

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers, params={"status": "pending"})
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        logger.warning("clustering_pending_proposals_fetch_failed", org_id=org_id, kb_slug=kb_slug)
    return []


def _has_similar_pending_proposal(
    labels: list[str], existing_proposals: list[dict]
) -> bool:
    """Check if any pending proposal has overlapping labels."""
    label_set = set(labels)
    for proposal in existing_proposals:
        title = (proposal.get("title") or "").lower()
        # Simple overlap check: if any label appears in the proposal title
        for label in label_set:
            if label.lower() in title:
                return True
    return False


async def _suggest_category_name(labels: list[str]) -> str | None:
    """Use klai-fast to suggest a taxonomy category name from document labels (R3).

    Returns a short category name (max 40 chars, Dutch) or None on failure.
    """
    import httpx

    prompt = (
        "Gegeven deze documentbeschrijvingen: "
        f"{', '.join(labels)}. "
        "Stel een korte taxonomiecategorienaam voor (max 40 tekens, Nederlands)."
    )

    try:
        async with httpx.AsyncClient(timeout=settings.taxonomy_classification_timeout) as client:
            resp = await asyncio.wait_for(
                client.post(
                    f"{settings.litellm_url}/v1/chat/completions",
                    json={
                        "model": settings.enrichment_model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 30,
                        "temperature": 0.3,
                    },
                    headers={"Authorization": f"Bearer {settings.litellm_api_key}"},
                ),
                timeout=settings.taxonomy_classification_timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                name = data["choices"][0]["message"]["content"].strip().strip('"')
                return name[:40]  # enforce max length
    except Exception:
        logger.exception("clustering_suggest_name_failed", labels=labels)
    return None
