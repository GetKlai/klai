"""
Fire-and-forget product event emission.

Usage:
    emit_event("signup", org_id=org.id, user_id=user_id, properties={"plan": "core"})

Events are written to `product_events` in a separate lightweight transaction.
Failures are logged at WARNING level; the caller is never affected.
"""

import asyncio
import logging

from app.core.database import AsyncSessionLocal
from app.models.events import ProductEvent

log = logging.getLogger(__name__)

# Keep references to prevent background tasks from being garbage-collected.
_pending: set[asyncio.Task] = set()


def emit_event(
    event_type: str,
    org_id: int | None = None,
    user_id: str | None = None,
    properties: dict | None = None,
) -> None:
    """Schedule a non-blocking product event insert.

    Returns immediately; the insert runs in a background asyncio task.
    Safe to call from any async FastAPI endpoint.
    """

    async def _insert() -> None:
        try:
            async with AsyncSessionLocal() as session:
                session.add(
                    ProductEvent(
                        event_type=event_type,
                        org_id=org_id,
                        user_id=user_id,
                        properties=properties or {},
                    )
                )
                await session.commit()
        except Exception:
            log.warning(
                "emit_event failed for event_type=%s org_id=%s",
                event_type,
                org_id,
                exc_info=True,
            )

    try:
        task = asyncio.create_task(_insert())
        _pending.add(task)
        task.add_done_callback(_pending.discard)
    except RuntimeError:
        log.warning("emit_event: no running event loop, event_type=%s dropped", event_type)
