"""Operator script: repair the artifacts the nightly PG/Qdrant reconcile reports.

Takes artifact ids from the ``missing_sample`` / ``orphaned_sample`` of the
``pg_qdrant_reconcile`` event, re-reads the current state of each in Postgres
and Qdrant, and picks the repair from that state, never from the report:

    reindex        active synced row, document text on the row, no points.
                   Runs the operator rebuild of one artifact
                   (``rebuild_tasks._rebuild_artifact``, which calls the
                   enrichment body the pipeline uses) and then counts the
                   points; zero points afterwards is a failure.
    retire         active synced row, no document text, no points. Nothing
                   exists to index it from, so the row is closed with
                   one conditional UPDATE on its id while it is still active.
    delete-points  points whose artifact has no active row. The points are
                   deleted by id.
    skip           anything else, with the reason. An active row that owns
                   points (e.g. index_status=failed with raw chunks) is a
                   live document and is never touched.

Dry run is the default and changes nothing:

    docker exec klai-core-knowledge-ingest-1 \\
        python -m knowledge_ingest.scripts.repair_pg_qdrant_drift <artifact_id> ...

    docker exec klai-core-knowledge-ingest-1 \\
        python -m knowledge_ingest.scripts.repair_pg_qdrant_drift --execute <artifact_id> ...

The process exits non-zero when any executed repair failed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

from qdrant_client.models import FieldCondition, Filter, MatchValue, PointIdsList

from knowledge_ingest import pg_store, qdrant_store
from knowledge_ingest.db import cross_org_admin_connection, tenant_scoped_connection


async def _point_ids(artifact_id: str) -> list[str]:
    ids: list[str] = []
    offset = None
    while True:
        points, offset = await qdrant_store.get_client().scroll(
            qdrant_store.COLLECTION,
            scroll_filter=Filter(
                must=[FieldCondition(key="artifact_id", match=MatchValue(value=artifact_id))]
            ),
            limit=500,
            offset=offset,
            with_payload=False,
            with_vectors=False,
        )
        ids += [str(p.id) for p in points]
        if offset is None:
            return ids


async def _point_orgs(point_ids: list[str]) -> set[str]:
    points = await qdrant_store.get_client().retrieve(
        qdrant_store.COLLECTION, ids=point_ids, with_payload=["org_id"]
    )
    return {str((p.payload or {}).get("org_id")) for p in points}


async def _row(artifact_id: str) -> dict | None:
    async with cross_org_admin_connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT id::text AS artifact_id, org_id, kb_slug, path, index_status,
                   belief_time_end = $2 AS active,
                   coalesce(extra->>'document_text', '') <> '' AS has_text
            FROM knowledge.artifacts WHERE id = $1::uuid
            """,
            artifact_id,
            pg_store._SENTINEL,
        )
    return dict(row) if row else None


def _plan(row: dict | None, point_ids: list[str]) -> tuple[str, str]:
    if row is None or not row["active"]:
        if point_ids:
            return "delete-points", "no active row owns these points"
        return "skip", "no active row and no points"
    if point_ids:
        return "skip", f"active row owns its points (index_status={row['index_status']})"
    if row["index_status"] != "synced":
        return "skip", f"index_status={row['index_status']} does not promise points"
    if row["has_text"]:
        return "reindex", "synced row without points; document text on the row"
    return "retire", "synced row without points and without document text"


async def _reindex(row: dict) -> None:
    from knowledge_ingest import rebuild_tasks

    async with cross_org_admin_connection() as conn:
        artifact = await conn.fetchrow(
            """
            SELECT id, path, content_type, extra, synthesis_depth,
                   belief_time_start, belief_time_end, user_id
            FROM knowledge.artifacts WHERE id = $1::uuid
            """,
            row["artifact_id"],
        )
    outcome = await rebuild_tasks._rebuild_artifact(
        org_id=row["org_id"],
        kb_slug=row["kb_slug"],
        artifact=dict(artifact),
        semaphore=asyncio.Semaphore(1),
    )
    if outcome != "processed":
        raise RuntimeError(f"rebuild {outcome}")
    if not await _point_ids(row["artifact_id"]):
        raise RuntimeError("reindex wrote no points")


async def _retire(row: dict) -> None:
    # One conditional statement on the planned id: a newer version of the
    # same path that became active after the plan is never closed.
    async with tenant_scoped_connection(row["org_id"]) as conn:
        closed = await conn.fetchrow(
            """
            UPDATE knowledge.artifacts SET belief_time_end = $4
            WHERE id = $1::uuid AND org_id = $2 AND belief_time_end = $3
              AND index_status = 'synced'
              AND coalesce(extra->>'document_text', '') = ''
            RETURNING id
            """,
            row["artifact_id"],
            row["org_id"],
            pg_store._SENTINEL,
            int(time.time()),
        )
    if closed is None:
        raise RuntimeError("artifact is no longer an active synced row without text")


async def _delete_points(point_ids: list[str]) -> None:
    await qdrant_store.get_client().delete(
        qdrant_store.COLLECTION, points_selector=PointIdsList(points=point_ids)
    )
    if await qdrant_store.get_client().retrieve(qdrant_store.COLLECTION, ids=point_ids):
        raise RuntimeError("points still present after delete")


async def main(artifact_ids: list[str], execute: bool) -> int:
    failures = 0
    for artifact_id in artifact_ids:
        row = await _row(artifact_id)
        point_ids = await _point_ids(artifact_id)
        action, reason = _plan(row, point_ids)
        orgs = {row["org_id"]} if row else await _point_orgs(point_ids) if point_ids else set()
        line = {
            "artifact_id": artifact_id,
            "org_id": ",".join(sorted(orgs)),
            "kb_slug": row["kb_slug"] if row else None,
            "action": action,
            "reason": reason,
            "points": len(point_ids),
        }
        if action == "delete-points":
            line["point_ids"] = point_ids
        if execute and action != "skip":
            try:
                if action == "reindex":
                    await _reindex(row)
                elif action == "retire":
                    await _retire(row)
                else:
                    await _delete_points(point_ids)
                line["result"] = "done"
            except Exception as exc:
                failures += 1
                line["result"] = f"failed: {exc}"
        print(json.dumps(line), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("artifact_ids", nargs="+")
    parser.add_argument(
        "--execute", action="store_true", help="Apply the repairs (default: dry run)."
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.artifact_ids, args.execute)))
