"""Operator script: give every artifact row an explicit state per derived product.

Rows written before ``derived_state`` existed carry no ``enrichment_state`` or
``graph_state``; the reader derives one from their old fields and answers
``unknown`` where those cannot tell. This script writes, once, the state that
is true for each such row, so the reader's legacy branch can be removed. It
never touches a row that already has an explicit state, so a second run
changes nothing and an interrupted run continues where it stopped.

    # Preview (default): what would be written, per organisation, product and
    # state, and how many rows still have no explicit state. Writes nothing.
    python -m knowledge_ingest.scripts.convert_document_states [--org <org_id>]

    # Write. Needs INGEST_OK=1; holds the organisation's operation lock.
    INGEST_OK=1 python -m knowledge_ingest.scripts.convert_document_states \\
        [--org <org_id>] --execute

What is written, where the reader already knows: the reader's answer, with a
legacy ``pending`` resolved (a live job keeps it pending; without one the
extraction or enrichment was interrupted and never completed). Where the
reader says ``unknown``, the truth is read where it lives:

- graph, active row: no extraction recorded, so none was ever built. The graph
  switched off, a navigation page and a row without text get what ingest would
  record today; otherwise ``failed (missing_before_states)``, the reason a
  later backfill selects. A recorded extraction whose episodes the graph lost
  stays ``done``, as a row written today does: the nightly reconcile reports
  it and ``reextract_document_graph`` repairs it, and both act only on
  ``done``. They are counted here as
  ``done_with_episodes_missing``.
- graph, closed row: ``failed (missing_before_states)``. ``not_applicable``
  would count as finished in the current-content walk and change what the
  prune keeps; today such a row is passed over, and ``failed`` keeps that.
- enrichment, active row: the vector index decides. Every point of the version
  carries ``text_enriched`` (the field only enrichment writes, and the one the
  cheap layer treats as the mark of an enriched chunk) means ``done``.
  Otherwise today's policy (too many chunks, switched off for the
  organisation) gives ``not_applicable``, no stored text gives
  ``failed (no_stored_text)``, and the rest is ``failed
  (missing_before_states)``.
- enrichment, closed row: ``not_applicable (not_current)``. Its points went
  with the version, and a job for a version that is no longer current is
  abandoned; nothing reads it.
- an upload's ``index_status`` in its old meaning (``pending``/``failed`` for
  its enrichment) becomes the current one: ``synced`` when the version has
  points, ``failed`` when it has none.

The conversion is bookkeeping, not a wave of new failures: it logs no
``derived_product_failed`` event per row, only one ``document_states_converted``
line per organisation with the counts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import Counter
from typing import Any

import asyncpg
import structlog
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import FieldCondition, Filter, IsEmptyCondition, MatchValue, PayloadField

from knowledge_ingest import graph as graph_module
from knowledge_ingest import org_config
from knowledge_ingest.config import settings
from knowledge_ingest.db import cross_org_admin_connection
from knowledge_ingest.derived_state import (
    DONE,
    FAILED,
    NOT_APPLICABLE,
    PENDING,
    UNKNOWN,
    Product,
    ProductState,
    _state_patch,
    product_state,
)
from knowledge_ingest.enrichment_policy import enrichment_skip_reason, graph_episode_skip_reason
from knowledge_ingest.operations import guard_cli, kb_operation
from knowledge_ingest.pg_store import _SENTINEL, PURGE_PENDING
from knowledge_ingest.qdrant_store import COLLECTION, get_client

logger = structlog.get_logger()

MISSING = "missing_before_states"
NOT_CURRENT = "not_current"
PRODUCTS: tuple[Product, ...] = ("enrichment", "graph")
_BATCH = 500
_SCROLL = 1000
_FIRST_ID = "00000000-0000-0000-0000-000000000000"
_TASKS: dict[str, Product] = {
    "enrich_document_interactive": "enrichment",
    "enrich_document_bulk": "enrichment",
    "ingest_graphiti_episode": "graph",
}


async def _point_counts(client: AsyncQdrantClient, org_id: str) -> tuple[Counter, Counter]:
    """Per artifact of the org: its points, and those enrichment wrote."""
    org = FieldCondition(key="org_id", match=MatchValue(value=org_id))
    enriched = IsEmptyCondition(is_empty=PayloadField(key="text_enriched"))
    counts: list[Counter] = []
    for selection in (Filter(must=[org]), Filter(must=[org], must_not=[enriched])):
        counter: Counter = Counter()
        offset = None
        while True:
            points, offset = await client.scroll(
                COLLECTION,
                scroll_filter=selection,
                limit=_SCROLL,
                offset=offset,
                with_payload=["artifact_id"],
                with_vectors=False,
            )
            counter.update(str((p.payload or {}).get("artifact_id")) for p in points)
            if offset is None:
                break
        counts.append(counter)
    return counts[0], counts[1]


async def _rows(conn: asyncpg.Connection, org_id: str, after: str, limit: int) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT id::text AS id, index_status, extra::text AS extra,
               belief_time_end = $3 AND index_status <> $4 AS active
        FROM knowledge.artifacts
        WHERE org_id = $1 AND id > $2::uuid
          AND (extra->>'enrichment_state' IS NULL OR extra->>'graph_state' IS NULL)
        ORDER BY id
        LIMIT $5
        """,
        org_id,
        after,
        _SENTINEL,
        PURGE_PENDING,
        limit,
    )
    return [{**dict(r), "extra": json.loads(r["extra"] or "{}")} for r in rows]


async def _live_jobs(conn: asyncpg.Connection, artifact_ids: list[str]) -> set[tuple[str, str]]:
    """(product, artifact id) of every queued or running job of these artifacts."""
    jobs = await conn.fetch(
        """
        SELECT task_name, args->>'artifact_id' AS artifact_id
        FROM procrastinate_jobs
        WHERE status IN ('todo', 'doing') AND args->>'artifact_id' = ANY($1::text[])
        """,
        artifact_ids,
    )
    return {
        (_TASKS[name], job["artifact_id"])
        for job in jobs
        if (name := job["task_name"].rsplit(".", 1)[-1]) in _TASKS
    }


def _graph_state(row: dict, live: bool) -> ProductState:
    known = product_state(row["extra"], "graph")
    if known.state not in (PENDING, UNKNOWN):
        return known
    if row["active"] and live:
        return ProductState(PENDING)
    if known.state == PENDING or not row["active"]:
        return ProductState(FAILED, MISSING)
    text = row["extra"].get("document_text")
    if not settings.graphiti_enabled:
        return ProductState(NOT_APPLICABLE, "graph_disabled")
    if not text:
        return ProductState(FAILED, "no_stored_text")
    if skip := graph_episode_skip_reason(text):
        return ProductState(NOT_APPLICABLE, skip)
    return ProductState(FAILED, MISSING)


def _enrichment_state(
    row: dict, live: bool, points: int, enriched: int, enabled: bool
) -> ProductState:
    extra = row["extra"]
    known = product_state(extra, "enrichment", index_status=row["index_status"])
    if row["active"] and live:
        return ProductState(PENDING)
    if known.state in (NOT_APPLICABLE, FAILED):
        return known
    if not row["active"]:
        return ProductState(NOT_APPLICABLE, NOT_CURRENT)
    if points and enriched == points:
        return ProductState(DONE)
    if skip := enrichment_skip_reason(chunk_count=points, extra_payload=extra):
        return ProductState(NOT_APPLICABLE, skip)
    if not enabled:
        return ProductState(NOT_APPLICABLE, "disabled_for_organisation")
    if not extra.get("document_text"):
        return ProductState(FAILED, "no_stored_text")
    return ProductState(FAILED, MISSING)


def _index_status(row: dict, live: bool, points: int) -> str | None:
    """The current meaning for an upload whose index_status still carries its enrichment."""
    old_meaning = not row["extra"].get("source_connector_id") and row["index_status"] in (
        PENDING,
        FAILED,
    )
    if not row["active"] or live or not old_meaning:
        return None
    return "synced" if points else FAILED


async def _write(
    conn: asyncpg.Connection,
    artifact_id: str,
    product: Product,
    state: ProductState,
    index_status: str | None,
) -> None:
    # Only where the row still has no state for this product: a state written
    # meanwhile by live traffic (set_product_state) always wins. From no state
    # every outcome is a legal move for that writer too; closed rows are
    # written here because its current-row condition is about live work.
    await conn.execute(
        """
        UPDATE knowledge.artifacts
        SET extra = COALESCE(extra, '{}'::jsonb) || $2::jsonb,
            index_status = COALESCE($4, index_status),
            index_status_changed_at = CASE WHEN $4::text IS NULL
                                           THEN index_status_changed_at ELSE $5 END
        WHERE id = $1::uuid AND extra->>($3 || '_state') IS NULL
        """,
        artifact_id,
        json.dumps(_state_patch(product, state.state, state.reason)),
        product,
        index_status,
        int(time.time()),
    )


def _recorded_episodes(extra: dict) -> list[str]:
    ids = extra.get("graphiti_episode_ids")
    return [str(i) for i in ids] if isinstance(ids, list) else [str(extra["graphiti_episode_id"])]


async def convert_org(
    conn: asyncpg.Connection,
    client: AsyncQdrantClient,
    org_id: str,
    *,
    execute: bool,
    batch_size: int = _BATCH,
) -> Counter:
    """Convert, or without ``execute`` only count, the org's rows without a state.

    The counter's keys are (product, state, reason).
    """
    counts: Counter = Counter()
    points, enriched = await _point_counts(client, org_id)
    enabled = await org_config.is_enrichment_enabled(conn, org_id)
    stored_episodes: set[str] | None = None
    after = _FIRST_ID
    while rows := await _rows(conn, org_id, after, batch_size):
        after = rows[-1]["id"]
        live = await _live_jobs(conn, [row["id"] for row in rows])
        for row in rows:
            extra, artifact_id = row["extra"], row["id"]
            for product in PRODUCTS:
                if extra.get(f"{product}_state"):
                    continue
                is_live = (product, artifact_id) in live
                if product == "graph":
                    state, index_status = _graph_state(row, is_live), None
                else:
                    state = _enrichment_state(
                        row, is_live, points[artifact_id], enriched[artifact_id], enabled
                    )
                    index_status = _index_status(row, is_live, points[artifact_id])
                counts[(product, state.state, state.reason)] += 1
                if index_status:
                    counts[("index_status", index_status, row["index_status"])] += 1
                if (
                    product == "graph"
                    and state.state == DONE
                    and row["active"]
                    and settings.graphiti_enabled
                ):
                    if stored_episodes is None:
                        stored_episodes = await graph_module.stored_episode_uuids(org_id)
                    if not set(_recorded_episodes(extra)) <= stored_episodes:
                        counts[("check", "done_with_episodes_missing", None)] += 1
                if execute:
                    await _write(conn, artifact_id, product, state, index_status)
    logger.info(
        "document_states_converted" if execute else "document_states_preview",
        org_id=org_id,
        counts={" ".join(str(k) for k in key): n for key, n in sorted(counts.items(), key=str)},
    )
    return counts


async def remaining(conn: asyncpg.Connection, org_id: str | None) -> dict[str, int]:
    """Rows still without an explicit state, per product; zero for both lets the legacy go."""
    row = await conn.fetchrow(
        """
        SELECT count(*) FILTER (WHERE extra->>'enrichment_state' IS NULL) AS enrichment,
               count(*) FILTER (WHERE extra->>'graph_state' IS NULL) AS graph
        FROM knowledge.artifacts
        WHERE $1::text IS NULL OR org_id = $1
        """,
        org_id,
    )
    return {"enrichment": row["enrichment"], "graph": row["graph"]}


async def run(org_id: str | None, *, execute: bool, batch_size: int = _BATCH) -> dict[str, Any]:
    async with cross_org_admin_connection() as conn:
        orgs = (
            [org_id]
            if org_id
            else [
                r["org_id"]
                for r in await conn.fetch(
                    "SELECT DISTINCT org_id FROM knowledge.artifacts "
                    "WHERE extra->>'enrichment_state' IS NULL OR extra->>'graph_state' IS NULL "
                    "ORDER BY org_id"
                )
            ]
        )
        per_org: dict[str, Counter] = {}
        for org in orgs:
            async with kb_operation(org, None, execute=execute):
                per_org[org] = await convert_org(
                    conn, get_client(), org, execute=execute, batch_size=batch_size
                )
        return {"per_org": per_org, "remaining": await remaining(conn, org_id)}


def _print(result: dict[str, Any], execute: bool) -> None:
    for org, counts in result["per_org"].items():
        for (product, state, reason), n in sorted(counts.items(), key=str):
            print(f"{org:40} {product:12} {state:15} {reason or '':30} {n:>8}")
    verb = "written" if execute else "would be written; nothing written"
    print(f"{verb}. rows without an explicit state now: {result['remaining']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--org", help="convert one organisation (default: every organisation)")
    parser.add_argument("--execute", action="store_true", help="write; preview without it")
    parser.add_argument("--batch-size", type=int, default=_BATCH, help="rows per batch")
    args = parser.parse_args()
    guard_cli("convert_document_states", args.execute)
    _print(
        asyncio.run(run(args.org, execute=args.execute, batch_size=args.batch_size)), args.execute
    )


if __name__ == "__main__":
    main()
