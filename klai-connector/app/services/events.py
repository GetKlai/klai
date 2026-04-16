"""Fire-and-forget product event emission to the shared klai database.

SPEC-CRAWL-003 REQ-15: emit `knowledge.sync_quality_degraded` when a sync's
quality_status transitions from healthy to degraded or failed.

The connector and portal-api share the same ``klai`` Postgres database, so the
connector writes directly to ``product_events`` instead of going through an
HTTP endpoint. This mirrors the pattern in
``klai-portal/backend/app/services/events.py`` (see also
``.claude/rules/klai/infra/observability.md`` section "Product events").

Design notes:
- Raw SQL is required because the ORM adds implicit ``RETURNING`` which
  triggers the ``tenant_read`` RLS policy on ``product_events`` and fails when
  no ``app.current_org_id`` GUC is set (the connector has no tenant context).
  The ``tenant_write`` policy is permissive (``WITH CHECK (true)``).
- Zitadel org IDs are varchar(64); ``product_events.org_id`` is the
  ``portal_orgs.id`` integer FK. We resolve via a ``SELECT`` on
  ``portal_orgs`` and fall back to ``NULL`` on any lookup failure.
- Fire-and-forget: errors are logged at warning level and swallowed so that
  sync runs never fail due to event-emission issues.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from sqlalchemy import text

from app.core.database import session_maker
from app.core.logging import get_logger

logger = get_logger(__name__)

# Strong references prevent GC of background insert tasks.
_pending: set[asyncio.Task[Any]] = set()


def emit_product_event(
    event_type: str,
    *,
    zitadel_org_id: str | None = None,
    properties: dict[str, Any] | None = None,
) -> None:
    """Schedule a non-blocking insert into the shared ``product_events`` table.

    Args:
        event_type: Event type string, e.g. ``"knowledge.sync_quality_degraded"``.
        zitadel_org_id: Zitadel resource-owner ID (varchar). Resolved to
            ``portal_orgs.id`` before insert. ``None`` is allowed for
            tenant-agnostic events.
        properties: JSON-serializable dict to store in the ``properties`` column.
    """

    async def _insert() -> None:
        if session_maker is None:
            logger.warning(
                "emit_product_event skipped: database not initialised (event_type=%s)",
                event_type,
            )
            return

        try:
            async with session_maker() as session:
                org_id: int | None = None
                if zitadel_org_id:
                    result = await session.execute(
                        text("SELECT id FROM portal_orgs WHERE zitadel_org_id = :z"),
                        {"z": zitadel_org_id},
                    )
                    row = result.first()
                    if row is not None:
                        org_id = int(row[0])

                await session.execute(
                    text(
                        """
                        INSERT INTO product_events (event_type, org_id, properties)
                        VALUES (:event_type, :org_id, CAST(:properties AS jsonb))
                        """
                    ),
                    {
                        "event_type": event_type,
                        "org_id": org_id,
                        "properties": json.dumps(properties or {}),
                    },
                )
                await session.commit()
        except Exception:
            logger.warning(
                "emit_product_event failed for event_type=%s zitadel_org_id=%s",
                event_type,
                zitadel_org_id,
                exc_info=True,
            )

    try:
        task = asyncio.create_task(_insert())
        _pending.add(task)
        task.add_done_callback(_pending.discard)
    except RuntimeError:
        logger.warning(
            "emit_product_event: no running event loop, event_type=%s dropped",
            event_type,
        )
