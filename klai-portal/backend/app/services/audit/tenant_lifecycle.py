"""
Tenant lifecycle event audit helper.

Emits INSERT into ``tenant_lifecycle_events`` within the caller's existing
transaction. This is NOT fire-and-forget — a failure here causes the entire
finalize step to roll back, which is the intended behaviour (R6: audit must
not be lost silently).

# @MX:ANCHOR: synchronous insert, never fire-and-forget. SPEC-INFRA-TENANT-DELETE-001 R6.
# @MX:REASON: audit of a hard-delete must be atomic with the delete; if the
#   insert fails the finalize transaction rolls back and the operator retries.

Pattern mirrors app/services/events.py but:
- Uses raw text() to bypass SQLAlchemy ORM RETURNING + RLS triggers.
- Uses CAST(:param AS jsonb) per portal-backend.md SQLAlchemy+RLS rule.
- Runs synchronously within the caller's transaction (no separate session,
  no asyncio.create_task).
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()

_VALID_EVENT_TYPES = frozenset({"provisioned", "deprovisioned", "failed_deprovisioning"})
_VALID_ACTOR_TYPES = frozenset({"owner", "platform_admin", "system"})


async def emit_lifecycle_event(
    db: AsyncSession,
    *,
    event_type: str,
    org_id_snapshot: int,
    org_slug_snapshot: str,
    org_name_snapshot: str,
    actor_user_id: str | None,
    actor_type: str,
    properties: dict[str, Any] | None = None,
) -> None:
    """INSERT a tenant lifecycle event within the caller's transaction.

    The INSERT is committed as part of the caller's transaction — not in a
    separate session. A DB error here propagates up and rolls back the
    enclosing transaction.

    Args:
        db: The active async session. Must have an open transaction.
        event_type: One of 'provisioned', 'deprovisioned', 'failed_deprovisioning'.
        org_id_snapshot: The org's integer PK at event time.
        org_slug_snapshot: The org's slug at event time.
        org_name_snapshot: The org's display name at event time.
        actor_user_id: Zitadel user_id of the person who triggered the event.
            None for system-initiated events.
        actor_type: One of 'owner', 'platform_admin', 'system'.
        properties: Optional additional metadata stored as JSONB. Defaults to {}.
    """
    if event_type not in _VALID_EVENT_TYPES:
        raise ValueError(f"Invalid event_type {event_type!r}. Must be one of: {sorted(_VALID_EVENT_TYPES)}")
    if actor_type not in _VALID_ACTOR_TYPES:
        raise ValueError(f"Invalid actor_type {actor_type!r}. Must be one of: {sorted(_VALID_ACTOR_TYPES)}")

    await db.execute(
        text("""
            INSERT INTO tenant_lifecycle_events (
                event_type,
                org_id_snapshot,
                org_slug_snapshot,
                org_name_snapshot,
                actor_user_id,
                actor_type,
                properties
            )
            VALUES (
                :event_type,
                :org_id,
                :slug,
                :name,
                :actor,
                :actor_type,
                CAST(:props AS jsonb)
            )
        """),
        {
            "event_type": event_type,
            "org_id": org_id_snapshot,
            "slug": org_slug_snapshot,
            "name": org_name_snapshot,
            "actor": actor_user_id,
            "actor_type": actor_type,
            "props": json.dumps(properties or {}),
        },
    )
    logger.info(
        "tenant_lifecycle_event_emitted",
        event_type=event_type,
        org_id=org_id_snapshot,
        org_slug=org_slug_snapshot,
        actor_type=actor_type,
    )
