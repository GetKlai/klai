"""The state of each derived product of a document version: enrichment and graph.

A document version (one ``knowledge.artifacts`` row) has two products built
from its text after ingest: enrichment of its chunks and graph extraction. For
each, the row records exactly one state in ``extra``:

- ``pending``: queued or running;
- ``done``;
- ``failed`` with a reason: the work was needed for a current document and
  could not be done; the job that gave up ends failed too;
- ``not_applicable`` with a reason: policy said no (held out of the graph on
  purpose, no text to extract, too large for enrichment, switched off).

``product_state`` is the one reader. Rows written before these keys existed
are derived from the fields they have, and say ``unknown`` where those fields
cannot tell, never ``done`` by default. ``set_product_state`` is the one
writer; a transition to ``failed`` logs ``derived_product_failed`` once at
error level, which the ``derived_product_failed`` alert fires on. Nothing
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


def state_patch(product: Product, state: str, reason: str | None = None) -> dict[str, Any]:
    """The ``extra`` keys that record a state, for writers that merge ``extra`` anyway."""
    return {
        f"{product}_state": state,
        f"{product}_reason": reason,
        f"{product}_state_at": int(time.time()),
    }


async def set_product_state(
    conn: asyncpg.Connection,
    artifact_id: str,
    product: Product,
    state: str,
    reason: str | None = None,
) -> bool:
    """Record ``state`` for ``product`` on the artifact, if it is still the current version.

    A replaced or deleted version gets no state: its work stopped because the
    document moved on, which is not a failure. A transition to ``failed`` (not
    a repeat of the same failure) logs ``derived_product_failed`` at error level
    with ids only. Returns whether the row was current.
    """
    from knowledge_ingest.pg_store import _SENTINEL, PURGE_PENDING

    row = await conn.fetchrow(
        """
        WITH before AS (
            SELECT id, extra::jsonb->>($3 || '_state') AS state,
                   extra::jsonb->>($3 || '_reason') AS reason
            FROM knowledge.artifacts
            WHERE id = $1::uuid AND belief_time_end = $4 AND index_status <> $5
        )
        UPDATE knowledge.artifacts AS a
        SET extra = COALESCE(a.extra::jsonb, '{}'::jsonb) || $2::jsonb
        FROM before
        WHERE a.id = before.id
        RETURNING a.org_id, a.kb_slug, before.state AS previous_state,
                  before.reason AS previous_reason
        """,
        artifact_id,
        json.dumps(state_patch(product, state, reason)),
        product,
        _SENTINEL,
        PURGE_PENDING,
    )
    if row is None:
        return False
    if state == FAILED and (row["previous_state"], row["previous_reason"]) != (FAILED, reason):
        logger.error(
            "derived_product_failed",
            artifact_id=artifact_id,
            org_id=row["org_id"],
            kb_slug=row["kb_slug"],
            product=product,
            reason=reason,
        )
    return True


async def queue_derived_work(
    conn: asyncpg.Connection,
    artifact_id: str,
    product: Product,
    configured_task: Any,
    /,
    **job_args: Any,
) -> None:
    """Queue a product's job; the document records pending, or failed if queueing fails.

    Pending is written first, so a job that runs at once cannot have its
    ``done`` overwritten. When the defer raises, the row is already committed
    and a retry of this request would find the content unchanged and queue
    nothing, so a failed request alone would lose the work silently: the
    document records failed (queue_unavailable, alerted) and the request fails.
    """
    from procrastinate.exceptions import AlreadyEnqueued

    await set_product_state(conn, artifact_id, product, PENDING)
    try:
        await configured_task.defer_async(**job_args)
    except AlreadyEnqueued:
        # A job for this exact version is waiting already and covers it.
        logger.info("derived_work_already_queued", artifact_id=artifact_id, product=product)
    except Exception:
        await set_product_state(conn, artifact_id, product, FAILED, "queue_unavailable")
        raise
