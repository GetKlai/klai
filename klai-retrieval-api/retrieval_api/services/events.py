"""Fire-and-forget product event emission to the portal database.

Events are inserted into the portal's ``product_events`` table via a
lightweight asyncpg connection pool.  The ``org_id`` foreign key is
resolved automatically from the Zitadel ``tenant_id`` using a sub-query.

Pool lifecycle is managed by the FastAPI lifespan (init_pool / close_pool).
"""

from __future__ import annotations

import asyncio
import json
from urllib.parse import unquote, urlparse

import asyncpg
import structlog

from retrieval_api.config import settings

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


async def init_pool() -> None:
    """Create the portal events connection pool. Call from FastAPI lifespan."""
    global _pool
    dsn = settings.portal_events_dsn
    if not dsn:
        logger.info("events: portal_events_dsn not set, event emission disabled")
        return
    try:
        parsed = urlparse(dsn)
        _pool = await asyncpg.create_pool(
            host=parsed.hostname,
            port=parsed.port or 5432,
            user=unquote(parsed.username or ""),
            password=unquote(parsed.password or ""),
            database=parsed.path.lstrip("/"),
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

    try:
        task = asyncio.create_task(_insert())
        _pending.add(task)
        task.add_done_callback(_pending.discard)
    except RuntimeError:
        logger.warning("emit_event: no running event loop, event dropped", event_type=event_type)
