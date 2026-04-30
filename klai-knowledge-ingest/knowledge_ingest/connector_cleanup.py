"""Connector-delete orchestrator.

SPEC-CONNECTOR-DELETE-LIFECYCLE-001 REQ-05: ONE place to scrub everything
that belongs to a connector — Postgres rows across schemas, Qdrant vectors,
FalkorDB graph nodes, S3 image keys, queued procrastinate jobs, klai-connector
sync_runs, and finally the portal_connectors row itself.

Why centralise: pre-PR, cleanup logic was scattered across
``pg_store.delete_connector_artifacts``, ``qdrant_store.delete_connector``,
``graph_module.delete_kb_episodes``, ``klai_connector_client.delete_sync_runs``,
plus a recently-added ``pg_store.delete_connector_crawl_jobs``. Adding a new
data store meant remembering five files. The Voys e2e on 2026-04-30
proved that any forgotten store leaks orphan data forever, AND that
in-flight procrastinate jobs would regenerate Qdrant/FalkorDB content
seconds AFTER a synchronous delete completed.

This module wraps it in a single idempotent function that the
procrastinate worker (``connector_purge_task``) drives.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from knowledge_ingest import graph as graph_module
from knowledge_ingest import pg_store, qdrant_store
from knowledge_ingest.db import get_pool

logger = structlog.get_logger()


# Procrastinate task names we want to cancel on connector delete.
# Module path is what procrastinate stores as ``task_name`` — see
# ``enrichment_tasks._register_tasks``.
_ENRICHMENT_TASKS = (
    "knowledge_ingest.enrichment_tasks.enrich_document_bulk",
    "knowledge_ingest.enrichment_tasks.enrich_document_interactive",
)
_GRAPHITI_TASKS = ("knowledge_ingest.enrichment_tasks.ingest_graphiti_episode",)


@dataclass
class CleanupReport:
    """Per-step counts for observability + assertions in tests."""

    enrichment_jobs_cancelled: int = 0
    graphiti_jobs_cancelled: int = 0
    artifacts_deleted: int = 0
    crawl_jobs_deleted: int = 0
    qdrant_chunks_deleted: int = 0
    falkor_episodes_deleted: int = 0
    s3_images_deleted: int = 0
    sync_runs_deleted: int | None = None  # None when REQ-08 FK CASCADE owns it

    def as_dict(self) -> dict[str, Any]:
        return {
            "enrichment_jobs_cancelled": self.enrichment_jobs_cancelled,
            "graphiti_jobs_cancelled": self.graphiti_jobs_cancelled,
            "artifacts_deleted": self.artifacts_deleted,
            "crawl_jobs_deleted": self.crawl_jobs_deleted,
            "qdrant_chunks_deleted": self.qdrant_chunks_deleted,
            "falkor_episodes_deleted": self.falkor_episodes_deleted,
            "s3_images_deleted": self.s3_images_deleted,
            "sync_runs_deleted": self.sync_runs_deleted,
        }


async def _list_artifact_ids(org_id: str, kb_slug: str, connector_id: str) -> list[str]:
    """Return artifact UUIDs for a connector, BEFORE we delete them.

    Needed because the graphiti-cancel step filters procrastinate-jobs by
    artifact_id (the graphiti task signature does not include
    ``source_connector_id``). We capture the set before
    ``delete_connector_artifacts`` removes the rows.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id::text AS id FROM knowledge.artifacts
             WHERE org_id = $1
               AND kb_slug = $2
               AND extra IS NOT NULL
               AND extra::jsonb->>'source_connector_id' = $3
            """,
            org_id,
            kb_slug,
            connector_id,
        )
    return [r["id"] for r in rows]


async def _cancel_enrichment_jobs(proc_app: Any, connector_id: str) -> int:
    """Cancel queued + in-flight enrich_document_* jobs for this connector.

    Filter: ``args->'extra_payload'->>'source_connector_id' = connector_id``.
    Procrastinate ``cancel_job_by_id_async`` is per-id — we discover IDs
    via a single SELECT then loop.

    ``abort=True`` marks running jobs for asyncio.CancelledError delivery
    via Postgres NOTIFY (Procrastinate native). ``delete_job=True`` purges
    the row from the queue table after cancellation so it never replays.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id FROM procrastinate_jobs
             WHERE task_name = ANY($1::text[])
               AND status IN ('todo', 'doing')
               AND args->'extra_payload'->>'source_connector_id' = $2
            """,
            list(_ENRICHMENT_TASKS),
            connector_id,
        )
    job_ids = [int(r["id"]) for r in rows]

    cancelled = 0
    for jid in job_ids:
        try:
            await proc_app.job_manager.cancel_job_by_id_async(jid, abort=True, delete_job=True)
            cancelled += 1
        except Exception:
            # Job may have just transitioned from todo->doing->finished
            # between our SELECT and the cancel call. That's a no-op for
            # us — the existence-guard (REQ-07) catches the race anyway.
            logger.warning(
                "cancel_job_failed",
                job_id=jid,
                connector_id=connector_id,
                exc_info=True,
            )
    return cancelled


async def _cancel_graphiti_jobs(proc_app: Any, artifact_ids: list[str]) -> int:
    """Cancel queued + in-flight ingest_graphiti_episode jobs.

    The graphiti task signature does not carry ``source_connector_id``;
    it accepts ``artifact_id`` as an arg. We pass the artifact-id set
    captured BEFORE artifact deletion (``_list_artifact_ids``) so the
    filter still resolves.
    """
    if not artifact_ids:
        return 0
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id FROM procrastinate_jobs
             WHERE task_name = ANY($1::text[])
               AND status IN ('todo', 'doing')
               AND args->>'artifact_id' = ANY($2::text[])
            """,
            list(_GRAPHITI_TASKS),
            artifact_ids,
        )
    job_ids = [int(r["id"]) for r in rows]

    cancelled = 0
    for jid in job_ids:
        try:
            await proc_app.job_manager.cancel_job_by_id_async(jid, abort=True, delete_job=True)
            cancelled += 1
        except Exception:
            logger.warning("cancel_graphiti_job_failed", job_id=jid, exc_info=True)
    return cancelled


async def purge_connector(
    *,
    org_id: str,
    kb_slug: str,
    connector_id: str,
    proc_app: Any,
) -> CleanupReport:
    """Delete every byte that belongs to a connector. Idempotent.

    Order matters:
      1. Snapshot artifact-ids (needed for graphiti-cancel below).
      2. Cancel queued + in-flight enrichment jobs — closes the regrow
         window before we touch the data stores.
      3. Cancel queued + in-flight graphiti jobs (by artifact-id set).
      4. Hard-delete pg artifacts (cascades via FK to embedding_queue,
         artifact_entities, derivations + URL-set scope to crawled_pages
         and page_links — see ``pg_store.delete_connector_artifacts``).
      5. Hard-delete pg crawl_jobs scoped via ``config->>'connector_id'``.
      6. Hard-delete FalkorDB episodes by episode-id list.
      7. Hard-delete Qdrant vectors by ``source_connector_id`` filter.
      8. (REQ-08 will cascade ``connector.sync_runs`` via FK; until then
         the portal-side ``klai_connector_client.delete_sync_runs`` is the
         interim fence — not called from this module to keep
         knowledge-ingest free of cross-service deletes during cleanup.
         The portal worker driver calls it before invoking us.)

    Each step is idempotent. Re-running on an already-purged connector
    returns a report with zero counts (and no errors).

    Failure semantics: any exception aborts the function. Procrastinate
    retries the worker-task per its retry policy (REQ-04.5). Because every
    step is idempotent, retries do not double-count.
    """
    log = logger.bind(org_id=org_id, kb_slug=kb_slug, connector_id=connector_id)
    log.info("connector_purge_started")

    # Step 1: capture artifact UUIDs before they vanish.
    artifact_ids = await _list_artifact_ids(org_id, kb_slug, connector_id)
    log.info("connector_purge_step_artifacts_listed", count=len(artifact_ids))

    # Step 2: cancel enrichment jobs (uses extra_payload.source_connector_id).
    enrichment_cancelled = await _cancel_enrichment_jobs(proc_app, connector_id)
    log.info(
        "connector_purge_step_enrichment_jobs_cancelled",
        cancelled=enrichment_cancelled,
    )

    # Step 3: cancel graphiti jobs (uses artifact_id list from step 1).
    graphiti_cancelled = await _cancel_graphiti_jobs(proc_app, artifact_ids)
    log.info(
        "connector_purge_step_graphiti_jobs_cancelled",
        cancelled=graphiti_cancelled,
    )

    # Step 4: snapshot graphiti episode-ids so we can clean FalkorDB even
    # after artifacts are gone (the join-key is artifact->episode in pg).
    episode_ids = await pg_store.get_connector_episode_ids(org_id, kb_slug, connector_id)
    log.info("connector_purge_step_episodes_listed", count=len(episode_ids))

    # Step 4b: snapshot orphan S3 image keys BEFORE the artifact delete
    # cascades the artifact_images rows away. Refcount on content_hash so
    # we only return keys not referenced by any other artifact.
    # SPEC-CONNECTOR-DELETE-LIFECYCLE-001 REQ-06.3.
    orphan_image_keys = await pg_store.get_orphan_image_keys_for_connector(
        org_id, kb_slug, connector_id
    )
    log.info(
        "connector_purge_step_orphan_images_listed",
        count=len(orphan_image_keys),
    )

    # Step 5: pg artifacts (and cascade — see function docstring).
    artifacts_deleted = await pg_store.delete_connector_artifacts(org_id, kb_slug, connector_id)
    log.info("connector_purge_step_artifacts_deleted", count=artifacts_deleted)

    # Step 6: pg crawl_jobs.
    crawl_jobs_deleted = await pg_store.delete_connector_crawl_jobs(org_id, kb_slug, connector_id)
    log.info("connector_purge_step_crawl_jobs_deleted", count=crawl_jobs_deleted)

    # Step 7: FalkorDB episodes (using the snapshot we just took).
    await graph_module.delete_kb_episodes(org_id, episode_ids)
    log.info(
        "connector_purge_step_falkor_episodes_deleted",
        count=len(episode_ids),
    )

    # Step 8: Qdrant vectors.
    await qdrant_store.delete_connector(org_id, kb_slug, connector_id)
    log.info("connector_purge_step_qdrant_deleted")

    # Step 9: Garage S3 image keys — orchestrator-scoped ones from step 4b.
    s3_images_deleted = 0
    image_store = None
    try:
        from knowledge_ingest.adapters.crawler import _build_image_store

        image_store = _build_image_store()
    except Exception:
        logger.exception("connector_purge_step_image_store_init_failed")

    if orphan_image_keys and image_store is not None:
        s3_images_deleted = await image_store.delete_keys(orphan_image_keys)
        log.info(
            "connector_purge_step_s3_images_deleted",
            count=s3_images_deleted,
            requested=len(orphan_image_keys),
        )

    # Step 10 (JANITOR): catch the cleanup-misses that the per-connector
    # logic above structurally cannot find:
    #   a) FalkorDB episodes written AFTER our cancel — graphiti tasks
    #      with sync LLM calls don't honour asyncio cancellation, so
    #      mid-flight episodes can land between cancel + delete. The
    #      artifact-id snapshot we took in step 1 still applies — any
    #      episode in FalkorDB referencing those artifact-ids is now
    #      orphan because the artifact rows are gone (step 5).
    #   b) Garage keys that lost their last reference at step 5's CASCADE
    #      on artifact_images. Refcount on content_hash: a key is orphan
    #      iff no artifact in this KB still references its hash.
    # Both run unconditionally — even when steps 4b/7 returned zero,
    # because their inputs depend on rows that no longer exist.
    falkor_orphans_deleted = await graph_module.delete_orphan_episodes_for_artifact_ids(
        org_id, artifact_ids
    )
    log.info(
        "connector_purge_step_falkor_orphans_swept",
        count=falkor_orphans_deleted,
    )

    janitor_s3_deleted = 0
    if image_store is not None:
        try:
            from minio import Minio

            from knowledge_ingest.adapters.crawler import _build_image_store
            from knowledge_ingest.config import settings as ki_settings

            active_hashes = await pg_store.get_active_image_hashes_for_kb(org_id, kb_slug)
            # List all S3 keys under {org}/images/{kb_slug}/, derive the
            # content_hash from each filename, and propose for delete the
            # ones whose hash isn't in active_hashes.
            prefix = f"{org_id}/images/{kb_slug}/"
            from minio import Minio  # noqa: F811

            mc = Minio(
                ki_settings.garage_s3_endpoint,
                access_key=ki_settings.garage_access_key,
                secret_key=ki_settings.garage_secret_key,
                region=ki_settings.garage_region,
                secure=False,
            )
            orphan_under_prefix: list[str] = []
            for obj in mc.list_objects(ki_settings.garage_bucket, prefix=prefix, recursive=True):
                key = obj.object_name
                basename = key.rsplit("/", 1)[-1]
                content_hash = basename.rsplit(".", 1)[0]
                if content_hash and content_hash not in active_hashes:
                    orphan_under_prefix.append(key)
            if orphan_under_prefix:
                janitor_s3_deleted = await image_store.delete_keys(orphan_under_prefix)
            log.info(
                "connector_purge_step_garage_orphans_swept",
                count=janitor_s3_deleted,
                scanned=len(orphan_under_prefix),
                active_hashes=len(active_hashes),
            )
        except Exception:
            logger.exception("connector_purge_step_garage_janitor_failed")

    s3_images_deleted += janitor_s3_deleted

    # NOTE: connector.sync_runs cleanup is invoked by the portal-side
    # delete-orchestration BEFORE it asks knowledge-ingest to purge. That
    # keeps cross-service auth + tenant-scoping aligned with how the rest
    # of klai-connector's API is gated. When SPEC-CONNECTOR-CLEANUP-001
    # REQ-04 lands the cross-schema FK CASCADE, even that explicit call
    # becomes redundant (the portal_connectors row delete cascades).

    report = CleanupReport(
        enrichment_jobs_cancelled=enrichment_cancelled,
        graphiti_jobs_cancelled=graphiti_cancelled,
        artifacts_deleted=artifacts_deleted,
        crawl_jobs_deleted=crawl_jobs_deleted,
        qdrant_chunks_deleted=0,  # qdrant_store.delete_connector doesn't return a count today
        falkor_episodes_deleted=len(episode_ids) + falkor_orphans_deleted,
        s3_images_deleted=s3_images_deleted,
        sync_runs_deleted=None,
    )
    log.info("connector_purge_completed", **report.as_dict())
    return report
