"""
Audit log service -- write immutable access control event entries.

The audit log is append-only. No UPDATE or DELETE operations are issued
against portal_audit_log from this module or anywhere in the application.

@MX:ANCHOR fan_in=10+ -- log_event is the single write path to the audit log.
                          All access control events must go through this function.
"""

import logging

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import PortalAuditLog

# Use the Core table to avoid ORM's implicit INSERT...RETURNING.
# PostgreSQL evaluates SELECT RLS policies on RETURNING clauses;
# the tenant_isolation_read policy fails when app.current_org_id is
# unset (login/logout events with org_id=0).
_table = PortalAuditLog.__table__

logger = logging.getLogger(__name__)


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
    try:
        async with db.begin_nested():
            await db.execute(
                insert(_table).values(
                    org_id=org_id,
                    actor_user_id=actor,
                    action=action,
                    resource_type=resource_type,
                    resource_id=str(resource_id),
                    details=details,
                )
            )
    except Exception:
        logger.exception(
            "Audit log write failed (non-fatal): action=%s resource_type=%s resource_id=%s",
            action,
            resource_type,
            resource_id,
        )
