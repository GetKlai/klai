"""Stalled-job recovery for procrastinate workers.

Procrastinate v3 detects stalled workers via heartbeat (`procrastinate_workers.last_heartbeat`)
and prunes their rows on the next worker startup. The FK
`procrastinate_jobs.worker_id → procrastinate_workers.id ON DELETE SET NULL`
sets `worker_id = NULL` on jobs whose owning worker disappeared.

But procrastinate does NOT reset those jobs — their status stays `doing` forever.
On every container kill (Docker SIGKILL after the 10s default `stop_grace_period`)
that happens mid-LLM-call, one or more graphiti/enrich jobs become permanent zombies.
After enough deploys the worker concurrency slots fill up and enrichment stalls.

This module fills the gap: at lifespan startup, it prunes stalled worker rows
and retries every job in `doing` status whose owner is gone.

Safe to run because every task this worker handles is idempotent:
- ``ingest_graphiti_episode`` dedups via Episode UUID
- ``enrich_document_bulk`` dedups via content_hash + artifact_id
- ``connector_purge_task`` is fully idempotent by design (SPEC-CONNECTOR-DELETE-LIFECYCLE-001)

See SPEC-PROCRASTINATE-ZOMBIE-001.
"""

from __future__ import annotations

import datetime
from typing import Any

import structlog

logger = structlog.get_logger()

# Worker rows older than this are considered dead. Default heartbeat interval
# is 10s; a 120s window accommodates a slow-restarting worker without
# false-positive pruning.
STALLED_WORKER_TIMEOUT_SECONDS = 120.0


async def recover_zombie_jobs(proc_app: Any) -> dict[str, int]:
    """Reset jobs orphaned by dead workers back to ``todo``.

    Two-step:
    1. Prune stalled worker rows (heartbeat older than STALLED_WORKER_TIMEOUT_SECONDS).
       FK CASCADE sets ``worker_id = NULL`` on each orphaned job.
    2. Retry every job still in ``doing`` with ``worker_id IS NULL``.

    Returns counts for observability/tests.
    """
    pruned_workers = await proc_app.job_manager.prune_stalled_workers(
        STALLED_WORKER_TIMEOUT_SECONDS
    )
    pruned_count = len(pruned_workers)
    if pruned_count:
        logger.info("procrastinate_pruned_stalled_workers", count=pruned_count)

    rows = await proc_app.connector.execute_query_all_async(
        query="""
            SELECT id, queue_name, task_name
            FROM procrastinate_jobs
            WHERE status = 'doing' AND worker_id IS NULL
        """,
    )
    if not rows:
        logger.info("procrastinate_zombie_recovery_clean")
        return {"workers_pruned": pruned_count, "jobs_retried": 0}

    retry_at = datetime.datetime.now(datetime.UTC)
    retried = 0
    for row in rows:
        job_id = row["id"]
        try:
            await proc_app.job_manager.retry_job_by_id_async(job_id=job_id, retry_at=retry_at)
            retried += 1
        except Exception:
            logger.exception(
                "procrastinate_zombie_retry_failed",
                job_id=job_id,
                queue=row.get("queue_name"),
                task=row.get("task_name"),
            )

    logger.info(
        "procrastinate_zombies_retried",
        workers_pruned=pruned_count,
        jobs_retried=retried,
        jobs_total=len(rows),
    )
    return {"workers_pruned": pruned_count, "jobs_retried": retried}
