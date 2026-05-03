"""Audit services package.

Two distinct audit surfaces:

- ``log_event`` — append-only access-control audit log (portal_audit_log).
  Fire-and-forget pattern with own session so the caller's transaction can
  roll back without losing the audit row. Used by every admin endpoint.

- ``tenant_lifecycle.emit_lifecycle_event`` — synchronous tenant-lifecycle
  audit (tenant_lifecycle_events) emitted within the deprovisioning
  orchestrator's transaction. Survives portal_orgs hard-delete by design
  (no FK to portal_orgs). See SPEC-INFRA-TENANT-DELETE-001 R6.

The two surfaces serve different audiences and have opposite consistency
contracts (fire-and-forget vs transactional), so they live in separate
modules. ``log_event`` is re-exported from this package init for backward
compatibility with the original ``app.services.audit`` flat-module layout.
"""

import json

import structlog
from sqlalchemy import text

from app.core.database import AsyncSessionLocal

logger = structlog.get_logger()

# Raw SQL avoids ORM's implicit INSERT...RETURNING which triggers the
# SELECT RLS policy. That policy fails when app.current_org_id is unset
# (login/logout events with org_id=0).
_INSERT_SQL = text(
    "INSERT INTO portal_audit_log "
    "(org_id, actor_user_id, action, resource_type, resource_id, details) "
    "VALUES (:org_id, :actor, :action, :resource_type, :resource_id, CAST(:details AS jsonb))"
)


# @MX:ANCHOR fan_in=10+ — log_event is the single write path to the audit log.
#                          All access control events must go through this function.
async def log_event(
    org_id: int,
    actor: str,
    action: str,
    resource_type: str,
    resource_id: str,
    details: dict | None = None,
) -> None:
    """Write an immutable audit log entry.

    Opens its own database session so the insert commits independently
    of the caller's transaction. Callers often raise HTTPException after
    logging, which rolls back the request session and any SAVEPOINTs.
    """
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(
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
            await session.commit()
    except Exception:
        logger.exception(
            "Audit log write failed (non-fatal)",
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
        )


__all__ = ["log_event"]
