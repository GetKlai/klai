"""Read-only PG <-> Qdrant consistency reconciliation.

Shadow detector for GAP-SYNC-01. It logs discrepancies but never repairs,
deletes, or re-enqueues anything.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

import structlog

from knowledge_ingest import pg_store, qdrant_store, queues
from knowledge_ingest.db import cross_org_admin_connection

logger = structlog.get_logger()

_SCROLL_BATCH = 500
_MAX_SAMPLE = 20


@dataclass(frozen=True, order=True)
class ArtifactKey:
    org_id: str
    kb_slug: str
    path: str
    artifact_id: str


def _artifact_key_from_mapping(payload: dict[str, Any]) -> ArtifactKey | None:
    values = {
        "org_id": payload.get("org_id"),
        "kb_slug": payload.get("kb_slug"),
        "path": payload.get("path"),
        "artifact_id": payload.get("artifact_id"),
    }
    if any(value in (None, "") for value in values.values()):
        return None
    return ArtifactKey(
        org_id=str(values["org_id"]),
        kb_slug=str(values["kb_slug"]),
        path=str(values["path"]),
        artifact_id=str(values["artifact_id"]),
    )


def _sample(keys: set[ArtifactKey]) -> list[dict[str, str]]:
    return [
        {
            "org_id": key.org_id,
            "kb_slug": key.kb_slug,
            "path": key.path,
            "artifact_id": key.artifact_id,
        }
        for key in sorted(keys)[:_MAX_SAMPLE]
    ]


def _diff_inventory(
    pg_artifacts: list[dict[str, Any]],
    qdrant_artifact_counts: Counter[ArtifactKey],
) -> dict[str, Any]:
    pg_keys = {
        key
        for artifact in pg_artifacts
        if (key := _artifact_key_from_mapping(artifact)) is not None
    }
    qdrant_keys = set(qdrant_artifact_counts)
    missing_in_qdrant = pg_keys - qdrant_keys
    orphaned_in_qdrant = qdrant_keys - pg_keys

    return {
        "pg_active_artifacts": len(pg_keys),
        "qdrant_artifacts": len(qdrant_keys),
        "qdrant_points": sum(qdrant_artifact_counts.values()),
        "missing_in_qdrant": len(missing_in_qdrant),
        "orphaned_in_qdrant": len(orphaned_in_qdrant),
        "discrepancies_total": len(missing_in_qdrant) + len(orphaned_in_qdrant),
        "missing_sample": _sample(missing_in_qdrant),
        "orphaned_sample": _sample(orphaned_in_qdrant),
    }


async def _fetch_qdrant_artifact_counts() -> Counter[ArtifactKey]:
    client = qdrant_store.get_client()
    counts: Counter[ArtifactKey] = Counter()
    offset = None

    while True:
        points, next_offset = await client.scroll(
            collection_name=qdrant_store.COLLECTION,
            limit=_SCROLL_BATCH,
            offset=offset,
            with_payload=["org_id", "kb_slug", "path", "artifact_id"],
            with_vectors=False,
        )
        if not points:
            break

        for point in points:
            payload = point.payload or {}
            if key := _artifact_key_from_mapping(payload):
                counts[key] += 1

        if next_offset is None:
            break
        offset = next_offset

    return counts


async def reconcile_pg_qdrant() -> dict[str, Any]:
    """Compare active synced PG artifacts with distinct Qdrant artifact payloads."""
    async with cross_org_admin_connection() as conn:
        pg_artifacts = await pg_store.list_active_synced_artifacts(conn)

    qdrant_artifact_counts = await _fetch_qdrant_artifact_counts()
    report = _diff_inventory(pg_artifacts, qdrant_artifact_counts)
    status = "ok" if report["discrepancies_total"] == 0 else "failed"

    logger.info(
        "pg_qdrant_reconcile",
        status=status,
        **report,
    )
    return {"status": status, **report}


def register_consistency_reconcile_task(procrastinate_app: Any) -> None:
    """Register the nightly read-only PG <-> Qdrant reconciliation task."""
    import procrastinate

    @procrastinate_app.periodic(
        cron="30 3 * * *",
        periodic_id="pg-qdrant-reconcile",
    )
    @procrastinate_app.task(
        name="knowledge_ingest.consistency_reconcile.reconcile_pg_qdrant_periodic",
        queue=queues.INGEST_KB,
        retry=procrastinate.RetryStrategy(max_attempts=1),
        queueing_lock="pg-qdrant-reconcile",
    )
    async def reconcile_pg_qdrant_periodic(timestamp: int) -> dict[str, Any]:
        logger.info("pg_qdrant_reconcile_periodic_fired", deferrer_ts=timestamp)
        return await reconcile_pg_qdrant()

    procrastinate_app.reconcile_pg_qdrant_periodic = reconcile_pg_qdrant_periodic  # type: ignore[attr-defined]
