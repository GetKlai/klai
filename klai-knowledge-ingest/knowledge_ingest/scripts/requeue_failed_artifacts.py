"""Operator script: requeue current versions whose enrichment or graph failed.

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
uses) whose enrichment failed according to ``derived_state.product_state``
(legacy rows included), oldest ``created_at`` first, optionally scoped by
``--org`` / ``--kb``.

Enqueue: defers ``enrich_document_bulk`` -- the exact task normal bulk
ingest defers (see ``knowledge_ingest/routes/ingest.py``) -- with
``queueing_lock=f"requeue:{artifact_id}"``. That lock namespace is
deliberately distinct from normal ingest's
``f"{org_id}:{kb_slug}:{path}:{artifact_id}"`` lock so a requeue can never
collide with (or silently no-op against) a legitimate concurrent re-ingest
of the same document.

The script moves each document's enrichment back to pending
(``queue_derived_work``); ``enrich_document_bulk`` records ``done`` or
``failed`` once the job completes.

``--product graph`` does the same for the graph: it queues
``ingest_graphiti_episode`` with the arguments and locks the ingest route uses,
including ``replace_stale``, so the graph keeps only the current extraction.
``--reason`` narrows either product to one failure reason (for example
``missing_before_states``). A version without stored text is never queued,
because its job could only fail again and alert; the preview counts those
separately.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import structlog

from knowledge_ingest.config import settings
from knowledge_ingest.db import cross_org_admin_connection
from knowledge_ingest.derived_state import FAILED, Product, product_state, queue_derived_work
from knowledge_ingest.operations import guard_cli
from knowledge_ingest.pg_store import (  # single source of truth -- do not re-literal
    _SENTINEL,
    PURGE_PENDING,
)

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
    product: Product = "enrichment",
    reason: str | None = None,
) -> list[dict]:
    """Select current versions whose ``product`` failed, oldest-``created_at``-first.

    Whether it failed, and why, is ``derived_state.product_state``'s answer,
    legacy enrichment rows included; the SQL only narrows the rows the reader
    has to look at. A row whose ``index_status`` is failed but whose
    enrichment is done had its raw write fail, which a new enrichment does not
    repair, so it is not selected.

    ``belief_time_end = _SENTINEL`` restricts to the currently-active
    belief-time row per artifact and a row being purged is skipped: a
    superseded, soft-deleted or deleting row must never be requeued.
    ``org_id`` / ``kb_slug`` are optional scoping
    filters: a static query with ``$N::text IS NULL OR ...`` clauses is
    used (rather than building the WHERE clause with an f-string) so the
    SQL text itself never varies with caller input.
    """
    params: list[Any] = [_SENTINEL, org_id, kb_slug, limit, product, reason, PURGE_PENDING]

    rows = await conn.fetch(
        """
        SELECT
            a.id::text AS artifact_id,
            a.org_id AS org_id,
            a.kb_slug AS kb_slug,
            a.path AS path,
            a.content_type AS content_type,
            a.belief_time_start AS belief_time_start,
            a.created_at AS created_at,
            a.index_status AS index_status,
            a.extra::jsonb AS extra
        FROM knowledge.artifacts a
        WHERE (a.extra::jsonb->>($5 || '_state') = 'failed'
               OR ($5 = 'enrichment' AND a.extra::jsonb->>'enrichment_state' IS NULL
                   AND a.index_status = 'failed'))
          AND ($6::text IS NULL OR a.extra::jsonb->>($5 || '_reason') = $6
               OR a.extra::jsonb->>($5 || '_state') IS NULL)
          AND a.belief_time_end = $1
          AND a.index_status <> $7
          AND ($2::text IS NULL OR a.org_id = $2)
          AND ($3::text IS NULL OR a.kb_slug = $3)
        ORDER BY a.created_at ASC
        LIMIT $4
        """,
        *params,
    )
    selected = []
    for row in rows:
        extra = _extra(row["extra"])
        state = product_state(extra, product, index_status=row["index_status"])
        if state.state != FAILED or (reason is not None and state.reason != reason):
            continue
        selected.append(
            {
                "artifact_id": row["artifact_id"],
                "org_id": row["org_id"],
                "kb_slug": row["kb_slug"],
                "path": row["path"],
                "content_type": row["content_type"],
                "belief_time_start": row["belief_time_start"],
                "created_at": row["created_at"],
                "index_status": row["index_status"],
                "has_text": bool(extra.get("document_text")),
            }
        )
    return selected


def _extra(value: Any) -> dict:
    return json.loads(value) if isinstance(value, str) else (value or {})


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


def _print_dry_run_summary(selected: list[dict]) -> None:
    artifacts = [a for a in selected if a["has_text"]]
    print(f"[dry-run] {len(artifacts)} failed artifact(s) would be requeued")
    print(f"  {len(selected) - len(artifacts)} without stored text, not requeued")
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


async def _queue(conn: Any, proc_app: Any, artifact: dict, product: Product) -> bool:
    artifact_id = artifact["artifact_id"]
    if product == "graph":
        # The ingest route's job, locks and arguments (routes/ingest.py).
        return await queue_derived_work(
            conn,
            artifact_id,
            "graph",
            proc_app.ingest_graphiti_episode.configure(
                lock=f"graphiti:{artifact_id}",
                queueing_lock=f"graphiti:{artifact_id}",
            ),
            artifact_id=artifact_id,
            org_id=artifact["org_id"],
            content_type=artifact["content_type"],
            belief_time_start=artifact["belief_time_start"],
            kb_slug=artifact["kb_slug"],
            path=artifact["path"],
            replace_stale=True,
        )
    if artifact["index_status"] == "failed":
        # A row from before the enrichment state kept its enrichment
        # failure in index_status. Back to pending, as a reindex does,
        # so the job's outcome moves it on.
        await conn.execute(
            "UPDATE knowledge.artifacts SET index_status = 'pending', "
            "index_status_changed_at = extract(epoch from now())::bigint "
            "WHERE id = $1::uuid AND index_status = 'failed'",
            artifact_id,
        )
    return await queue_derived_work(
        conn,
        artifact_id,
        "enrichment",
        proc_app.enrich_document_bulk.configure(  # type: ignore[attr-defined]
            queueing_lock=f"requeue:{artifact_id}",
        ),
        artifact_id=artifact_id,
    )


async def _requeue(selected: list[dict], product: Product = "enrichment") -> tuple[int, int]:
    """Queue the ``product`` job for every selected artifact that has stored text.

    Through ``queue_derived_work``: the document's product goes back to
    pending, the one legal way out of failed, and a queue that fails marks it
    failed again and stops the run. Returns ``(enqueued,
    skipped_already_enqueued)``; an artifact a live job already covers is
    skipped and must not abort the remaining batch.
    """
    artifacts = [a for a in selected if a["has_text"]]
    total = len(artifacts)
    enqueued = 0
    skipped = 0
    async with _procrastinate_app() as proc_app, cross_org_admin_connection() as conn:
        for idx, artifact in enumerate(artifacts, start=1):
            if await _queue(conn, proc_app, artifact, product):
                enqueued += 1
            else:
                skipped += 1
                logger.info(
                    "requeue_failed_artifact_already_enqueued",
                    artifact_id=artifact["artifact_id"],
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
    product: Product = "enrichment",
    reason: str | None = None,
) -> None:
    async with cross_org_admin_connection() as conn:
        artifacts = await _select_failed_artifacts(
            conn, org_id=org_id, kb_slug=kb_slug, limit=limit, product=product, reason=reason
        )

    if not artifacts:
        logger.info(
            "requeue_failed_artifacts_none_selected",
            org_id=org_id,
            kb_slug=kb_slug,
            product=product,
            reason=reason,
        )
        return

    if dry_run:
        _print_dry_run_summary(artifacts)
        return

    enqueued, skipped = await _requeue(artifacts, product)
    logger.info(
        "requeue_failed_artifacts_done",
        product=product,
        selected=len(artifacts),
        without_text=sum(1 for a in artifacts if not a["has_text"]),
        enqueued=enqueued,
        skipped_already_enqueued=skipped,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Requeue the enrichment or graph job of current versions where it failed."
    )
    parser.add_argument(
        "--product",
        choices=("enrichment", "graph"),
        default="enrichment",
        help="Which derived product to requeue (default: enrichment).",
    )
    parser.add_argument(
        "--reason",
        default=None,
        help="Only versions that failed for this reason, e.g. missing_before_states.",
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
        help="Actually queue the product's job for every selected artifact.",
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
    guard_cli("requeue_failed_artifacts", args.execute)
    asyncio.run(
        main(
            dry_run=not args.execute,
            limit=args.limit,
            org_id=args.org_id,
            kb_slug=args.kb_slug,
            product=args.product,
            reason=args.reason,
        )
    )
