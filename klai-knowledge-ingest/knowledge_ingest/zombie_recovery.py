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
- ``ingest_graphiti_episode`` resumes after the last part it stored in ``graphiti_episode_ids``
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

# The unique index procrastinate's own ``procrastinate_retry_job_v2`` UPDATE
# can violate: a newer job already sits in ``todo`` with the same
# ``queueing_lock`` as the zombie being retried, so the zombie cannot go back
# to ``todo``.
QUEUEING_LOCK_UNIQUE_CONSTRAINT = "procrastinate_jobs_queueing_lock_idx_v1"

# The document product a task builds. Every queueing lock of these tasks names
# the artifact (``{org}:{kb}:{path}:{artifact}``, ``reindex:{org}:{artifact}``,
# ``requeue:{artifact}``, ``graphiti:{artifact}``), so a job holding a zombie's
# lock works on the same document version.
_PRODUCT_OF_TASK = {
    "enrich_document_interactive": "enrichment",
    "enrich_document_bulk": "enrichment",
    "ingest_graphiti_episode": "graph",
}


def _product_work(job: Any) -> tuple[str, Any] | None:
    product = _PRODUCT_OF_TASK.get(job.task_name.rsplit(".", 1)[-1])
    return (product, job.task_kwargs.get("artifact_id")) if product else None


async def _queued_twin_covers(proc_app: Any, job: Any, queueing_lock: str | None) -> bool:
    """Whether the live job holding the zombie's queueing lock covers its work.

    For a document product it does when it builds the same product of the same
    artifact, whatever its other arguments: every such job reloads the version
    from Postgres, and it either redoes the zombie's work (a capacity re-queue,
    the same batch, an old-shape job with ``resource_key``) or exists only
    because that work finished (the next batch of a large document is deferred
    after the previous batch wrote its chunks). Any other task keeps its
    earlier rule: the lock holder covers it.
    """
    work = _product_work(job)
    if work is None:
        return True
    twins = await proc_app.job_manager.list_jobs_async(queueing_lock=queueing_lock)
    return any(
        twin.id != job.id and twin.status in ("todo", "doing") and _product_work(twin) == work
        for twin in twins
    )


async def _record_lost_work(job: Any) -> None:
    """A zombie whose work nothing covers: the document says failed."""
    work = _product_work(job)
    if work is None or not work[1]:
        return
    product, artifact_id = work
    from knowledge_ingest.db import cross_org_admin_connection
    from knowledge_ingest.derived_state import FAILED, record_outcome

    async with cross_org_admin_connection() as conn:
        await record_outcome(conn, artifact_id, product, FAILED, "stuck_job_not_covered")


async def recover_zombie_jobs(proc_app: Any) -> dict[str, int]:
    """Reset jobs orphaned by dead workers back to ``todo``.

    Use Procrastinate's heartbeat-aware stalled-job query directly. This
    catches both already-pruned ownerless jobs and jobs whose worker row still
    exists but whose heartbeat expired after this container started.

    Returns counts for observability/tests.
    """
    from procrastinate.exceptions import UniqueViolation
    from procrastinate.jobs import Status

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
        except UniqueViolation as exc:
            if exc.constraint_name != QUEUEING_LOCK_UNIQUE_CONSTRAINT:
                logger.exception(
                    "procrastinate_zombie_retry_failed",
                    job_id=job_id,
                    queue=job.queue,
                    task=job.task_name,
                )
                continue
            covered = await _queued_twin_covers(proc_app, job, exc.queueing_lock)
            try:
                await proc_app.job_manager.finish_job_by_id_async(
                    job_id=job_id,
                    status=Status.ABORTED if covered else Status.FAILED,
                    delete_job=False,
                )
                if not covered:
                    await _record_lost_work(job)
            except Exception:
                # Keep recovering the other zombies in this pass.
                logger.exception(
                    "procrastinate_zombie_finish_failed",
                    job_id=job_id,
                    queue=job.queue,
                    task=job.task_name,
                )
                continue
            (logger.info if covered else logger.error)(
                "zombie_superseded_by_queued_job" if covered else "zombie_work_not_covered",
                job_id=job_id,
                queue=job.queue,
                task=job.task_name,
                queueing_lock=exc.queueing_lock,
            )
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
