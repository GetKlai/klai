"""Stalled-job recovery for procrastinate workers.

Procrastinate v3 detects stalled workers via heartbeat (`procrastinate_workers.last_heartbeat`)
and prunes their rows on the next worker startup. The FK
`procrastinate_jobs.worker_id → procrastinate_workers.id ON DELETE SET NULL`
sets `worker_id = NULL` on jobs whose owning worker disappeared.

But procrastinate does NOT reset those jobs — their status stays `doing` forever.
On every hard container kill that happens mid-task, one or more jobs can become
permanent zombies.
After enough deploys the worker concurrency slots fill up and enrichment stalls.

This module fills the gap: at lifespan startup, it prunes stalled worker rows
and retries every job in `doing` status whose owner is gone.

Safe to run because every task this worker handles is retry-safe:
- ``ingest_graphiti_episode`` dedups via Episode UUID
- ``enrich_document_bulk`` dedups via content_hash + artifact_id
- ``connector_purge_task`` is fully idempotent by design (SPEC-CONNECTOR-DELETE-LIFECYCLE-001)
- ``run_crawl`` resumes from a generation-fenced durable checkpoint

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

    Use Procrastinate's heartbeat-aware stalled-job query directly. This
    catches both already-pruned ownerless jobs and jobs whose worker row still
    exists but whose heartbeat expired after this container started.

    Returns counts for observability/tests.
    """
    jobs = list(
        await proc_app.job_manager.get_stalled_jobs(
            seconds_since_heartbeat=STALLED_WORKER_TIMEOUT_SECONDS
        )
    )
    if not jobs:
        logger.info("procrastinate_zombie_recovery_clean")
        return {"jobs_retried": 0}

    retry_at = datetime.datetime.now(datetime.UTC)
    retried = 0
    for job in jobs:
        job_id = job.id
        try:
            await proc_app.job_manager.retry_job_by_id_async(job_id=job_id, retry_at=retry_at)
            retried += 1
        except Exception:
            logger.exception(
                "procrastinate_zombie_retry_failed",
                job_id=job_id,
                queue=job.queue,
                task=job.task_name,
            )

    logger.info(
        "procrastinate_zombies_retried",
        jobs_retried=retried,
        jobs_total=len(jobs),
    )
    return {"jobs_retried": retried}


def register_zombie_recovery_task(procrastinate_app: Any) -> None:
    """Register the minute-level recovery pass on an unstarvable queue."""
    import procrastinate

    from knowledge_ingest import queues

    @procrastinate_app.periodic(
        cron="* * * * *",
        periodic_id="stalled-job-recovery",
    )
    @procrastinate_app.task(
        name="knowledge_ingest.zombie_recovery.recover_stalled_jobs_periodic",
        queue=queues.MAINTENANCE,
        retry=procrastinate.RetryStrategy(max_attempts=1),
        queueing_lock="stalled-job-recovery",
    )
    async def recover_stalled_jobs_periodic(timestamp: int) -> dict[str, int]:
        logger.info("procrastinate_zombie_recovery_periodic_fired", deferrer_ts=timestamp)
        return await recover_zombie_jobs(procrastinate_app)

    procrastinate_app.recover_stalled_jobs_periodic = recover_stalled_jobs_periodic
