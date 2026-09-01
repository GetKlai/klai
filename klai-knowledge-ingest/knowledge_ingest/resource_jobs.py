"""Connector-scoped Procrastinate job ownership helpers."""

from dataclasses import dataclass
from typing import Any

import structlog

from knowledge_ingest.queues import CRAWL_JOBS, ENRICH_BULK, GRAPHITI_BULK

CONNECTOR_WRITER_QUEUES = (CRAWL_JOBS, ENRICH_BULK, GRAPHITI_BULK)
RESOURCE_JOB_STATUSES = ("todo", "doing", "cancelled", "aborted", "failed", "succeeded")

logger = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class ConnectorResource:
    org_id: str
    kb_slug: str
    connector_id: str
    generation: str


@dataclass(frozen=True, slots=True)
class ResourceJobCancellation:
    jobs_found: int
    jobs_cancelled: int
    jobs_failed_to_cancel: int


def _validate_component(name: str, value: object) -> str:
    component = str(value)
    if not component or ":" in component:
        raise ValueError(f"connector resource key {name} must be non-empty and contain no ':'")
    return component


def connector_resource_key(
    org_id: object,
    kb_slug: object,
    connector_id: object,
    generation: object,
) -> str:
    """Build the canonical authority key for one connector generation."""
    parts = (
        _validate_component("org_id", org_id),
        _validate_component("kb_slug", kb_slug),
        _validate_component("connector_id", connector_id),
        _validate_component("generation", generation),
    )
    return "connector:" + ":".join(parts)


def parse_connector_resource_key(resource_key: str) -> ConnectorResource:
    """Validate and split a canonical connector resource key."""
    parts = resource_key.split(":")
    if len(parts) != 5 or parts[0] != "connector" or any(not part for part in parts[1:]):
        raise ValueError("invalid connector resource key")
    return ConnectorResource(*parts[1:])


async def list_live_jobs_by_resource_key(
    pool: Any,
    resource_key: str,
    queues: tuple[str, ...] = CONNECTOR_WRITER_QUEUES,
) -> list[int]:
    """List live writer jobs owned by exactly one connector generation."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id
              FROM procrastinate_jobs
             WHERE queue_name = ANY($1::text[])
               AND status IN ('todo', 'doing')
               AND args->>'resource_key' = $2
            """,
            list(queues),
            resource_key,
        )
    return [int(row["id"]) for row in rows]


async def cancel_jobs_by_resource_key(
    proc_app: Any,
    pool: Any,
    resource_key: str,
    queues: tuple[str, ...] = CONNECTOR_WRITER_QUEUES,
) -> ResourceJobCancellation:
    """Request cancellation without deleting the auditable job rows."""
    job_ids = await list_live_jobs_by_resource_key(pool, resource_key, queues)
    cancelled = 0
    failed = 0
    for job_id in job_ids:
        try:
            was_cancelled = await proc_app.job_manager.cancel_job_by_id_async(
                job_id,
                abort=True,
                delete_job=False,
            )
            if was_cancelled:
                cancelled += 1
            else:
                failed += 1
        except Exception:
            failed += 1
            logger.warning(
                "connector_resource_job_cancel_failed",
                resource_key=resource_key,
                job_id=job_id,
                exc_info=True,
            )
    report = ResourceJobCancellation(len(job_ids), cancelled, failed)
    logger.info(
        "connector_resource_jobs_cancel_requested",
        resource_key=resource_key,
        jobs_found=report.jobs_found,
        jobs_cancelled=report.jobs_cancelled,
        jobs_failed_to_cancel=report.jobs_failed_to_cancel,
    )
    return report


async def get_resource_job_counts(pool: Any, resource_key: str) -> dict[str, int]:
    """Return raw Procrastinate 3.x statuses and delete/rebuild buckets."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT status::text AS status, count(*)::bigint AS count
              FROM procrastinate_jobs
             WHERE args->>'resource_key' = $1
               AND status::text = ANY($2::text[])
             GROUP BY status
            """,
            resource_key,
            list(RESOURCE_JOB_STATUSES),
        )
    counts = {status: 0 for status in RESOURCE_JOB_STATUSES}
    for row in rows:
        counts[str(row["status"])] = int(row["count"])
    counts["pending"] = counts["todo"]
    counts["running"] = counts["doing"]
    counts["terminal"] = sum(
        counts[status] for status in ("cancelled", "aborted", "failed", "succeeded")
    )
    counts["failed_visible"] = counts["failed"]
    return counts
