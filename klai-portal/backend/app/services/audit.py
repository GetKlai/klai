"""
Audit log service -- write immutable access control event entries.

The audit log is append-only. No UPDATE or DELETE operations are issued
against portal_audit_log from this module or anywhere in the application.

@MX:ANCHOR fan_in=10+ -- log_event is the single write path to the audit log.
                          All access control events must go through this function.
"""

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Raw SQL avoids ORM's implicit INSERT...RETURNING which triggers the
# SELECT RLS policy. That policy fails when app.current_org_id is unset
# (login/logout events with org_id=0).
_INSERT_SQL = text(
    "INSERT INTO portal_audit_log "
    "(org_id, actor_user_id, action, resource_type, resource_id, details) "
    "VALUES (:org_id, :actor, :action, :resource_type, :resource_id, :details::jsonb)"
)


async def log_event(
    db: AsyncSession,
    org_id: int,
    actor: str,
    action: str,
    resource_type: str,
    resource_id: str,
    details: dict | None = None,
) -> None:
    """Write an immutable audit log entry.

    Uses a SAVEPOINT (begin_nested) so a failure rolls back only the audit
    insert, not the caller's transaction. Audit failures must not block
    business operations.
    """
    import json

    try:
        async with db.begin_nested():
            await db.execute(
                _INSERT_SQL,
                {
                    "org_id": org_id,
                    "actor": actor,
                    "action": action,
                    "resource_type": resource_type,
                    "resource_id": str(resource_id),
                    "details": json.dumps(details) if details else None,
                },
            )
    except Exception:
        logger.exception(
            "Audit log write failed (non-fatal): action=%s resource_type=%s resource_id=%s",
            action,
            resource_type,
            resource_id,
        )
