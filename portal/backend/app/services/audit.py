"""
Audit log service -- write immutable access control event entries.

The audit log is append-only. No UPDATE or DELETE operations are issued
against portal_audit_log from this module or anywhere in the application.

@MX:ANCHOR fan_in=10+ -- log_event is the single write path to the audit log.
                          All access control events must go through this function.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import PortalAuditLog

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

    Uses flush() (not commit()) so the entry participates in the caller's
    transaction. If the audit write fails, the exception is logged but does
    not roll back the parent transaction -- audit failures must not block
    business operations.
    """
    try:
        entry = PortalAuditLog(
            org_id=org_id,
            actor_user_id=actor,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id),
            details=details,
        )
        db.add(entry)
        await db.flush()
    except Exception:
        logger.exception(
            "Audit log write failed (non-fatal): action=%s resource_type=%s resource_id=%s",
            action,
            resource_type,
            resource_id,
        )
