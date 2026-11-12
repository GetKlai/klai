"""Read-only consistency reconciliation: PG <-> Qdrant, and PG <-> the graph.

Shadow detector for GAP-SYNC-01. It logs discrepancies but never repairs,
deletes, or re-enqueues anything.
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any

import structlog

from knowledge_ingest import graph as graph_module
from knowledge_ingest import pg_store, qdrant_store, queues
from knowledge_ingest.config import settings
from knowledge_ingest.db import cross_org_admin_connection

logger = structlog.get_logger()

_SCROLL_BATCH = 500
_MAX_SAMPLE = 20
# Artifacts created or closed within this window are excluded from the diff:
# their Qdrant writes may legitimately still be in flight, and flagging them
# would make the nightly alert flap on ordinary ingest activity.
_RECENT_WRITE_TOLERANCE_S = 900
# Bound the whole reconcile pass so a hung Qdrant scroll cannot wedge a
# worker slot all night. A timeout surfaces as status=error (alerted).
_RECONCILE_TIMEOUT_S = 900


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
    recent_keys: set[ArtifactKey] | None = None,
) -> dict[str, Any]:
    # Only a synced artifact promises chunks in Qdrant. Any active artifact
    # may own them, though: a direct upload whose enrichment failed keeps its
    # raw chunks by design and stays searchable, so its points are not orphans.
    active_keys: set[ArtifactKey] = set()
    pg_keys: set[ArtifactKey] = set()
    for artifact in pg_artifacts:
        if (key := _artifact_key_from_mapping(artifact)) is not None:
            active_keys.add(key)
            if artifact["index_status"] == "synced":
                pg_keys.add(key)
    qdrant_keys = set(qdrant_artifact_counts)
    missing_in_qdrant = pg_keys - qdrant_keys
    orphaned_in_qdrant = qdrant_keys - active_keys
    if recent_keys:
        # Points belonging to just-created or just-superseded artifacts are
        # in-flight ingest activity, not drift.
        missing_in_qdrant -= recent_keys
        orphaned_in_qdrant -= recent_keys

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


async def lost_graph_extractions(conn: Any, org_ids: set[str]) -> dict[str, Any]:
    """Active rows whose recorded, finished extraction has an episode the graph lacks.

    FalkorDB persists by RDB snapshot only, so a crash or a kill during the
    shutdown save loses every write since the last snapshot while Postgres
    already records the extraction as complete: in one organisation 290 of
    10,922 recorded episode ids were missing, clustered on restart days.
    Retrieval then has no facts for the document and nothing says so.

    A row created within ``graph.ORPHAN_GRACE`` (24 h, the graph job's retry
    span) is left out, the same tolerance the orphan sweep uses for work in
    flight. A graph that cannot be read raises: an unreadable graph is an error
    of the check, never a count of missing episodes. Ids only in the sample.
    """
    cutoff = time.time() - graph_module.ORPHAN_GRACE.total_seconds()
    lost: list[tuple[str, str]] = []
    for org_id in sorted(org_ids):
        rows = [
            row
            for row in await pg_store.get_graph_rows_for_org(conn, org_id)
            if row["active"]
            and row["finished"]
            and row["episode_ids"]
            and row["created_at"] <= cutoff
        ]
        if not rows:
            continue
        stored = await graph_module.stored_episode_uuids(org_id)
        lost += [(org_id, row["id"]) for row in rows if not set(row["episode_ids"]) <= stored]
    return {
        "graph_lost_extractions": len(lost),
        "graph_lost_by_org": dict(Counter(org_id for org_id, _ in lost)),
        "graph_lost_sample": [
            {"org_id": org_id, "artifact_id": artifact_id}
            for org_id, artifact_id in lost[:_MAX_SAMPLE]
        ],
    }


async def reconcile_pg_qdrant() -> dict[str, Any]:
    """Compare active PG artifacts with distinct Qdrant artifact payloads."""
    try:
        async with asyncio.timeout(_RECONCILE_TIMEOUT_S):
            cutoff = int(time.time()) - _RECENT_WRITE_TOLERANCE_S
            async with cross_org_admin_connection() as conn:
                pg_artifacts = await pg_store.list_active_artifacts(conn, created_before=cutoff)
                recent_artifacts = await pg_store.list_recent_artifact_keys(conn, since=cutoff)
                graph_report: dict[str, Any] = {"graph_check": "disabled"}
                if settings.graphiti_enabled:
                    org_ids = {str(artifact["org_id"]) for artifact in pg_artifacts}
                    try:
                        graph_report = {
                            "graph_check": "ok",
                            **await lost_graph_extractions(conn, org_ids),
                        }
                    except Exception as exc:
                        logger.exception("graph_lost_extractions_check_failed")
                        graph_report = {"graph_check": "error", "graph_error": str(exc)}
            qdrant_artifact_counts = await _fetch_qdrant_artifact_counts()
    except Exception as exc:
        # A crashed reconcile must still emit the pg_qdrant_reconcile event,
        # otherwise the Grafana alert (which matches status:failed OR
        # status:error) silently never fires (adversarial review 2026-06-11).
        logger.exception("pg_qdrant_reconcile", status="error", error=str(exc))
        raise

    recent_keys = {
        key
        for artifact in recent_artifacts
        if (key := _artifact_key_from_mapping(artifact)) is not None
    }
    report = _diff_inventory(pg_artifacts, qdrant_artifact_counts, recent_keys) | graph_report
    if graph_report["graph_check"] == "error":
        status = "error"
    elif report["discrepancies_total"] or report.get("graph_lost_extractions"):
        status = "failed"
    else:
        status = "ok"

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
        # MAINTENANCE: its own worker slot for control work. Not the I/O lane,
        # where the full-collection Qdrant scroll held user-triggered ingest
        # behind it for minutes (adversarial review 2026-06-11), and not the
        # LLM lane (RAG_EVAL), where procrastinate takes the oldest job across
        # enrich-bulk and graphiti-bulk: the 03:30 run waited 4 to 9 hours
        # behind bulk LLM work (finished 07:56-12:08 from 2026-10-19 to 10-23).
        # The scroll uses no LLM; holding the slot for minutes only delays the
        # next zombie-recovery pass by as much.
        queue=queues.MAINTENANCE,
        retry=procrastinate.RetryStrategy(max_attempts=1),
        queueing_lock="pg-qdrant-reconcile",
    )
    async def reconcile_pg_qdrant_periodic(timestamp: int) -> dict[str, Any]:
        logger.info("pg_qdrant_reconcile_periodic_fired", deferrer_ts=timestamp)
        return await reconcile_pg_qdrant()

    procrastinate_app.reconcile_pg_qdrant_periodic = reconcile_pg_qdrant_periodic  # type: ignore[attr-defined]
