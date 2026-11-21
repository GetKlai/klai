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

``--product graph`` hands each version to ``graph_refresh._refresh_stale_graph``
as a reindex by a person would: it applies the ingest route's policy (a
navigation page is recorded ``not_applicable``), queues
``ingest_graphiti_episode`` with the route's locks and arguments including
``replace_stale``, and answers ``None`` when the extraction is already current.
``--reason`` narrows either product to one failure reason (for example
``missing_before_states``). A version without stored text is never selected,
and without ``--reason`` neither is a graph that failed for a reason a new job
cannot change (``graph_refresh._cannot_succeed``): their jobs could only fail
again and alert. The preview counts both separately.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import structlog

from knowledge_ingest import pg_store
from knowledge_ingest.config import settings
from knowledge_ingest.db import cross_org_admin_connection, tenant_scoped_connection
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


# The rows a run looks at: current versions, not being purged, whose product
# failed (for the reason, when one is given). Static SQL with
# ``$N::text IS NULL OR ...`` clauses, so the text never varies with caller input.
# Parameters: $1 sentinel, $2 org, $3 kb, $4 product, $5 reason, $6 purge-pending
# status, $7 the current graph writer build.
_FAILED_ROWS = """
    FROM knowledge.artifacts a
    WHERE (a.extra::jsonb->>($4 || '_state') = 'failed'
           OR ($4 = 'enrichment' AND a.extra::jsonb->>'enrichment_state' IS NULL
               AND a.index_status = 'failed'))
      AND ($5::text IS NULL OR a.extra::jsonb->>($4 || '_reason') = $5
           OR a.extra::jsonb->>($4 || '_state') IS NULL)
      AND a.belief_time_end = $1
      AND a.index_status <> $6
      AND ($2::text IS NULL OR a.org_id = $2)
      AND ($3::text IS NULL OR a.kb_slug = $3)
"""
_HAS_TEXT = "COALESCE(a.extra::jsonb->>'document_text', '') <> ''"
# graph_refresh._cannot_succeed as a condition: a graph that failed for want of
# text, or quarantined by the writer build that runs now, fails the same way
# again. Held only when no reason is named; naming it is a deliberate choice.
_HELD = """COALESCE($4 = 'graph' AND $5::text IS NULL
    AND (a.extra::jsonb->>'graph_reason' = 'no_stored_text'
         OR (a.extra::jsonb->>'graph_reason' = 'quarantined'
             AND a.extra::jsonb->>'graphiti_quarantine_build' = $7::text)), false)"""
_SELECT = (
    """
    SELECT a.id::text AS artifact_id, a.org_id AS org_id, a.kb_slug AS kb_slug,
           a.path AS path, a.created_at AS created_at, a.index_status AS index_status,
           a.extra::jsonb - 'document_text' AS extra
    """
    + _FAILED_ROWS
    + f"  AND {_HAS_TEXT} AND NOT {_HELD}\n    ORDER BY a.created_at ASC\n    LIMIT $8"
)
_COUNT_LEFT_OUT = (
    f"""
    SELECT count(*) FILTER (WHERE NOT {_HAS_TEXT}) AS without_text,
           count(*) FILTER (WHERE {_HAS_TEXT} AND {_HELD}) AS held
    """
    + _FAILED_ROWS
)


def _params(
    product: Product, reason: str | None, org_id: str | None, kb_slug: str | None
) -> list[Any]:
    build = None
    if product == "graph":
        from knowledge_ingest.falkordb_probe import graph_writer_build

        build = graph_writer_build()
    return [_SENTINEL, org_id, kb_slug, product, reason, PURGE_PENDING, build]


async def _select_failed_artifacts(
    conn: asyncpg.Connection,
    *,
    org_id: str | None,
    kb_slug: str | None,
    limit: int,
    product: Product = "enrichment",
    reason: str | None = None,
) -> list[dict]:
    """Select current versions whose ``product`` failed and that can be queued.

    Oldest ``created_at`` first. For the graph these are current versions with
    an explicit failed state; for enrichment, whether it failed and why is
    ``derived_state.product_state``'s answer, legacy rows included, and the
    SQL only narrows the rows the reader has to look at. A row whose
    ``index_status`` is failed but whose enrichment is done had its raw write
    fail, which a new enrichment does not repair, so it is not selected.

    ``belief_time_end = _SENTINEL`` restricts to the currently-active
    belief-time row per artifact and a row being purged is skipped: a
    superseded, soft-deleted or deleting row must never be requeued. Rows
    without stored text and held graphs are left out in the SQL, so the limit
    counts only versions a job can do something for; otherwise they would
    come back first on every run and a small limit would queue nothing.
    """
    rows = await conn.fetch(_SELECT, *_params(product, reason, org_id, kb_slug), limit)
    selected = []
    for row in rows:
        state = product_state(_extra(row["extra"]), product, index_status=row["index_status"])
        if state.state != FAILED or (reason is not None and state.reason != reason):
            continue
        selected.append(
            {
                "artifact_id": row["artifact_id"],
                "org_id": row["org_id"],
                "kb_slug": row["kb_slug"],
                "path": row["path"],
                "created_at": row["created_at"],
                "index_status": row["index_status"],
            }
        )
    return selected


async def _count_left_out(
    conn: asyncpg.Connection,
    *,
    org_id: str | None,
    kb_slug: str | None,
    product: Product,
    reason: str | None,
) -> dict[str, int]:
    """How many failed versions the selection leaves out: no stored text, or held."""
    row = await conn.fetchrow(_COUNT_LEFT_OUT, *_params(product, reason, org_id, kb_slug))
    return {"without_text": row["without_text"], "held": row["held"]}


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


def _print_dry_run_summary(artifacts: list[dict], left_out: dict[str, int]) -> None:
    print(f"[dry-run] {len(artifacts)} failed artifact(s) would be requeued")
    print(f"  {left_out['without_text']} without stored text, not requeued")
    print(f"  {left_out['held']} held: a new job would fail the same way")
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


# What a run did with each version, as the done event reports it.
_OUTCOMES = (
    "enqueued",
    "skipped_already_enqueued",
    "not_applicable",
    "extraction_current",
    "not_current",
)


async def _queue_graph(artifact: dict) -> str:
    """Hand the version to the graph refresh, as a reindex by a person does.

    It applies the ingest route's policy and queues the route's job, so none
    of that is repeated here; it raises when the job cannot be queued, which
    stops the run.
    """
    from knowledge_ingest import graph_refresh

    async with tenant_scoped_connection(artifact["org_id"]) as conn:
        row = await pg_store.read_artifact_for_enrichment(conn, artifact["artifact_id"])
        if row is None:
            return "not_current"  # replaced or deleted since the selection
        extra = row["extra"]
        answer = await graph_refresh._refresh_stale_graph(
            conn,
            artifact_id=artifact["artifact_id"],
            extra=extra,
            org_id=row["org_id"],
            kb_slug=row["kb_slug"],
            path=row["path"],
            content_type=row["content_type"],
            belief_time_start=row["belief_time_start"],
            indexable_content=extra.get("document_text") or "",
            user_requested=True,
        )
    if answer is None:
        return "extraction_current"
    if answer.startswith("skipped:"):
        return "not_applicable"
    return {"queued": "enqueued", "already_queued": "skipped_already_enqueued"}[answer]


async def _queue_enrichment(conn: Any, proc_app: Any, artifact: dict) -> str:
    artifact_id = artifact["artifact_id"]
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
    queued = await queue_derived_work(
        conn,
        artifact_id,
        "enrichment",
        proc_app.enrich_document_bulk.configure(  # type: ignore[attr-defined]
            queueing_lock=f"requeue:{artifact_id}",
        ),
        artifact_id=artifact_id,
    )
    return "enqueued" if queued else "skipped_already_enqueued"


async def _requeue(artifacts: list[dict], product: Product = "enrichment") -> dict[str, int]:
    """Queue the ``product`` job for every selected artifact; counts per outcome.

    The document's product goes back to pending first, the one legal way out
    of failed, and a queue that fails marks it failed again and stops the run.
    An artifact a live job already covers is skipped and must not abort the
    remaining batch.
    """
    total = len(artifacts)
    outcomes: Counter[str] = Counter()
    async with _procrastinate_app() as proc_app, cross_org_admin_connection() as conn:
        for idx, artifact in enumerate(artifacts, start=1):
            if product == "graph":
                outcome = await _queue_graph(artifact)
            else:
                outcome = await _queue_enrichment(conn, proc_app, artifact)
            outcomes[outcome] += 1
            if outcome != "enqueued":
                logger.info(
                    "requeue_failed_artifact_not_queued",
                    outcome=outcome,
                    artifact_id=artifact["artifact_id"],
                    org_id=artifact["org_id"],
                    kb_slug=artifact["kb_slug"],
                )
            if idx % _LOG_EVERY == 0 or idx == total:
                logger.info(
                    "requeue_failed_artifacts_progress", processed=idx, total=total, **outcomes
                )
    return {key: outcomes[key] for key in _OUTCOMES}


async def main(
    *,
    dry_run: bool,
    limit: int,
    org_id: str | None,
    kb_slug: str | None,
    product: Product = "enrichment",
    reason: str | None = None,
) -> None:
    if product == "graph" and not settings.graphiti_enabled:
        # Every queued job would retry for a day and fail again.
        sys.stderr.write(
            "requeue_failed_artifacts: the graph is switched off "
            "(settings.graphiti_enabled is false); nothing was selected or queued\n"
        )
        raise SystemExit(2)
    async with cross_org_admin_connection() as conn:
        artifacts = await _select_failed_artifacts(
            conn, org_id=org_id, kb_slug=kb_slug, limit=limit, product=product, reason=reason
        )
        if dry_run:
            left_out = await _count_left_out(
                conn, org_id=org_id, kb_slug=kb_slug, product=product, reason=reason
            )

    if dry_run:
        _print_dry_run_summary(artifacts, left_out)
        return

    if not artifacts:
        logger.info(
            "requeue_failed_artifacts_none_selected",
            org_id=org_id,
            kb_slug=kb_slug,
            product=product,
            reason=reason,
        )
        return

    outcomes = await _requeue(artifacts, product)
    logger.info(
        "requeue_failed_artifacts_done", product=product, selected=len(artifacts), **outcomes
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
