"""Re-trigger auto-categorise per child-centroid for already-approved consolidated nodes.

SPEC-TAXONOMY-REVIEW-FLOW-001 Issue 1 migration tool.

Background: nodes approved BEFORE this SPEC landed only have the aggregate
``cluster_centroid`` stored in their source proposal's payload. With the
multi-centroid approve path now in place, NEW approves get tight per-child
matching automatically — but existing approved nodes are stuck on the
diffuse aggregate centroid and have ~90% chunks untagged.

This script reads every approved proposal in a KB whose payload contains
``child_centroids`` and re-enqueues one ``auto_categorise`` job per child
centroid under the same node_id. Idempotent: ``classify_by_centroid``
matches by `taxonomy_node_id` set semantics, so re-running adds the same
node_id to the same chunks (no-op) plus picks up newly-tight matches.

Usage (run inside portal-api container so it has prod DB + env):
    docker exec klai-core-portal-api-1 python -m scripts.retag_consolidated_nodes \\
        --zitadel-org-id <id> --kb-slug <slug>

Or if scripts/ is not in the image (klai-portal CLAUDE.md note):
    docker exec -i klai-core-portal-api-1 python - < this-file.py \\
        --zitadel-org-id <id> --kb-slug <slug>
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("retag_consolidated_nodes")


async def amain(args: argparse.Namespace) -> int:
    # Defer imports so --help works without full env
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal, set_tenant
    from app.models.knowledge_bases import PortalKnowledgeBase
    from app.models.portal import PortalOrg
    from app.models.taxonomy import PortalTaxonomyProposal
    from app.services.knowledge_ingest_client import enqueue_auto_categorise

    async with AsyncSessionLocal() as db:
        # Look up org by zitadel id (no tenant context needed; portal_orgs is permissive)
        org_result = await db.execute(select(PortalOrg).where(PortalOrg.zitadel_org_id == args.zitadel_org_id))
        org = org_result.scalar_one_or_none()
        if not org:
            logger.error("Org not found for zitadel_org_id=%s", args.zitadel_org_id)
            return 1

        await set_tenant(db, org.id)

        kb_result = await db.execute(
            select(PortalKnowledgeBase).where(
                PortalKnowledgeBase.slug == args.kb_slug,
                PortalKnowledgeBase.org_id == org.id,
            )
        )
        kb = kb_result.scalar_one_or_none()
        if not kb:
            logger.error("KB not found: %s", args.kb_slug)
            return 1

        proposals_result = await db.execute(
            select(PortalTaxonomyProposal).where(
                PortalTaxonomyProposal.kb_id == kb.id,
                PortalTaxonomyProposal.status == "approved",
            )
        )
        proposals = proposals_result.scalars().all()

    logger.info("Found %d approved proposals in KB %s", len(proposals), args.kb_slug)

    re_triggered = 0
    skipped_legacy = 0
    skipped_no_node = 0

    for p in proposals:
        payload: dict[str, Any] = p.payload or {}
        child_centroids = payload.get("child_centroids")
        # Find the corresponding node by name (we did not store node_id back on
        # the proposal, but the new node has the same name as the proposal
        # title or payload.suggested_name).
        if not isinstance(child_centroids, list) or not child_centroids:
            logger.info(
                "skip_proposal_no_child_centroids id=%s title=%r — legacy single-centroid",
                p.id,
                p.title,
            )
            skipped_legacy += 1
            continue

        # Look up the corresponding node by (kb_id, name)
        async with AsyncSessionLocal() as db:
            await set_tenant(db, org.id)
            from app.models.taxonomy import PortalTaxonomyNode

            node_name = payload.get("suggested_name") or p.title
            node_result = await db.execute(
                select(PortalTaxonomyNode).where(
                    PortalTaxonomyNode.kb_id == kb.id,
                    PortalTaxonomyNode.name == node_name,
                )
            )
            node = node_result.scalar_one_or_none()
            if not node:
                logger.warning(
                    "skip_proposal_no_node id=%s title=%r — node was deleted?",
                    p.id,
                    p.title,
                )
                skipped_no_node += 1
                continue
            node_id = node.id

        logger.info(
            "retag node_id=%s name=%r centroids=%d",
            node_id,
            node_name,
            len(child_centroids),
        )

        for centroid in child_centroids:
            await enqueue_auto_categorise(
                org_id=str(org.zitadel_org_id),
                kb_slug=args.kb_slug,
                node_id=node_id,
                cluster_centroid=centroid,
            )
        re_triggered += 1

    logger.info(
        "done re_triggered=%d skipped_legacy=%d skipped_no_node=%d",
        re_triggered,
        skipped_legacy,
        skipped_no_node,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--zitadel-org-id", required=True, help="Zitadel org ID (string)")
    parser.add_argument("--kb-slug", required=True, help="KB slug")
    args = parser.parse_args()
    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
