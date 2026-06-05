"""Periodic cleanup for direct-upload artifacts stuck in pending.

This is a final safety net. Normal enrichment must still set
``index_status`` to ``synced`` or ``failed`` itself; this janitor only handles
old pending rows that no longer have a runnable Procrastinate enrichment job.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from knowledge_ingest import pg_store, queues
from knowledge_ingest.db import cross_org_admin_connection

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
