"""
Procrastinate task for async taxonomy backfill.

Queue: taxonomy-backfill (separate from enrichment queues; can take minutes for large KBs).
Deduplication: backfill_job_options() gives every enqueue the same per-KB
queueing_lock (at most one waiting) and lock (at most one running).

An hourly sweep (sweep_unclassified_kbs) queues a backfill for every KB with
chunks that ingest stored as null because the LLM had no capacity.

The actual 4-phase logic (label, migrate, classify, tag) lives in _run_backfill().
Phase 0 (blind labelling) runs unconditionally; phases 1-3 require taxonomy nodes.
"""

from __future__ import annotations

import asyncio
import warnings
from typing import Any

import structlog

from knowledge_ingest import queues
from knowledge_ingest.llm_capacity import LLMCapacityUnavailable

logger = structlog.get_logger()

# Hourly: work classified during an LLM outage is labelled within about an
# hour of capacity returning, while an outage costs at most one failing LLM
# call per affected KB per hour (a backfill stops at its first capacity
# failure). Daily would leave documents without labels or taxonomy for up to
# a day after recovery; more often buys little, because LiteLLM's pool hook
# only re-probes a full key every 5 minutes and budgets roll over per day.
UNCLASSIFIED_SWEEP_CRON = "17 * * * *"

LLM_CAPACITY_BACKFILL_MESSAGE = "LLM capacity unavailable; the automatic sweep will retry"


def backfill_job_options(org_id: str, kb_slug: str) -> dict[str, str]:
    """``queueing_lock`` keeps one backfill waiting per KB; ``lock`` keeps two
    from running at once for the same KB (the queueing lock only covers todo)."""
    key = f"taxonomy-backfill:{org_id}:{kb_slug}"
    return {"queueing_lock": key, "lock": key}


def register_taxonomy_tasks(procrastinate_app: Any) -> None:
    """Register taxonomy tasks on the Procrastinate app. Called from enrichment_tasks.init_app()."""
    import procrastinate

    class _RetryOnceUnlessLLMCapacity(procrastinate.BaseRetryStrategy):
        """``RetryStrategy(max_attempts=1)``, except that a capacity failure is
        final: an immediate retry would meet the same spent key, and the
        hourly sweep queues the next attempt."""

        def get_retry_decision(self, *, exception: BaseException, job: Any) -> Any:
            if job.attempts >= 1 or isinstance(exception, LLMCapacityUnavailable):
                return None
            return procrastinate.RetryDecision(retry_in={"seconds": 0})

    @procrastinate_app.task(
        queue=queues.TAXONOMY_BACKFILL,
        retry=_RetryOnceUnlessLLMCapacity(),
    )
    async def run_taxonomy_backfill(
        org_id: str,
        kb_slug: str,
        batch_size: int = 100,
    ) -> dict:
        """Run the 4-phase taxonomy backfill as a background job.

        When the LLM has no capacity the run stops at the first document it
        could not label or classify, leaves that one and the rest null, and
        fails; sweep_unclassified_kbs queues the next attempt.
        """
        try:
            return await _run_backfill(org_id=org_id, kb_slug=kb_slug, batch_size=batch_size)
        except LLMCapacityUnavailable as exc:
            logger.info(
                "taxonomy_backfill_stopped_llm_capacity",
                org_id=org_id,
                kb_slug=kb_slug,
                reason=str(exc),
            )
            raise LLMCapacityUnavailable(LLM_CAPACITY_BACKFILL_MESSAGE) from exc

    procrastinate_app.run_taxonomy_backfill = run_taxonomy_backfill  # type: ignore[attr-defined]

    @procrastinate_app.periodic(
        cron=UNCLASSIFIED_SWEEP_CRON,
        periodic_id="taxonomy-unclassified-sweep",
    )
    @procrastinate_app.task(
        name="knowledge_ingest.taxonomy_tasks.sweep_unclassified_kbs_periodic",
        queue=queues.MAINTENANCE,
        retry=procrastinate.RetryStrategy(max_attempts=1),
        queueing_lock="taxonomy-unclassified-sweep",
    )
    async def sweep_unclassified_kbs_periodic(timestamp: int) -> dict:
        logger.info("taxonomy_unclassified_sweep_started", deferrer_ts=timestamp)
        kbs = await sweep_unclassified_kbs()
        return {"queued_kbs": len(kbs)}

    procrastinate_app.sweep_unclassified_kbs_periodic = sweep_unclassified_kbs_periodic  # type: ignore[attr-defined]


async def sweep_unclassified_kbs() -> list[tuple[str, str]]:
    """Queue one backfill for every KB that still has chunks stored as null.

    Null content_label / taxonomy_node_ids is the "not yet classified" state
    ingest writes when the LLM had no capacity; [] (ran, found nothing) and a
    missing field (legacy, or no taxonomy on the KB) are not swept. One scroll
    per KB found: each query excludes the KBs already seen.
    """
    from procrastinate.exceptions import AlreadyEnqueued
    from qdrant_client.models import (
        FieldCondition,
        Filter,
        IsNullCondition,
        MatchValue,
        PayloadField,
    )

    from knowledge_ingest import qdrant_store
    from knowledge_ingest.enrichment_tasks import get_app

    client = qdrant_store.get_client()
    unclassified = [
        IsNullCondition(is_null=PayloadField(key="content_label")),
        IsNullCondition(is_null=PayloadField(key="taxonomy_node_ids")),
    ]
    kbs: list[tuple[str, str]] = []
    while True:
        points, _ = await client.scroll(
            collection_name=qdrant_store.COLLECTION,
            scroll_filter=Filter(
                should=unclassified,
                must_not=[
                    Filter(
                        must=[
                            FieldCondition(key="org_id", match=MatchValue(value=org_id)),
                            FieldCondition(key="kb_slug", match=MatchValue(value=kb_slug)),
                        ]
                    )
                    for org_id, kb_slug in kbs
                ],
            ),
            limit=1,
            with_payload=["org_id", "kb_slug"],
            with_vectors=False,
        )
        if not points:
            break
        payload = points[0].payload or {}
        kbs.append((payload["org_id"], payload["kb_slug"]))

    backfill = get_app().run_taxonomy_backfill
    for org_id, kb_slug in kbs:
        try:
            await backfill.configure(**backfill_job_options(org_id, kb_slug)).defer_async(
                org_id=org_id, kb_slug=kb_slug, batch_size=100
            )
        except AlreadyEnqueued:
            pass
    logger.info("taxonomy_unclassified_sweep", kbs=len(kbs))
    return kbs


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
        IsNullCondition,
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
        # Absent means "no taxonomy on this KB". Left null, the sweep would
        # queue this KB every hour with nothing to classify against.
        await client.delete_payload(
            COLLECTION,
            keys=["taxonomy_node_ids"],
            points=Filter(
                must=[
                    FieldCondition(key="org_id", match=MatchValue(value=org_id)),
                    FieldCondition(key="kb_slug", match=MatchValue(value=kb_slug)),
                    IsNullCondition(is_null=PayloadField(key="taxonomy_node_ids")),
                ]
            ),
        )
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
    }
