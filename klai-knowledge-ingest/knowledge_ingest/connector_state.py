"""Connector lifecycle state lookup.

SPEC-CONNECTOR-DELETE-LIFECYCLE-001 REQ-07: every enrichment-pipeline task
that writes connector-scoped data MUST call ``connector_is_active`` at the
top and abort if it returns ``False``. Closes the in-flight regrow window
between portal DELETE and procrastinate cancel-jobs completion.

The lookup hits ``portal_connectors.state`` (a separate schema, but same
``klai`` database — knowledge-ingest already shares the connection pool
with portal-api). A small process-local cache keeps the query off the hot
path: a single-instance worker enriching N chunks for the same connector
runs the lookup once per cache window.

Fail-closed: any DB error or "row not found" returns ``False`` so the
enrichment task aborts. We would rather miss an enrichment than write a
chunk that becomes orphan data.
"""

from __future__ import annotations

import time

import structlog

from knowledge_ingest.db import get_pool

logger = structlog.get_logger()

# Cache window in seconds. Keep small: we want the guard to react fast to a
# state flip, and a single worker only processes a connector for at most a
# few minutes at a time. 5s is the same trade-off used by the metrics-cache
# in retrieval-api.
_CACHE_TTL_SECONDS = 5.0

# {connector_id: (state, expires_at_monotonic)}
_state_cache: dict[str, tuple[str, float]] = {}


async def connector_is_active(connector_id: str | None) -> bool:
    """Return True iff the connector exists and has state='active'.

    Returns False on:
      - connector_id is None or empty (chunk has no source connector — let
        the caller decide; defensive default is False, but in practice this
        path is exercised only by chunks that DO have source_connector_id)
      - connector_id is unknown to portal_connectors (deleted, hard-purged)
      - state is anything other than 'active' (e.g. 'deleting')
      - any database error (fail-closed)
    """
    if not connector_id:
        return False

    state = await get_connector_state(connector_id)
    return state == "active"


async def get_connector_state(connector_id: str) -> str | None:
    """Return the ``state`` value from ``portal_connectors`` or None.

    None means: row not found, or query failed. Caller decides what to do
    with that — the typical caller (``connector_is_active``) treats it as
    "not active".

    Cached for ``_CACHE_TTL_SECONDS`` per connector_id to keep enrichment
    tasks off the DB hot path.
    """
    now = time.monotonic()
    cached = _state_cache.get(connector_id)
    if cached is not None and cached[1] > now:
        return cached[0]

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT state FROM portal_connectors WHERE id = $1::uuid",
                connector_id,
            )
        state: str | None = row["state"] if row is not None else None
    except Exception:
        # Fail-closed. Log so we notice if the DB is down causing mass
        # enrichment-aborts, but never raise: the caller is an enrichment
        # task and must remain idempotent + safe.
        logger.exception(
            "connector_state_lookup_failed",
            connector_id=connector_id,
        )
        return None

    # Only cache definite answers (state present), not lookup failures.
    # Otherwise a transient DB hiccup poisons the cache for 5 seconds.
    if state is not None:
        _state_cache[connector_id] = (state, now + _CACHE_TTL_SECONDS)
    return state


def invalidate_cache(connector_id: str | None = None) -> None:
    """Clear the cache (one entry or all). Test-only helper.

    Prod code never invalidates explicitly — the 5s TTL handles staleness
    naturally and the orchestrator-worker doesn't need fast feedback (the
    cancel-jobs step inside ``purge_connector`` is the synchronous fence).
    """
    if connector_id is None:
        _state_cache.clear()
    else:
        _state_cache.pop(connector_id, None)
