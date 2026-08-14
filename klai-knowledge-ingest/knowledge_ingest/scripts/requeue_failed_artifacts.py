"""Operator script: requeue failed artifacts for bulk enrichment.

Context: before the shared LiteLLM token bucket (``knowledge_ingest/llm_throttle.py``)
existed, concurrent crawls/imports routinely burned through every retry on
``enrich_document_bulk`` (see ``knowledge_ingest/enrichment_tasks.py``) with
429s. Roughly 1165 artifacts ended up permanently ``index_status='failed'``
with no enriched chunks -- a silent quality regression, because ingest
itself already reported success before enrichment ran. The 429 root cause
is fixed now; this script re-enqueues bulk enrichment for the artifacts
that were affected so they get a fair retry under the fixed throttle.

Usage (inside the running container):

    docker exec klai-core-knowledge-ingest-1 \\
        python -m knowledge_ingest.scripts.requeue_failed_artifacts --dry-run

    docker exec klai-core-knowledge-ingest-1 \\
        python -m knowledge_ingest.scripts.requeue_failed_artifacts --execute

    docker exec klai-core-knowledge-ingest-1 \\
        python -m knowledge_ingest.scripts.requeue_failed_artifacts \\
        --execute --org <org_id> --kb <kb_slug> --limit 200

Selection: active artifacts (``belief_time_end = _SENTINEL`` -- the same
"still current" marker every other active-artifact query in ``pg_store.py``
uses) with ``index_status = 'failed'``, oldest ``created_at`` first,
optionally scoped by ``--org`` / ``--kb``.

Enqueue: defers ``enrich_document_bulk`` -- the exact task normal bulk
ingest defers (see ``knowledge_ingest/routes/ingest.py``) -- with
``queueing_lock=f"requeue:{artifact_id}"``. That lock namespace is
deliberately distinct from normal ingest's
``f"{org_id}:{kb_slug}:{path}:{artifact_id}"`` lock so a requeue can never
collide with (or silently no-op against) a legitimate concurrent re-ingest
of the same document.

This script does NOT touch ``index_status`` itself. ``enrich_document_bulk``
(via ``_load_and_enrich`` / ``_set_direct_upload_index_status`` in
``enrichment_tasks.py``) is the sole owner of that field and sets it to
``synced`` or ``failed`` once the retry completes.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import structlog

from knowledge_ingest.config import settings
from knowledge_ingest.db import cross_org_admin_connection
from knowledge_ingest.pg_store import _SENTINEL  # single source of truth -- do not re-literal

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import AsyncIterator

    import asyncpg

logger = structlog.get_logger()

DEFAULT_LIMIT = 1000
_LOG_EVERY = 100


async def _select_failed_artifacts(
    conn: asyncpg.Connection,
    *,
    org_id: str | None,
    kb_slug: str | None,
    limit: int,
) -> list[dict]:
    """Select active, failed artifacts oldest-``created_at``-first.

    ``belief_time_end = _SENTINEL`` restricts to the currently-active
    belief-time row per artifact -- a superseded/soft-deleted row must
    never be requeued. ``org_id`` / ``kb_slug`` are optional scoping
    filters: a static query with ``$N::text IS NULL OR ...`` clauses is
    used (rather than building the WHERE clause with an f-string) so the
    SQL text itself never varies with caller input.
    """
    params: list[Any] = [_SENTINEL, org_id, kb_slug, limit]

    rows = await conn.fetch(
        """
        SELECT
            a.id::text AS artifact_id,
            a.org_id AS org_id,
            a.kb_slug AS kb_slug,
            a.path AS path,
            a.created_at AS created_at
        FROM knowledge.artifacts a
        WHERE a.index_status = 'failed'
          AND a.belief_time_end = $1
          AND ($2::text IS NULL OR a.org_id = $2)
          AND ($3::text IS NULL OR a.kb_slug = $3)
        ORDER BY a.created_at ASC
        LIMIT $4
        """,
        *params,
    )
    return [
        {
            "artifact_id": row["artifact_id"],
            "org_id": row["org_id"],
            "kb_slug": row["kb_slug"],
            "path": row["path"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


@asynccontextmanager
async def _procrastinate_app() -> AsyncIterator[Any]:
    """Bootstrap a standalone Procrastinate App with tasks registered.

    Mirrors ``knowledge_ingest.worker.WorkerLifecycle.__aenter__``: this
    script runs as a one-shot ``docker exec`` process outside the FastAPI
    lifespan, so there is no already-open app to reuse via
    ``enrichment_tasks.get_app()`` -- it has to build and open its own
    connector pool, exactly like the worker does at container startup.
    """
    import procrastinate

    from knowledge_ingest import enrichment_tasks
    from knowledge_ingest.worker import _build_libpq_dsn

    conninfo = _build_libpq_dsn(settings.postgres_dsn)
    connector = procrastinate.PsycopgConnector(conninfo=conninfo, kwargs={})
    app = enrichment_tasks.init_app(connector)
    async with app.open_async():
        yield app


def _print_dry_run_summary(artifacts: list[dict]) -> None:
    print(f"[dry-run] {len(artifacts)} failed artifact(s) would be requeued")
    groups: dict[tuple[str, str], int] = {}
    for artifact in artifacts:
        key = (artifact["org_id"], artifact["kb_slug"])
        groups[key] = groups.get(key, 0) + 1
    for (org_id, kb_slug), count in sorted(groups.items()):
        print(f"  org={org_id} kb={kb_slug}: {count}")
    preview = artifacts[:10]
    print(f"First {len(preview)} artifact(s):")
    for artifact in preview:
        print(
            f"  {artifact['artifact_id']}  "
            f"{artifact['org_id']}/{artifact['kb_slug']}/{artifact['path']}"
        )


async def _requeue(artifacts: list[dict]) -> tuple[int, int]:
    """Defer ``enrich_document_bulk`` for every artifact.

    Returns ``(enqueued, skipped_already_enqueued)``. ``AlreadyEnqueued`` is
    expected (concurrent requeue run, or a live job already covers the
    artifact) and must not abort the remaining batch.
    """
    from procrastinate.exceptions import AlreadyEnqueued

    total = len(artifacts)
    enqueued = 0
    skipped = 0
    async with _procrastinate_app() as proc_app:
        for idx, artifact in enumerate(artifacts, start=1):
            artifact_id = artifact["artifact_id"]
            try:
                await proc_app.enrich_document_bulk.configure(  # type: ignore[attr-defined]
                    queueing_lock=f"requeue:{artifact_id}",
                ).defer_async(artifact_id=artifact_id)
                enqueued += 1
            except AlreadyEnqueued:
                skipped += 1
                logger.info(
                    "requeue_failed_artifact_already_enqueued",
                    artifact_id=artifact_id,
                    org_id=artifact["org_id"],
                    kb_slug=artifact["kb_slug"],
                )
                continue

            if idx % _LOG_EVERY == 0 or idx == total:
                logger.info(
                    "requeue_failed_artifacts_progress",
                    processed=idx,
                    total=total,
                    enqueued=enqueued,
                    skipped_already_enqueued=skipped,
                )
    return enqueued, skipped


async def main(
    *,
    dry_run: bool,
    limit: int,
    org_id: str | None,
    kb_slug: str | None,
) -> None:
    async with cross_org_admin_connection() as conn:
        artifacts = await _select_failed_artifacts(
            conn, org_id=org_id, kb_slug=kb_slug, limit=limit
        )

    if not artifacts:
        logger.info(
            "requeue_failed_artifacts_none_selected",
            org_id=org_id,
            kb_slug=kb_slug,
        )
        return

    if dry_run:
        _print_dry_run_summary(artifacts)
        return

    enqueued, skipped = await _requeue(artifacts)
    logger.info(
        "requeue_failed_artifacts_done",
        selected=len(artifacts),
        enqueued=enqueued,
        skipped_already_enqueued=skipped,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Requeue index_status='failed' artifacts for bulk enrichment "
            "(recovery from LiteLLM 429 exhaustion prior to the shared "
            "token-bucket fix)."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Select and print candidates without enqueueing anything (default).",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually defer enrich_document_bulk for every selected artifact.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Maximum artifacts to select, oldest first (default: {DEFAULT_LIMIT}).",
    )
    parser.add_argument("--org", dest="org_id", default=None, help="Restrict to one org_id.")
    parser.add_argument("--kb", dest="kb_slug", default=None, help="Restrict to one kb_slug.")
    return parser.parse_args(argv)


if __name__ == "__main__":
    from knowledge_ingest.logging_setup import setup_logging

    setup_logging("knowledge-ingest")

    args = _parse_args()
    asyncio.run(
        main(
            dry_run=not args.execute,
            limit=args.limit,
            org_id=args.org_id,
            kb_slug=args.kb_slug,
        )
    )
