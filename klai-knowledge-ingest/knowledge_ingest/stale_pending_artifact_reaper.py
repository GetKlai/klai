"""Periodic cleanup for artifacts stuck in pending.

``index_status`` says whether a version's raw text is in the vector index. It
is ``pending`` only while an ingest request writes that text, and during a
reindex until the enrichment job records its outcome. What this janitor can
still find is a version whose ingest request died between creating the row
and finishing the raw write (process killed, vector store error after the
row was created): its text never became searchable, so ``failed`` is true.
A reindex whose enrichment job is lost does not reach it, because every way
such a job ends records the outcome (``derived_state.record_outcome``). Each
run that marks rows logs ``stale_pending_artifacts_failed`` at warning level,
which the ``knowledge_ingest_stale_pending_reaper_fired`` alert watches.

A version is born with both product states ``pending``. When its ingest died,
nothing will finish them, so each product that is still ``pending`` without a
job becomes ``failed (ingest_interrupted)`` through the one writer, which logs
``derived_product_failed`` per product. A product that already has an outcome
is left as it is; nothing is re-queued.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from knowledge_ingest import pg_store, queues
from knowledge_ingest.db import cross_org_admin_connection
from knowledge_ingest.derived_state import FAILED, set_product_state

logger = structlog.get_logger()

STALE_PENDING_AFTER_SECONDS = 30 * 60
STALE_PENDING_REAPER_LIMIT = 500


async def reap_stale_pending_artifacts(
    *,
    stale_after_seconds: int = STALE_PENDING_AFTER_SECONDS,
    limit: int = STALE_PENDING_REAPER_LIMIT,
) -> list[dict]:
    cutoff = int(time.time()) - stale_after_seconds
    async with cross_org_admin_connection() as conn:
        rows = await pg_store.mark_stale_pending_artifacts_failed(
            conn,
            cutoff_created_at=cutoff,
            limit=limit,
        )
        for row in rows:
            for product in row.pop("interrupted"):
                await set_product_state(
                    conn, row["artifact_id"], product, FAILED, "ingest_interrupted"
                )
    if rows:
        logger.warning(
            "stale_pending_artifacts_failed",
            count=len(rows),
            cutoff_created_at=cutoff,
            artifacts=rows,
        )
    else:
        logger.info("stale_pending_artifact_reaper_clean", cutoff_created_at=cutoff)
    return rows


def register_stale_pending_artifact_reaper(procrastinate_app: Any) -> None:
    """Register the periodic stale-pending cleanup task."""
    import procrastinate

    @procrastinate_app.periodic(
        cron="*/15 * * * *",
        periodic_id="stale-pending-artifact-reaper",
    )
    @procrastinate_app.task(
        name="knowledge_ingest.stale_pending_artifact_reaper.reap_stale_pending_artifacts_periodic",
        queue=queues.INGEST_KB,
        retry=procrastinate.RetryStrategy(max_attempts=1),
        queueing_lock="stale-pending-artifact-reaper",
    )
    async def reap_stale_pending_artifacts_periodic(timestamp: int) -> dict:
        logger.info("stale_pending_artifact_reaper_started", deferrer_ts=timestamp)
        rows = await reap_stale_pending_artifacts()
        return {"failed": len(rows)}

    procrastinate_app.reap_stale_pending_artifacts_periodic = (  # type: ignore[attr-defined]
        reap_stale_pending_artifacts_periodic
    )
