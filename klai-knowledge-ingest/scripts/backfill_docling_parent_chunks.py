"""Backfill parent_chunks rows for docling-prechunked artifacts.

Context (incident 2026-05-28):

Before fix/docling-parent-chunks, the ingest.py ``skip_chunking`` branch
only populated ``texts`` and never set ``parents_serialised`` /
``parent_index_per_child``, so the ``insert_parent_chunks`` block at the
bottom of ``ingest_document`` was silently skipped for every
docling-prechunked PDF upload. Result: Qdrant got the chunks (visible
via ``upsert_chunks``) but ``knowledge.parent_chunks`` stayed empty for
the artifact. ``retrieval-api``'s child→parent lookup returned nothing,
the LLM never saw the relevant context, and chat answers hallucinated.

This script repairs the existing rows so chat starts working immediately
for every tenant that uploaded docling PDFs since SPEC-KB-FILE-UPLOAD-001
landed:

1. Find every active artifact where ``extra->>'pipeline' = 'docling'``.
2. Pull the Qdrant child points for the artifact (ordered by
   ``chunk_index``), extract the ``text`` payload.
3. INSERT each child as its own parent_chunk row when missing (1:1,
   matching the in-code fix's semantic: every docling chunk IS its own
   semantic parent — Docling already chunks at paragraph/section granularity).
4. UPDATE the Qdrant points so each child's payload carries
   ``parent_chunk_id`` pointing to the new pg row. Without step 4 the
   retrieval-api's parent-lookup still fails because the Qdrant payload
   has no link to the new pg rows.

Idempotent and retryable: re-running on an artifact that already has
parent_chunks re-applies the Qdrant ``parent_chunk_id`` payload patch.
This repairs half-failed runs where PostgreSQL inserts succeeded but the
Qdrant payload update failed.

Run from inside the knowledge-ingest container:

    docker exec klai-core-knowledge-ingest-1 \\
        python scripts/backfill_docling_parent_chunks.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request
from pathlib import Path

import asyncpg
import structlog

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from knowledge_ingest.config import settings

logger = structlog.get_logger()


_QDRANT_COLLECTION = "klai_knowledge"


def _qdrant_scroll(org_id: str, artifact_id: str) -> list[dict]:
    """Return every Qdrant point for ``artifact_id``, ordered by chunk_index.

    Uses urllib (no extra deps) and the QDRANT_API_KEY env var that the
    knowledge-ingest container already has. Each point has ``id`` and
    ``payload`` with at least ``text`` and ``chunk_index``.
    """
    api_key = os.environ.get("QDRANT_API_KEY") or ""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["api-key"] = api_key

    body = {
        "limit": 500,
        "filter": {
            "must": [
                {"key": "org_id", "match": {"value": org_id}},
                {"key": "artifact_id", "match": {"value": artifact_id}},
            ]
        },
        "with_payload": True,
        "with_vector": False,
    }
    points: list[dict] = []
    next_offset: str | None = None
    while True:
        if next_offset is not None:
            body["offset"] = next_offset
        req = urllib.request.Request(  # noqa: S310
            f"{settings.qdrant_url}/collections/{_QDRANT_COLLECTION}/points/scroll",
            data=json.dumps(body).encode(),
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            data = json.loads(resp.read())
        result = data.get("result", {})
        page = result.get("points") or []
        points.extend(page)
        next_offset = result.get("next_page_offset")
        if not next_offset:
            break
    points.sort(key=lambda p: int((p.get("payload") or {}).get("chunk_index") or 0))
    return points


def _qdrant_set_parent_chunk_ids(
    point_id_to_parent_id: dict[str, int],
) -> None:
    """Batch-update each Qdrant point's payload with parent_chunk_id.

    Qdrant exposes a per-point ``set_payload`` endpoint; we send one
    request per point (the bulk endpoint requires identical payloads
    across points which we don't have here). N≈18 per artifact in the
    Jantine case so the per-point overhead is fine.
    """
    api_key = os.environ.get("QDRANT_API_KEY") or ""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["api-key"] = api_key

    for point_id, parent_id in point_id_to_parent_id.items():
        body = {"payload": {"parent_chunk_id": parent_id}, "points": [point_id]}
        req = urllib.request.Request(  # noqa: S310
            f"{settings.qdrant_url}/collections/{_QDRANT_COLLECTION}/points/payload",
            data=json.dumps(body).encode(),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            resp.read()


async def _existing_parent_chunk_ids(
    conn: asyncpg.Connection,
    artifact_id: str,
) -> list[int]:
    rows = await conn.fetch(
        """
        SELECT id
        FROM knowledge.parent_chunks
        WHERE artifact_id = $1
        ORDER BY position ASC, id ASC
        """,
        artifact_id,
    )
    return [int(row["id"]) for row in rows]


async def _backfill_one_artifact(
    conn: asyncpg.Connection,
    org_id: str,
    kb_slug: str,
    artifact_id: str,
) -> tuple[int, str]:
    points = _qdrant_scroll(org_id, artifact_id)
    if not points:
        return 0, "no_qdrant_points"

    # Build the parents list from Qdrant payload text. Each point is its own
    # parent (1:1, matching the in-code fix semantic for docling uploads).
    parents = []
    point_ids_in_order: list[str] = []
    for i, point in enumerate(points):
        text = (point.get("payload") or {}).get("text") or ""
        if not text:
            continue
        parents.append(
            {
                "text": text,
                "token_count": max(
                    1, len(text) // 4
                ),  # rough — matches chunker._approx_token_count
                "position": i,
            }
        )
        point_ids_in_order.append(str(point["id"]))

    if not parents:
        return 0, "qdrant_points_had_no_text"

    # Set tenant context so RLS lets the inserts through.
    await conn.execute("SELECT set_config('app.current_org_id', $1, false)", org_id)

    existing_ids = await _existing_parent_chunk_ids(conn, artifact_id)
    if existing_ids:
        if len(existing_ids) != len(point_ids_in_order):
            return 0, "parent_chunk_count_mismatch"
        expected_mapping = dict(zip(point_ids_in_order, existing_ids, strict=True))
        already_repaired = all(
            int((point.get("payload") or {}).get("parent_chunk_id") or 0)
            == expected_mapping[str(point["id"])]
            for point in points
            if str(point["id"]) in expected_mapping
        )
        if already_repaired:
            return 0, "already_repaired"
        _qdrant_set_parent_chunk_ids(expected_mapping)
        return 0, "qdrant_parent_ids_repaired"

    # Insert and capture ids. asyncpg returns RECORD per row.
    inserted_ids: list[int] = []
    async with conn.transaction():
        for p in parents:
            row_id = await conn.fetchval(
                """
                INSERT INTO knowledge.parent_chunks
                    (artifact_id, org_id, text, token_count, position)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id
                """,
                artifact_id,
                org_id,
                p["text"],
                int(p["token_count"]),
                int(p["position"]),
            )
            inserted_ids.append(int(row_id))

    # Update each Qdrant point's payload with its parent_chunk_id.
    point_id_to_parent_id = dict(zip(point_ids_in_order, inserted_ids, strict=True))
    _qdrant_set_parent_chunk_ids(point_id_to_parent_id)

    logger.info(
        "backfilled_artifact",
        org_id=org_id,
        kb_slug=kb_slug,
        artifact_id=artifact_id,
        parent_chunks_inserted=len(inserted_ids),
    )
    return len(inserted_ids), "ok"


async def _list_docling_artifacts_without_parents(
    conn: asyncpg.Connection,
) -> list[tuple[str, str, str]]:
    """Return ``(org_id, kb_slug, artifact_id)`` for every artifact that:

    - was ingested via docling (``extra->>'pipeline' = 'docling'``)
    - is the current active row (``belief_time_end = sentinel``)

    The function name is kept for older operator notes; it now returns all
    active docling artifacts so reruns can repair Qdrant payloads after a
    partial failure.
    """
    rows = await conn.fetch(
        """
        SELECT a.org_id, a.kb_slug, a.id::text AS artifact_id
        FROM knowledge.artifacts a
        WHERE a.extra::jsonb->>'pipeline' = 'docling'
          AND a.belief_time_end = '253402300800'
        ORDER BY a.org_id, a.kb_slug, a.created_at DESC
        """
    )
    return [(r["org_id"], r["kb_slug"], r["artifact_id"]) for r in rows]


def _parse_pg_dsn(dsn: str) -> dict[str, object]:
    """Structural DSN parser — asyncpg's urlparse-based parser chokes on
    passwords with reserved chars (``:``, ``/``, ``+``, ``@``). Same pitfall
    as ``redis-url-password-must-be-parsed-manually``. We take the password
    as opaque bytes between the first ``:`` after the scheme and the LAST
    ``@`` before the host.
    """
    rest = dsn.split("://", 1)[1] if "://" in dsn else dsn
    creds, hostpart = rest.rsplit("@", 1)
    user, password = creds.split(":", 1)
    host_and_port, _, database = hostpart.partition("/")
    host, _, port_s = host_and_port.partition(":")
    return {
        "user": user,
        "password": password,
        "host": host,
        "port": int(port_s) if port_s else 5432,
        "database": database or user,
    }


async def main() -> int:
    # Connect as klai superuser to bypass RLS for the cross-org listing,
    # then set tenant context per-artifact for the actual inserts.
    dsn = (
        os.environ.get("KLAI_SUPERUSER_DSN")
        or os.environ.get("POSTGRES_DSN")
        or os.environ.get("DATABASE_URL")
    )
    if not dsn:
        print(
            "Set KLAI_SUPERUSER_DSN (or POSTGRES_DSN/DATABASE_URL) before running.",
            file=sys.stderr,
        )
        return 1
    # Strip SQLAlchemy's +asyncpg suffix; we use asyncpg directly here.
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")

    conn = await asyncpg.connect(**_parse_pg_dsn(dsn))
    try:
        targets = await _list_docling_artifacts_without_parents(conn)
        print(f"Found {len(targets)} active docling artifacts to verify/backfill.")

        total_inserted = 0
        outcomes: dict[str, int] = {}
        for org_id, kb_slug, artifact_id in targets:
            try:
                inserted, status = await _backfill_one_artifact(conn, org_id, kb_slug, artifact_id)
            except Exception as exc:
                logger.exception(
                    "backfill_artifact_failed",
                    org_id=org_id,
                    kb_slug=kb_slug,
                    artifact_id=artifact_id,
                )
                outcomes["error"] = outcomes.get("error", 0) + 1
                print(f"  FAIL {artifact_id}: {exc}", file=sys.stderr)
                continue
            outcomes[status] = outcomes.get(status, 0) + 1
            total_inserted += inserted

        print(f"Total parent_chunks rows inserted: {total_inserted}")
        print("Per-status breakdown:")
        for status, count in sorted(outcomes.items()):
            print(f"  {status}: {count}")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
