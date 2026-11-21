"""Report, and only on request purge, documents that were deleted but never removed.

Until delete meant gone, most delete paths only closed the artifact row: the
full text stayed in ``extra->>'document_text'`` and the parent chunks, image
rows and graph episodes stayed with it. This script finds those documents and,
with ``--execute``, removes them through the same operation every live delete
uses (``knowledge_ingest.document_purge.purge_documents``).

Classification
--------------
A *head* is a row nothing replaced (``superseded_by IS NULL``). Every closed
row hangs below exactly one head through ``superseded_by``. A chain whose head
is active is a document that still exists: its closed rows are replaced
versions and are kept. A chain whose head is closed is a candidate.

``superseded_by`` alone is not proof of deletion. The old crawler stale
cleanup and the old page cleanup set ``superseded_by = NULL`` on rows that
pointed at the rows they retired, and rows written before the link existed
were never linked, so "closed and unlinked" also covers older versions of a
document that is still live. A candidate therefore counts as **deleted** only
when no active row in the same organisation and knowledge base has its
document identity: the same path, or, for a connector document, the same
``(source_connector_id, source_ref)``. A candidate whose identity is still live
is reported as ``kept_unlinked`` and never purged.

Closing is also what the drift repair does when it retires a row
(``knowledge_ingest/scripts/repair_pg_qdrant_drift.py``), and the portal keeps
showing such an upload as not synced with a reindex action, so a retired row
is a document a user still sees. The retire step only closes rows that carry
no ``document_text``; every ingest since the text was stored on the row writes
it, and the delete paths never removed it. So a candidate counts as deleted
only when its head still has its ``document_text``, or carries the
``deleting`` status a delete that did not finish leaves behind. A candidate
with neither is reported as ``closed_without_text``: retired by the drift
repair, or deleted before the text was stored on the row. Nothing on the row
tells those apart, so ``--execute`` never touches that class.

Known uncertainty: a document whose identity changed in a way neither the
path nor the connector ``source_ref`` records (a connector without stable
``source_ref`` whose page was renamed, and whose old version then lost its
link) is counted as deleted although a successor exists. Its successor is not
touched; only the unlinked old rows go, which is the history of that rename.

Usage (in a knowledge-ingest container)::

    # dry run, every organisation or one
    python -m scripts.purge_deleted_documents [--org <zitadel_org_id>]
    # purge one organisation, only after the dry run was approved
    python -m scripts.purge_deleted_documents --org <zitadel_org_id> --execute

Never run this from startup, a schedule or another job: each execute run is
approved separately. Object-storage images of deleted documents are not
removed (known gap; the image rows go with the artifact rows).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from dataclasses import dataclass, field

import asyncpg
import structlog

from knowledge_ingest import document_purge
from knowledge_ingest.db import close_pool, cross_org_admin_connection, tenant_scoped_connection
from knowledge_ingest.operations import guard_cli, kb_operation
from knowledge_ingest.pg_store import PURGE_PENDING

logger = structlog.get_logger()

_SENTINEL = 253402300800
# Documents purged per batch. Each document is its own purge call; the batch
# only bounds how much is in flight between progress lines.
_BATCH = 50

# One row per artifact in a closed-head chain: which head it belongs to, how
# the head is classified, and what it still holds.
_CANDIDATES_SQL = """
WITH RECURSIVE heads AS (
    SELECT
        h.id,
        h.org_id,
        coalesce(h.extra->>'source_type', 'unknown') AS source_type,
        h.index_status = $3 OR coalesce(h.extra->>'document_text', '') <> '' AS deletion_recorded,
        EXISTS (
            SELECT 1 FROM knowledge.artifacts live
            WHERE live.org_id = h.org_id
              AND live.kb_slug = h.kb_slug
              AND live.belief_time_end = $1
              AND (
                  live.path = h.path
                  OR (
                      h.extra->>'source_connector_id' IS NOT NULL
                      AND h.extra->>'source_ref' IS NOT NULL
                      AND live.extra->>'source_connector_id' = h.extra->>'source_connector_id'
                      AND live.extra->>'source_ref' = h.extra->>'source_ref'
                  )
              )
        ) AS identity_live
    FROM knowledge.artifacts h
    WHERE h.superseded_by IS NULL
      AND h.belief_time_end <> $1
      AND ($2::text IS NULL OR h.org_id = $2)
),
chain AS (
    SELECT id AS head_id, id, org_id FROM heads
    UNION
    SELECT c.head_id, p.id, p.org_id
    FROM chain c
    JOIN knowledge.artifacts p ON p.superseded_by = c.id AND p.org_id = c.org_id
)
SELECT
    c.head_id::text AS head_id,
    h.org_id,
    h.source_type,
    h.identity_live,
    h.deletion_recorded,
    a.extra->>'document_text' IS NOT NULL AS has_text,
    (SELECT count(*) FROM knowledge.parent_chunks pc WHERE pc.artifact_id = a.id) AS parent_chunks,
    ARRAY(
        SELECT e FROM jsonb_array_elements_text(
            CASE
                WHEN a.extra ? 'graphiti_episode_ids' THEN a.extra->'graphiti_episode_ids'
                WHEN a.extra->>'graphiti_episode_id' IS NOT NULL
                    THEN jsonb_build_array(a.extra->>'graphiti_episode_id')
                ELSE '[]'::jsonb
            END
        ) AS e
        WHERE e <> 'no-chunks' AND e NOT LIKE 'skipped:%'
    ) AS episode_ids
FROM chain c
JOIN heads h ON h.id = c.head_id
JOIN knowledge.artifacts a ON a.id = c.id
"""

_CLOSED_ROWS_SQL = """
SELECT org_id, count(*) AS closed
FROM knowledge.artifacts
WHERE belief_time_end <> $1 AND ($2::text IS NULL OR org_id = $2)
GROUP BY org_id
"""


@dataclass
class _Group:
    documents: set[str] = field(default_factory=set)
    rows: int = 0
    rows_with_text: int = 0
    parent_chunks: int = 0
    episode_ids: set[str] = field(default_factory=set)


@dataclass
class Report:
    # (org_id, source_type) -> totals for documents classified as deleted
    deleted: dict[tuple[str, str], _Group]
    # (org_id, source_type) -> totals for unlinked rows of a live document
    kept_unlinked: dict[tuple[str, str], _Group]
    # (org_id, source_type) -> totals for closed heads without text: retired
    # by the drift repair or deleted before text was stored; never purged
    closed_without_text: dict[tuple[str, str], _Group]
    closed_rows: dict[str, int]
    # org_id -> head ids of deleted documents, the purge work list
    deleted_heads: dict[str, list[str]]


async def collect(conn: asyncpg.Connection, org_id: str | None) -> Report:
    deleted: dict[tuple[str, str], _Group] = defaultdict(_Group)
    kept: dict[tuple[str, str], _Group] = defaultdict(_Group)
    without_text: dict[tuple[str, str], _Group] = defaultdict(_Group)
    heads: dict[str, set[str]] = defaultdict(set)
    for row in await conn.fetch(_CANDIDATES_SQL, _SENTINEL, org_id, PURGE_PENDING):
        key = (row["org_id"], row["source_type"])
        is_deleted = not row["identity_live"] and row["deletion_recorded"]
        if row["identity_live"]:
            group = kept[key]
        elif is_deleted:
            group = deleted[key]
        else:
            group = without_text[key]
        group.documents.add(row["head_id"])
        group.rows += 1
        group.rows_with_text += int(row["has_text"])
        group.parent_chunks += row["parent_chunks"]
        group.episode_ids.update(row["episode_ids"])
        if is_deleted:
            heads[row["org_id"]].add(row["head_id"])
    closed = {
        row["org_id"]: row["closed"]
        for row in await conn.fetch(_CLOSED_ROWS_SQL, _SENTINEL, org_id)
    }
    return Report(
        deleted=dict(deleted),
        kept_unlinked=dict(kept),
        closed_without_text=dict(without_text),
        closed_rows=closed,
        deleted_heads={org: sorted(ids) for org, ids in heads.items()},
    )


def render(report: Report) -> str:
    lines = [
        "class                org_id                          source_type   documents     rows"
        "  with_text  parent_chunks  episodes"
    ]
    classes = (
        ("deleted", report.deleted),
        ("kept_unlinked", report.kept_unlinked),
        ("closed_without_text", report.closed_without_text),
    )
    for label, groups in classes:
        for (org, source_type), g in sorted(groups.items()):
            lines.append(
                f"{label:<20} {org:<31} {source_type:<13} {len(g.documents):>9} {g.rows:>8}"
                f" {g.rows_with_text:>10} {g.parent_chunks:>14} {len(g.episode_ids):>9}"
            )
    for org, closed in sorted(report.closed_rows.items()):
        in_chains = sum(g.rows for _, groups in classes for (o, _), g in groups.items() if o == org)
        lines.append(
            f"closed rows          {org:<31} total {closed}, replaced versions of live documents"
            f" {closed - in_chains} (kept)"
        )
    lines.append(
        "deleted = closed head with no live row of the same path or connector source_ref "
        "in the same knowledge base that still has its text or an unfinished delete "
        "(purged by --execute); kept_unlinked = closed and unlinked, but such a live row "
        "exists; closed_without_text = no text on the head, so it may be an upload the drift "
        "repair retired and a user still sees, or a document deleted before text was stored "
        "(neither is ever purged). See the module docstring for the residual uncertainty."
    )
    return "\n".join(lines)


async def purge(org_id: str, head_ids: list[str]) -> tuple[int, int]:
    """Purge each deleted document; returns (documents purged, documents failed)."""
    purged = failed = 0
    for start in range(0, len(head_ids), _BATCH):
        for head_id in head_ids[start : start + _BATCH]:
            try:
                async with tenant_scoped_connection(org_id) as conn:
                    await document_purge.purge_documents(conn, org_id, [head_id])
            except Exception:
                logger.exception("purge_deleted_document_failed", org_id=org_id, head_id=head_id)
                failed += 1
            else:
                purged += 1
        logger.info(
            "purge_deleted_documents_progress",
            org_id=org_id,
            done=min(start + _BATCH, len(head_ids)),
            total=len(head_ids),
            failed=failed,
        )
    return purged, failed


async def run(org_id: str | None, execute: bool) -> int:
    try:
        async with kb_operation(org_id, None, execute=execute):
            connection = (
                tenant_scoped_connection(org_id) if org_id else cross_org_admin_connection()
            )
            async with connection as conn:
                report = await collect(conn, org_id)
            print(render(report))
            if not execute:
                return 0
            assert org_id is not None  # main() refuses --execute without --org
            purged, failed = await purge(org_id, report.deleted_heads.get(org_id, []))
            print(f"purged {purged} deleted documents, {failed} failed")
            return 1 if failed else 0
    finally:
        await close_pool()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report, and only on request purge, deleted documents."
    )
    parser.add_argument("--org", help="Zitadel org id; omit for a dry run over every org")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Purge the documents classified as deleted (requires --org)",
    )
    args = parser.parse_args()
    if args.execute and not args.org:
        parser.error("--execute purges one organisation per approved run: pass --org")
    guard_cli("purge_deleted_documents", args.execute)
    return asyncio.run(run(args.org, args.execute))


if __name__ == "__main__":
    sys.exit(main())
