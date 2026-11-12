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

A version is born with both product states ``pending``, and a reindex, a graph
refresh or a rebuild sets a product pending again before it works on it. A
product of a current version that has been ``pending`` longer than the cutoff
(its own ``_state_at``), with no queued or running job that could finish it and
no operation holding its knowledge base, will never be finished: the request
died after the text was written, or before. It becomes ``failed
(interrupted)`` through the one writer, which logs ``derived_product_failed``
per product, whatever ``index_status`` says; ``index_status`` itself is only
failed where it is still ``pending``. An outcome already recorded is never
selected, and nothing is re-queued.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from knowledge_ingest import pg_store, queues
from knowledge_ingest.db import cross_org_admin_connection
from knowledge_ingest.derived_state import FAILED, set_product_state
from knowledge_ingest.operations import operation_running

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
        interrupted = [
            product
            for product in await pg_store.stale_pending_products(conn, cutoff=cutoff, limit=limit)
            # A run holding the knowledge base (rebuild_kb) works on its
            # versions without a job per version, and one large document can
            # take longer than the cutoff: 2,000 chunk calls at the alias's 2
            # requests per second take about 17 minutes alone, and four
            # documents in parallel share that rate.
            if not await operation_running(conn, product["org_id"], product["kb_slug"])
        ]
        for product in interrupted:
            await set_product_state(
                conn, product["artifact_id"], product["product"], FAILED, "interrupted"
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
