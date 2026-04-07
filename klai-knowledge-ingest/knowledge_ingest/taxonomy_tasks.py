"""
Procrastinate task for async taxonomy backfill.

Queue: taxonomy-backfill (separate from enrichment queues; can take minutes for large KBs).
Deduplication: queueing_lock = 'taxonomy-backfill:{org_id}:{kb_slug}' ensures at most
one pending/running backfill per KB.

The actual 4-phase logic (label, migrate, classify, tag) lives in _run_backfill().
Phase 0 (blind labelling) runs unconditionally; phases 1-3 require taxonomy nodes.
"""

from __future__ import annotations

import asyncio
import warnings
from typing import Any

import structlog

from knowledge_ingest.proposal_generator import DocumentSummary, maybe_generate_proposal

logger = structlog.get_logger()


def register_taxonomy_tasks(procrastinate_app: Any) -> None:
    """Register taxonomy tasks on the Procrastinate app. Called from enrichment_tasks.init_app()."""
    import procrastinate

    @procrastinate_app.task(
        queue="taxonomy-backfill",
        retry=procrastinate.RetryStrategy(max_attempts=1),
    )
    async def run_taxonomy_backfill(
        org_id: str,
        kb_slug: str,
        batch_size: int = 100,
    ) -> dict:
        """Run the 4-phase taxonomy backfill as a background job."""
        result = await _run_backfill(org_id=org_id, kb_slug=kb_slug, batch_size=batch_size)
        return result

    procrastinate_app.run_taxonomy_backfill = run_taxonomy_backfill  # type: ignore[attr-defined]


async def _run_backfill(org_id: str, kb_slug: str, batch_size: int) -> dict:
    """Core 4-phase backfill logic.

    Phase 0: Generate blind content_label for chunks missing it (SPEC-KB-023).
    Phase 1: Migrate old taxonomy_node_id -> taxonomy_node_ids.
    Phase 2: Re-classify unclassified chunks.
    Phase 3: Generate tags for chunks with taxonomy_node_ids but no tags.

    Returns a dict with labelled/migrated/classified/tagged/skipped counts.
    """
    warnings.filterwarnings("ignore", message="Api key is used with an insecure connection")

    from qdrant_client import AsyncQdrantClient
    from qdrant_client.models import (
        FieldCondition,
        Filter,
        IsEmptyCondition,
        MatchValue,
        PayloadField,
    )

    from knowledge_ingest.config import settings
    from knowledge_ingest.content_labeler import generate_content_label
    from knowledge_ingest.portal_client import fetch_taxonomy_nodes
    from knowledge_ingest.taxonomy_classifier import classify_document

    COLLECTION = "klai_knowledge"

    client = AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key or None,
    )

    labelled = 0
    migrated = 0
    classified = 0
    tagged = 0
    skipped = 0
    proposals_submitted = 0

    # Phase 0: Blind content_label generation (SPEC-KB-023)
    # Runs before taxonomy phases so labels are taxonomy-independent.
    offset = None
    while True:
        phase0_filter = Filter(
            must=[
                FieldCondition(key="org_id", match=MatchValue(value=org_id)),
                FieldCondition(key="kb_slug", match=MatchValue(value=kb_slug)),
                IsEmptyCondition(is_empty=PayloadField(key="content_label")),
            ]
        )

        points, next_offset = await asyncio.wait_for(
            client.scroll(
                collection_name=COLLECTION,
                scroll_filter=phase0_filter,
                limit=batch_size,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            ),
            timeout=30.0,
        )

        if not points:
            break

        # Group by document (artifact_id or path) — one LLM call per document
        doc_groups: dict[str, list] = {}
        for point in points:
            payload = point.payload or {}
            doc_key = payload.get("artifact_id") or payload.get("path") or str(point.id)
            if doc_key not in doc_groups:
                doc_groups[doc_key] = []
            doc_groups[doc_key].append(point)

        for doc_key, doc_points in doc_groups.items():
            first_payload = doc_points[0].payload or {}
            title = first_payload.get("title") or first_payload.get("path") or doc_key
            content_preview = first_payload.get("text", "")[:500]

            content_label = await generate_content_label(
                title=title,
                content_preview=content_preview,
            )

            point_ids = [p.id for p in doc_points]
            await client.set_payload(
                COLLECTION,
                payload={"content_label": content_label},
                points=point_ids,
            )
            labelled += len(point_ids)

        if next_offset is None:
            break
        offset = next_offset

    # Taxonomy phases require nodes — skip if none exist.
    # Always bypass cache: new nodes may have been approved just before this job ran.
    from knowledge_ingest.portal_client import invalidate_cache
    invalidate_cache(org_id, kb_slug)
    taxonomy_nodes = await fetch_taxonomy_nodes(kb_slug, org_id)
    if not taxonomy_nodes:
        logger.info(
            "taxonomy_backfill_no_nodes",
            kb_slug=kb_slug,
            org_id=org_id,
            labelled=labelled,
        )
        return {
            "labelled": labelled,
            "migrated": 0,
            "classified": 0,
            "tagged": 0,
            "skipped": 0,
            "proposals_submitted": 0,
        }

    # Phase 1: Migrate old taxonomy_node_id -> taxonomy_node_ids
    offset = None
    while True:
        phase1_filter = Filter(
            must=[
                FieldCondition(key="org_id", match=MatchValue(value=org_id)),
                FieldCondition(key="kb_slug", match=MatchValue(value=kb_slug)),
                IsEmptyCondition(is_empty=PayloadField(key="taxonomy_node_ids")),
            ],
            must_not=[
                IsEmptyCondition(is_empty=PayloadField(key="taxonomy_node_id")),
            ],
        )

        points, next_offset = await asyncio.wait_for(
            client.scroll(
                collection_name=COLLECTION,
                scroll_filter=phase1_filter,
                limit=batch_size,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            ),
            timeout=30.0,
        )

        if not points:
            break

        for point in points:
            payload = point.payload or {}
            old_id = payload.get("taxonomy_node_id")
            new_ids = [old_id] if old_id is not None else []
            await client.set_payload(
                COLLECTION,
                payload={"taxonomy_node_ids": new_ids},
                points=[point.id],
            )
            migrated += 1

        if next_offset is None:
            break
        offset = next_offset

    # Phase 2: Re-classify unclassified chunks
    # Collect unmatched documents for batch proposal generation at the end.
    unmatched_summaries: list[DocumentSummary] = []
    offset = None
    while True:
        phase2_filter = Filter(
            must=[
                FieldCondition(key="org_id", match=MatchValue(value=org_id)),
                FieldCondition(key="kb_slug", match=MatchValue(value=kb_slug)),
                IsEmptyCondition(is_empty=PayloadField(key="taxonomy_node_id")),
                IsEmptyCondition(is_empty=PayloadField(key="taxonomy_node_ids")),
            ]
        )

        points, next_offset = await asyncio.wait_for(
            client.scroll(
                collection_name=COLLECTION,
                scroll_filter=phase2_filter,
                limit=batch_size,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            ),
            timeout=30.0,
        )

        if not points:
            break

        doc_groups: dict[str, list] = {}
        for point in points:
            payload = point.payload or {}
            doc_key = payload.get("artifact_id") or payload.get("path") or str(point.id)
            if doc_key not in doc_groups:
                doc_groups[doc_key] = []
            doc_groups[doc_key].append(point)

        for doc_key, doc_points in doc_groups.items():
            first_payload = doc_points[0].payload or {}
            title = first_payload.get("title") or first_payload.get("path") or doc_key
            content_preview = first_payload.get("text", "")[:500]

            matched_nodes, suggested_tags = await classify_document(
                title=title,
                content_preview=content_preview,
                taxonomy_nodes=taxonomy_nodes,
            )
            node_ids = [nid for nid, _conf in matched_nodes]

            if not node_ids:
                unmatched_summaries.append(
                    DocumentSummary(title=title, content_preview=content_preview)
                )

            point_ids = [p.id for p in doc_points]
            update_payload: dict = {"taxonomy_node_ids": node_ids}
            if suggested_tags:
                update_payload["tags"] = suggested_tags
                tagged += len(point_ids)

            await client.set_payload(
                COLLECTION,
                payload=update_payload,
                points=point_ids,
            )
            classified += len(point_ids)

        if next_offset is None:
            break
        offset = next_offset

    # Phase 2 post-processing: generate taxonomy proposal if enough unmatched docs accumulated.
    if unmatched_summaries:
        submitted = await maybe_generate_proposal(
            org_id=org_id,
            kb_slug=kb_slug,
            unmatched_documents=unmatched_summaries,
            existing_nodes=taxonomy_nodes,
        )
        proposals_submitted = 1 if submitted else 0

    # Phase 3: Generate tags for chunks with taxonomy_node_ids but no tags
    offset = None
    while True:
        phase3_filter = Filter(
            must=[
                FieldCondition(key="org_id", match=MatchValue(value=org_id)),
                FieldCondition(key="kb_slug", match=MatchValue(value=kb_slug)),
                IsEmptyCondition(is_empty=PayloadField(key="tags")),
            ],
            must_not=[
                IsEmptyCondition(is_empty=PayloadField(key="taxonomy_node_ids")),
            ],
        )

        points, next_offset = await asyncio.wait_for(
            client.scroll(
                collection_name=COLLECTION,
                scroll_filter=phase3_filter,
                limit=batch_size,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            ),
            timeout=30.0,
        )

        if not points:
            break

        doc_groups_tag: dict[str, list] = {}
        for point in points:
            payload = point.payload or {}
            doc_key = payload.get("artifact_id") or payload.get("path") or str(point.id)
            if doc_key not in doc_groups_tag:
                doc_groups_tag[doc_key] = []
            doc_groups_tag[doc_key].append(point)

        for doc_key, doc_points in doc_groups_tag.items():
            first_payload = doc_points[0].payload or {}
            title = first_payload.get("title") or first_payload.get("path") or doc_key
            content_preview = first_payload.get("text", "")[:500]

            _, suggested_tags = await classify_document(
                title=title,
                content_preview=content_preview,
                taxonomy_nodes=taxonomy_nodes,
            )

            if suggested_tags:
                point_ids = [p.id for p in doc_points]
                await client.set_payload(
                    COLLECTION,
                    payload={"tags": suggested_tags},
                    points=point_ids,
                )
                tagged += len(point_ids)

        if next_offset is None:
            break
        offset = next_offset

    logger.info(
        "taxonomy_backfill_complete",
        org_id=org_id,
        kb_slug=kb_slug,
        labelled=labelled,
        migrated=migrated,
        classified=classified,
        tagged=tagged,
        skipped=skipped,
    )
    return {
        "labelled": labelled,
        "migrated": migrated,
        "classified": classified,
        "tagged": tagged,
        "skipped": skipped,
        "proposals_submitted": proposals_submitted,
    }
