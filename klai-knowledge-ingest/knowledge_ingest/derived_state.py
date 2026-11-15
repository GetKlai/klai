"""The state of each derived product of a document version: enrichment and graph.

A document version (one ``knowledge.artifacts`` row) has two products built
from its text after ingest: enrichment of its chunks and graph extraction. For
each, the row records exactly one state in ``extra``:

- ``pending``: the version was just created, or its work is queued or running;
- ``done``;
- ``failed`` with a reason: the work was needed for a current document and
  could not be done; the job that gave up ends failed too;
- ``not_applicable`` with a reason: policy said no (held out of the graph on
  purpose, no text to extract, too large for enrichment, switched off).

A version is born with both states ``pending`` (``born_pending``, written by
``pg_store.create_artifact`` in its INSERT), so every row the service writes
has them. ``product_state`` is the one reader. Rows written before these keys existed
are derived from the fields they have, and say ``unknown`` where those fields
cannot tell, never ``done`` by default. ``set_product_state`` is the one
writer and enforces the legal transitions; a transition to ``failed`` logs
``derived_product_failed`` once at error level, which the
``derived_product_failed`` alert fires on. Nothing
scans for incomplete documents or queues their work again: the document says
what happened, and a person acts.

Why keys in ``extra`` and not columns: every reader already loads ``extra``
(the graph fields the old rows are derived from live there), the transitions
are written by the same ``extra`` merges the jobs already make, and no query
filters on the state at a scale that needs an index.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

import asyncpg
import structlog

Product = Literal["enrichment", "graph"]
PRODUCTS: tuple[Product, ...] = ("enrichment", "graph")

PENDING = "pending"
DONE = "done"
FAILED = "failed"
NOT_APPLICABLE = "not_applicable"
UNKNOWN = "unknown"

logger = structlog.get_logger()


@dataclass(frozen=True)
class ProductState:
    state: str
    reason: str | None = None


def _legacy_graph_state(extra: Mapping[str, Any]) -> ProductState:
    scalar = extra.get("graphiti_episode_id")
    if extra.get("graphiti_quarantined_at"):
        return ProductState(FAILED, "quarantined")
    if isinstance(scalar, str) and scalar.startswith("skipped:"):
        return ProductState(NOT_APPLICABLE, scalar.removeprefix("skipped:"))
    if scalar == "no-chunks":
        return ProductState(NOT_APPLICABLE, "no_text")
    ids = extra.get("graphiti_episode_ids")
    recorded = ids if isinstance(ids, list) else ([scalar] if scalar else [])
    if not recorded:
        return ProductState(UNKNOWN)
    if extra.get("graphiti_episode_complete") is False:
        if extra.get("graphiti_exhausted_at"):
            return ProductState(FAILED, "retries_exhausted")
        return ProductState(PENDING)
    # Rows written before graphiti_episode_complete existed recorded ids only
    # once an extraction had finished.
    return ProductState(DONE)


def _legacy_enrichment_state(extra: Mapping[str, Any], index_status: str | None) -> ProductState:
    if extra.get("llm_enrichment_skipped"):
        return ProductState(NOT_APPLICABLE, "policy")
    # Before this change an upload's index_status carried its enrichment while
    # it ran or after it failed; ``synced`` also covered "skipped", so it says
    # nothing. A connector or crawl row's enrichment left no trace at all.
    if not extra.get("source_connector_id") and index_status in (PENDING, FAILED):
        return ProductState(index_status, "legacy" if index_status == FAILED else None)
    return ProductState(UNKNOWN)


def product_state(
    extra: Mapping[str, Any] | None, product: Product, *, index_status: str | None = None
) -> ProductState:
    """The state of ``product`` for the artifact row whose ``extra`` this is."""
    extra = extra or {}
    state = extra.get(f"{product}_state")
    if state:
        return ProductState(str(state), extra.get(f"{product}_reason"))
    if product == "graph":
        return _legacy_graph_state(extra)
    return _legacy_enrichment_state(extra, index_status)


def _state_patch(product: Product, state: str, reason: str | None) -> dict[str, Any]:
    return {
        f"{product}_state": state,
        f"{product}_reason": reason,
        f"{product}_state_at": int(time.time()),
    }


def born_pending() -> dict[str, Any]:
    """The states a new document version is inserted with: both products pending.

    Written in the INSERT itself, so no row the service writes ever exists
    without a state; an ingest that dies before its outcome leaves pending,
    which the stale-pending reaper turns into ``failed (interrupted)``.
    """
    return {
        key: value
        for product in PRODUCTS
        for key, value in _state_patch(product, PENDING, None).items()
    }


STATE_KEYS: frozenset[str] = frozenset(born_pending())
"""The ``extra`` keys this module owns; no caller-supplied metadata may carry them."""


async def set_product_state(
    conn: asyncpg.Connection,
    artifact_id: str,
    product: Product,
    state: str,
    reason: str | None = None,
    *,
    pending_since: int | None = None,
) -> bool:
    """Move ``product`` of the current version to ``state``, if that move is legal.

    Legal moves:

    - to ``pending`` from any state: work is (re)queued, which is always a
      deliberate act (a new version, a reindex, a graph refresh, an operator
      re-queue or rebuild);
    - from ``pending``, or from no recorded state (rows and jobs from before
      the state existed), to ``done``, ``failed`` or ``not_applicable``.

    Everything else is refused and logged, so a late or stray write cannot turn
    ``failed`` into ``done`` or ``done`` into ``failed``, and a repeated
    outcome changes nothing and logs nothing. A replaced or deleted version is never
    written: its work stopped because the document moved on.

    The conditions are checked on the row the UPDATE writes, in one statement,
    so a replacement committing meanwhile or a concurrent writer is seen: of
    two concurrent failures only the first moves the row out of pending, and
    only that one logs ``derived_product_failed`` (error level, ids only).

    ``pending_since`` narrows the move to the wait the caller saw: the write
    lands only while the product's ``_state_at`` is still that value. The
    stale-pending reaper passes it, so work queued again between its read and
    its write keeps its new pending instead of being failed.

    Returns whether the row now holds ``state``.
    """
    from knowledge_ingest.pg_store import _SENTINEL, PURGE_PENDING

    row = await conn.fetchrow(
        """
        UPDATE knowledge.artifacts AS a
        SET extra = COALESCE(a.extra, '{}'::jsonb) || $2::jsonb
        WHERE a.id = $1::uuid AND a.belief_time_end = $4 AND a.index_status <> $5
          AND ($6::text = 'pending' OR COALESCE(a.extra->>($3 || '_state'), 'pending') = 'pending')
          AND ($7::bigint IS NULL OR (a.extra->>($3 || '_state_at'))::bigint = $7)
        RETURNING a.org_id, a.kb_slug
        """,
        artifact_id,
        json.dumps(_state_patch(product, state, reason)),
        product,
        _SENTINEL,
        PURGE_PENDING,
        state,
        pending_since,
    )
    if row is not None:
        if state == FAILED:
            logger.error(
                "derived_product_failed",
                artifact_id=artifact_id,
                org_id=row["org_id"],
                kb_slug=row["kb_slug"],
                product=product,
                reason=reason,
            )
        return True
    held = await conn.fetchrow(
        """
        SELECT extra->>($2 || '_state') AS state, extra->>($2 || '_reason') AS reason
        FROM knowledge.artifacts
        WHERE id = $1::uuid AND belief_time_end = $3 AND index_status <> $4
        """,
        artifact_id,
        product,
        _SENTINEL,
        PURGE_PENDING,
    )
    if held is None:
        return False
    if (held["state"], held["reason"]) == (state, reason):
        return True
    logger.warning(
        "derived_state_transition_refused",
        artifact_id=artifact_id,
        product=product,
        held=held["state"],
        held_reason=held["reason"],
        to=state,
        to_reason=reason,
    )
    return False


async def record_outcome(
    conn: asyncpg.Connection,
    artifact_id: str,
    product: Product,
    state: str,
    reason: str | None = None,
) -> bool:
    """``set_product_state`` for the end of the work, plus what it means for the text.

    A reindex leaves ``index_status`` pending while enrichment rewrites the
    version's points, so the enrichment outcome also ends that.
    """
    from knowledge_ingest import pg_store

    current = await set_product_state(conn, artifact_id, product, state, reason)
    if current and product == "enrichment":
        await pg_store.finish_pending_index_status(
            conn, artifact_id, FAILED if state == FAILED else "synced"
        )
    return current


async def queue_derived_work(
    conn: asyncpg.Connection,
    artifact_id: str,
    product: Product,
    configured_task: Any,
    /,
    **job_args: Any,
) -> bool:
    """Queue a product's job; the document records pending, or failed if queueing fails.

    Pending is written first, so a job that runs at once cannot have its
    outcome overwritten. When the defer raises, the row is already committed
    and a retry of this request would find the content unchanged and queue
    nothing, so a failed request alone would lose the work silently: the
    document records failed (queue_unavailable, alerted) and the error is
    raised. Returns False when a job for this version was already waiting.
    """
    from procrastinate.exceptions import AlreadyEnqueued

    await set_product_state(conn, artifact_id, product, PENDING)
    try:
        await configured_task.defer_async(**job_args)
    except AlreadyEnqueued:
        # A job for this exact version is waiting already and covers it.
        logger.info("derived_work_already_queued", artifact_id=artifact_id, product=product)
        return False
    except Exception:
        await record_outcome(conn, artifact_id, product, FAILED, "queue_unavailable")
        raise
    return True
