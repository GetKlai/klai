"""Fire-and-forget product event emission to the portal database.

Events are inserted into the portal's ``product_events`` table via a
lightweight asyncpg connection pool.  The ``org_id`` foreign key is
resolved automatically from the Zitadel ``tenant_id`` using a sub-query.

Pool lifecycle is managed by the FastAPI lifespan (init_pool / close_pool).
Connection params are taken from individual settings (no DSN parsing needed).

SPEC-SEC-HYGIENE-001 REQ-40: ``_pending`` is capped at
``settings.retrieval_events_max_pending`` (default 1000). When the cap is
hit ``emit_event`` drops the new event, increments
``retrieval_events_dropped_total`` and emits a rate-limited
``retrieval_events_cap_hit`` warning so operators see the back-pressure
without flooding the log pipeline.
"""

from __future__ import annotations

import asyncio
import json
import time

import asyncpg
import structlog

from retrieval_api.config import settings
from retrieval_api.metrics import retrieval_events_dropped_total

logger = structlog.get_logger()

_pool: asyncpg.Pool | None = None
_pending: set[asyncio.Task] = set()

# REQ-40.2: rate-limit cap-hit warnings to ~1/min so a flood of drops
# cannot itself flood the log pipeline.
_CAP_LOG_INTERVAL_SECONDS = 60.0
_last_cap_log_time: float = 0.0

_INSERT_SQL = """
    INSERT INTO product_events (event_type, org_id, user_id, properties)
    VALUES (
        $1,
        (SELECT id FROM portal_orgs WHERE zitadel_org_id = $2),
        $3,
        $4::jsonb
    )
"""


async def init_pool() -> None:
    """Create the portal events connection pool. Call from FastAPI lifespan."""
    global _pool
    if not settings.portal_events_host:
        logger.info("events: portal_events_host not set, event emission disabled")
        return
    try:
        _pool = await asyncpg.create_pool(
            host=settings.portal_events_host,
            port=settings.portal_events_port,
            user=settings.portal_events_user,
            password=settings.portal_events_password,
            database=settings.portal_events_db,
            min_size=1,
            max_size=2,
        )
        logger.info("events: portal DB pool created")
    except Exception:
        logger.warning("events: failed to create portal DB pool", exc_info=True)


async def close_pool() -> None:
    """Close the portal events connection pool. Call from FastAPI lifespan."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def emit_event(
    event_type: str,
    tenant_id: str | None = None,
    user_id: str | None = None,
    properties: dict | None = None,
) -> None:
    """Schedule a non-blocking product event insert.

    Returns immediately; the insert runs in a background asyncio task.
    """

    async def _insert() -> None:
        if _pool is None:
            return
        try:
            await _pool.execute(
                _INSERT_SQL,
                event_type,
                tenant_id,
                user_id,
                json.dumps(properties or {}),
            )
        except Exception:
            logger.warning(
                "emit_event failed",
                event_type=event_type,
                tenant_id=tenant_id,
                exc_info=True,
            )

    # SPEC-SEC-HYGIENE-001 REQ-40.1: bounded pending set. Drop new events
    # rather than letting the in-flight task set grow without limit when
    # the insert pipeline can't keep up (e.g. portal DB blip + traffic
    # spike). Drops are observable via Prometheus + a rate-limited log.
    if len(_pending) >= settings.retrieval_events_max_pending:
        retrieval_events_dropped_total.inc()
        global _last_cap_log_time
        now = time.monotonic()
        if now - _last_cap_log_time >= _CAP_LOG_INTERVAL_SECONDS:
            _last_cap_log_time = now
            logger.warning(
                "retrieval_events_cap_hit",
                pending=len(_pending),
                cap=settings.retrieval_events_max_pending,
                event_type=event_type,
            )
        return

    try:
        task = asyncio.create_task(_insert())
        _pending.add(task)
        task.add_done_callback(_pending.discard)
    except RuntimeError:
        logger.warning("emit_event: no running event loop, event dropped", event_type=event_type)
