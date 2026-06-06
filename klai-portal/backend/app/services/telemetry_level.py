"""SPEC-PRIVACY-QUERY-SHADOW-001 — shared telemetry-level service-layer.

Single source of truth for changing a tenant's `portal_orgs.telemetry_level`,
shared between:

- the internal-admin endpoint (REQ-11; ``/internal/admin/orgs/{org_id}/telemetry-level``)
- the tenant self-service endpoint (REQ-15; ``/api/orgs/me/telemetry-level``)

Both endpoints share the DB-update + cache-invalidation + audit-log
behaviour. They differ only in:

- Authorization (klai-operator vs tenant-admin)
- Default ``reason`` ("internal admin" vs "tenant self-service via admin UI")
- ``operator_kind`` audit field

Cache invalidation: deletes every ``kb_ver:{org_id}:*`` Redis key for the
org so the LiteLLM hook picks up the new level on the next request. The
version-keyed feature cache (``kb_feature:{org}:{user}:{version}``) becomes
unreachable once the version pointer is gone, so it expires naturally on
its own TTL. Failure is logged but never blocks the DB-write.
"""

from __future__ import annotations

import json
from typing import Literal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.portal import PortalOrg
from app.services.audit import log_event
from app.services.litellm_cache import invalidate_kb_cache

logger = structlog.get_logger()

TelemetryLevel = Literal["off", "shadow", "full"]
VALID_LEVELS: tuple[TelemetryLevel, ...] = ("off", "shadow", "full")


async def set_telemetry_level(
    db: AsyncSession,
    org_id: int,
    new_level: TelemetryLevel,
    *,
    operator_kind: Literal["operator", "tenant_admin"],
    operator_user_id: str,
    reason: str,
) -> tuple[TelemetryLevel, TelemetryLevel]:
    """Update ``portal_orgs.telemetry_level`` for ``org_id`` and audit-log.

    Returns ``(old_level, new_level)``. Caller is responsible for any
    further response shaping. Validation of ``new_level`` is enforced by
    the ``Literal`` type at the API boundary (FastAPI/pydantic) — this
    function asserts again as a defense-in-depth check.

    Caller MUST have already authenticated and (for ``tenant_admin``)
    enforced same-org scoping. This function does not check authorization.
    """
    if new_level not in VALID_LEVELS:
        # Defense-in-depth: caller's pydantic Literal already restricts
        # this, but keep the assertion so a future direct call from a
        # background task can't inject a bogus value.
        raise ValueError(f"invalid telemetry_level: {new_level!r}")

    if not reason or not reason.strip():
        raise ValueError("reason must be non-empty")
    if len(reason) > 500:
        raise ValueError("reason exceeds 500 char limit")

    # SELECT FOR UPDATE serializes concurrent toggles on the same org.
    result = await db.execute(select(PortalOrg).where(PortalOrg.id == org_id).with_for_update())
    org = result.scalar_one_or_none()
    if org is None:
        raise LookupError(f"org_id={org_id} not found")

    old_level: TelemetryLevel = org.telemetry_level  # type: ignore[assignment]
    if old_level == new_level:
        # Idempotent no-op: still write an audit row so the operator's
        # action is recorded, but skip the cache invalidation churn.
        logger.info(
            "telemetry_level_unchanged",
            org_id=org_id,
            level=new_level,
            operator_kind=operator_kind,
        )
    else:
        org.telemetry_level = new_level
        await db.commit()

    # Cache invalidation runs even on no-op so a stuck hook can be nudged
    # by re-applying the same level (operator escape hatch). Org-wide
    # (librechat_user_id=None) keyed on the Zitadel org-id string the hook uses.
    await invalidate_kb_cache(org.zitadel_org_id)

    # Audit log uses raw SQL via log_event's own session — survives any
    # caller transaction rollback. action='telemetry_level_changed' is
    # the canonical name surfaced to operators in the audit UI.
    audit_details = {
        "old_level": old_level,
        "new_level": new_level,
        "reason": reason,
        "operator_kind": operator_kind,
        "operator_user_id": operator_user_id,
    }
    try:
        await log_event(
            org_id=org_id,
            actor=operator_user_id,
            action="telemetry_level_changed",
            resource_type="portal_org",
            resource_id=str(org_id),
            details=audit_details,
        )
    except Exception:
        # log_event is fire-and-forget per its own contract; if even that
        # falls over, surface it in our service log so we still notice.
        logger.warning(
            "telemetry_level_audit_log_failed",
            org_id=org_id,
            details=json.dumps(audit_details),
            exc_info=True,
        )

    logger.info(
        "telemetry_level_changed",
        org_id=org_id,
        old_level=old_level,
        new_level=new_level,
        operator_kind=operator_kind,
        operator_user_id=operator_user_id,
    )
    return old_level, new_level
