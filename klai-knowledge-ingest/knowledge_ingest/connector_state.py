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

"Row not found" returns ``False``, so the job is abandoned rather than writing
data for a connector that is gone. A database error raises, so the job fails
and is retried instead of being recorded as done.

The run fence (``connector_resource_fences``, one generation per connector) is
for crawl runs only: a newer crawl of the same connector stops the older crawl.
Enrichment and graph jobs belong to a document version and never consult it.
"""

from __future__ import annotations

import time
from enum import StrEnum

import structlog

from knowledge_ingest.db import get_pool, tenant_scoped_connection
from knowledge_ingest.resource_jobs import parse_connector_resource_key

logger = structlog.get_logger()

# Cache window in seconds. Keep small: we want the guard to react fast to a
# state flip, and a single worker only processes a connector for at most a
# few minutes at a time. 5s is the same trade-off used by the metrics-cache
# in retrieval-api.
_CACHE_TTL_SECONDS = 5.0

# {connector_id: (state, expires_at_monotonic)}
_state_cache: dict[str, tuple[str, float]] = {}


class FenceState(StrEnum):
    ACTIVE = "active"
    STALE_GENERATION = "stale_generation"
    DELETING = "deleting"
    DELETED = "deleted"
    UNKNOWN_ERROR = "unknown_error"


_fence_cache: dict[str, tuple[FenceState, float]] = {}


async def activate_connector_resource(conn, resource_key: str) -> None:
    resource = parse_connector_resource_key(resource_key)
    await conn.execute(
        """
        INSERT INTO knowledge.connector_resource_fences
            (org_id, kb_slug, connector_id, current_generation, state, updated_at)
        VALUES ($1, $2, $3, $4, 'active', now())
        ON CONFLICT (org_id, kb_slug, connector_id) DO UPDATE
          SET current_generation = EXCLUDED.current_generation,
              state = 'active',
              updated_at = now()
        """,
        resource.org_id,
        resource.kb_slug,
        resource.connector_id,
        resource.generation,
    )
    _fence_cache.pop(resource_key, None)


async def get_current_connector_resource_key(conn, resource_key: str) -> str | None:
    resource = parse_connector_resource_key(resource_key)
    generation = await conn.fetchval(
        """
        SELECT current_generation
          FROM knowledge.connector_resource_fences
         WHERE org_id = $1 AND kb_slug = $2 AND connector_id = $3
        """,
        resource.org_id,
        resource.kb_slug,
        resource.connector_id,
    )
    if generation is None:
        return None
    from knowledge_ingest.resource_jobs import connector_resource_key

    return connector_resource_key(
        resource.org_id, resource.kb_slug, resource.connector_id, generation
    )


async def mark_connector_resource_deleting(conn, resource_key: str) -> None:
    resource = parse_connector_resource_key(resource_key)
    await conn.execute(
        """
        UPDATE knowledge.connector_resource_fences
           SET state = 'deleting', updated_at = now()
         WHERE org_id = $1
           AND kb_slug = $2
           AND connector_id = $3
           AND current_generation = $4
        """,
        resource.org_id,
        resource.kb_slug,
        resource.connector_id,
        resource.generation,
    )
    _fence_cache.pop(resource_key, None)


async def check_connector_resource_fence(resource_key: str, *, fresh: bool = False) -> FenceState:
    """Fail closed unless this is the connector's active current generation."""
    now = time.monotonic()
    cached = _fence_cache.get(resource_key)
    if not fresh and cached is not None and cached[1] > now:
        return cached[0]
    try:
        resource = parse_connector_resource_key(resource_key)
        async with tenant_scoped_connection(resource.org_id) as conn:
            row = await conn.fetchrow(
                """
                SELECT f.current_generation,
                       f.state,
                       p.state AS connector_state
                  FROM knowledge.connector_resource_fences AS f
                  LEFT JOIN portal_connectors AS p ON p.id = f.connector_id::uuid
                 WHERE f.org_id = $1 AND f.kb_slug = $2 AND f.connector_id = $3
                """,
                resource.org_id,
                resource.kb_slug,
                resource.connector_id,
            )
    except Exception:
        logger.warning(
            "connector_resource_fence_lookup_failed",
            resource_key=resource_key,
            exc_info=True,
        )
        return FenceState.UNKNOWN_ERROR

    if row is None:
        result = FenceState.DELETED
    elif row.get("connector_state", "active") is None:
        result = FenceState.DELETED
    elif row.get("connector_state", "active") != "active":
        result = FenceState.DELETING
    elif str(row["current_generation"]) != resource.generation:
        result = FenceState.STALE_GENERATION
    elif row["state"] == "deleting":
        result = FenceState.DELETING
    elif row["state"] == "active":
        result = FenceState.ACTIVE
    else:
        result = FenceState.UNKNOWN_ERROR
    if not fresh:
        _fence_cache[resource_key] = (result, now + _CACHE_TTL_SECONDS)
    return result


async def connector_is_active(connector_id: str | None, *, fresh: bool = False) -> bool:
    """Return True iff the connector exists and has state='active'.

    Returns False on:
      - connector_id is None or empty (chunk has no source connector — let
        the caller decide; defensive default is False, but in practice this
        path is exercised only by chunks that DO have source_connector_id)
      - connector_id is unknown to portal_connectors (deleted, hard-purged)
      - state is anything other than 'active' (e.g. 'deleting')

    Raises when the lookup itself fails. ``fresh`` skips the cache: a check
    that decides about a write just made or about to be made must see a
    connector that flipped to ``deleting`` a moment ago.
    """
    if not connector_id:
        return False

    state = await get_connector_state(connector_id, fresh=fresh)
    return state == "active"


async def get_connector_state(connector_id: str, *, fresh: bool = False) -> str | None:
    """Return the ``state`` value from ``portal_connectors`` or None.

    None means the row is not found; a failed query raises. The typical
    caller (``connector_is_active``) treats None as "not active".

    Cached for ``_CACHE_TTL_SECONDS`` per connector_id to keep enrichment
    tasks off the DB hot path.
    """
    now = time.monotonic()
    cached = _state_cache.get(connector_id)
    if not fresh and cached is not None and cached[1] > now:
        return cached[0]

    # A failed lookup raises. Returning "not active" here made a database
    # hiccup end every enrichment and graph job it touched as if the connector
    # were gone, and the queue recorded those jobs as succeeded; raising lets
    # the job fail and be retried instead.
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT state FROM portal_connectors WHERE id = $1::uuid",
            connector_id,
        )
    state: str | None = row["state"] if row is not None else None

    # Only cache a connector that exists: a deleted one stays deleted, and an
    # unknown id is rare enough to look up again.
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
        _fence_cache.clear()
    else:
        _state_cache.pop(connector_id, None)
