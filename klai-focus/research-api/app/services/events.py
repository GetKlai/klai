"""Fire-and-forget product event emission via the existing database connection.

Events are inserted into the portal's ``product_events`` table (public schema)
using the same SQLAlchemy engine the rest of research-api uses.  The ``org_id``
foreign key is resolved automatically from the Zitadel ``tenant_id``.
"""

from __future__ import annotations

import asyncio
import json

import structlog
from sqlalchemy import text

from app.core.database import AsyncSessionLocal

logger = structlog.get_logger()

_pending: set[asyncio.Task] = set()

_INSERT_SQL = text("""
    INSERT INTO product_events (event_type, org_id, user_id, properties)
    VALUES (
        :event_type,
        (SELECT id FROM portal_orgs WHERE zitadel_org_id = :tenant_id),
        :user_id,
        CAST(:properties AS jsonb)
    )
""")


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
        try:
            async with AsyncSessionLocal() as session:
                await session.execute(
                    _INSERT_SQL,
                    {
                        "event_type": event_type,
                        "tenant_id": tenant_id,
                        "user_id": user_id,
                        "properties": json.dumps(properties or {}),
                    },
                )
                await session.commit()
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
