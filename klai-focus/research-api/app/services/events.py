"""Fire-and-forget product event emission to the portal database.

Events are inserted into the portal's ``product_events`` table via a
lightweight asyncpg connection pool.  The ``org_id`` foreign key is
resolved automatically from the Zitadel ``tenant_id`` using a sub-query.
"""

from __future__ import annotations

import asyncio
import json

import asyncpg
import structlog

from app.core.config import settings

logger = structlog.get_logger()

_pool: asyncpg.Pool | None = None
_pending: set[asyncio.Task] = set()

_INSERT_SQL = """
    INSERT INTO product_events (event_type, org_id, user_id, properties)
    VALUES (
        $1,
        (SELECT id FROM portal_orgs WHERE zitadel_org_id = $2),
        $3,
        $4::jsonb
    )
"""


async def _get_pool() -> asyncpg.Pool | None:
    global _pool
    if _pool is not None:
        return _pool
    dsn = settings.portal_events_dsn
    if not dsn:
        return None
    try:
        _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    except Exception:
        logger.warning("events: failed to create portal DB pool", exc_info=True)
        return None
    return _pool


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
        pool = await _get_pool()
        if pool is None:
            return
        try:
            await pool.execute(
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

    try:
        task = asyncio.create_task(_insert())
        _pending.add(task)
        task.add_done_callback(_pending.discard)
    except RuntimeError:
        logger.warning("emit_event: no running event loop, event dropped", event_type=event_type)
