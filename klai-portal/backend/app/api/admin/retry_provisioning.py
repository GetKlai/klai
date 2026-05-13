"""SPEC-PROV-001 M4 — admin-only retry endpoint for failed tenant provisioning.

POST /api/admin/orgs/{slug}/retry-provisioning

Callable only for orgs in `failed_rollback_complete` state. Other states are
rejected with 409 so admins do not accidentally re-start provisioning over a
run that is still mid-flight or needs manual cleanup.

Concurrency guarantee: `SELECT ... FOR UPDATE` on the target row serialises
concurrent retry clicks. The second caller reads the row after the first
committed the transition to `queued` and falls through to the
`not_in_retryable_state` branch.

Authorization (audit-tenant-isolation-2026-05-05 finding C-2):
The handler operates on a `slug` URL-parameter that may identify a tenant
DIFFERENT from the caller's own org (the failed-row predates any user-level
tenancy). This is a cross-tenant action and MUST be gated by
`_require_platform_admin` — only callers operating from the platform-admin
org (settings.platform_org_slug, default 'getklai') may invoke it. Without
this guard, any tenant-admin could revive any other tenant's failed-rollback
org. Mirrors the gating in `deprovision_org.py::deprovision_org_by_slug`
and `retry_deprovisioning`.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.permissions import UserPermissions, require_platform_admin
from app.models.portal import PortalOrg
from app.services.audit import log_event
from app.services.provisioning.orchestrator import provision_tenant

logger = structlog.get_logger()

router = APIRouter()


@router.post(
    "/orgs/{slug}/retry-provisioning",
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_provisioning(
    slug: str,
    background_tasks: BackgroundTasks,
    perms: UserPermissions = Depends(require_platform_admin()),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Retry provisioning for an org in `failed_rollback_complete` state.

    Returns 202 + `{"status": "queued"}` on success.
    Returns 409 with `error` code for non-retryable states:
        - `manual_cleanup_required` (failed_rollback_pending)
        - `not_in_retryable_state` (any other non-failed state)
        - `slug_in_use_by_new_org` (another active row claimed this slug)
    Returns 403 if caller is not a platform-admin.
    Returns 404 if no failed row with this slug exists.

    audit-tenant-isolation-2026-05-05 finding C-2: this endpoint operates on
    an arbitrary tenant `slug` (the failed-row may belong to ANY tenant, not
    just the caller's). The `require_platform_admin()` dependency enforces
    the cross-tenant gate (admin role + platform-admin org).
    """

    # Find the failed row for this slug. Because the partial unique index only
    # enforces uniqueness over active rows, there MAY be multiple rows sharing
    # the slug at this point (one soft-deleted failed, one newly-provisioned
    # successor). Target the most-recently-failed soft-deleted row.
    failed_row_result = await db.execute(
        select(PortalOrg)
        .where(
            and_(
                PortalOrg.slug == slug,
                PortalOrg.provisioning_status == "failed_rollback_complete",
                PortalOrg.deleted_at.is_not(None),
            )
        )
        .order_by(PortalOrg.deleted_at.desc())
        .limit(1)
        .with_for_update()
    )
    failed_org = failed_row_result.scalar_one_or_none()

    if failed_org is None:
        # No failed row with this slug → maybe the slug exists in a different
        # state, but that's not retryable via this endpoint.
        existing_result = await db.execute(select(PortalOrg).where(PortalOrg.slug == slug).limit(1))
        existing = existing_result.scalar_one_or_none()
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Organisation not found",
            )
        if existing.provisioning_status == "failed_rollback_pending":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "manual_cleanup_required",
                    "state": "failed_rollback_pending",
                },
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "not_in_retryable_state",
                "state": existing.provisioning_status,
            },
        )

    # Re-read inside the lock to protect against the narrow window where the
    # row's state changed between our query and the lock acquisition. Because
    # `with_for_update()` returned it, we already have the lock — this is the
    # authoritative state.
    if failed_org.provisioning_status != "failed_rollback_complete":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "not_in_retryable_state",
                "state": failed_org.provisioning_status,
            },
        )

    # Guard against slug reuse: if a newer signup created another org with the
    # same slug that is active, we cannot clear `deleted_at` on the failed row
    # without violating the partial unique index.
    collision_result = await db.execute(
        select(PortalOrg.id)
        .where(
            and_(
                PortalOrg.slug == slug,
                PortalOrg.id != failed_org.id,
                PortalOrg.deleted_at.is_(None),
            )
        )
        .limit(1)
    )
    if collision_result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "slug_in_use_by_new_org",
                "state": "failed_rollback_complete",
            },
        )

    # Revive the row: clear deleted_at and reset the state machine to queued.
    failed_org.deleted_at = None
    failed_org.provisioning_status = "queued"
    await db.commit()

    # SPEC-SEC-HYGIENE-001 REQ-20.2: invalidate tenant-slug cache so the
    # callback-URL allowlist re-accepts the restored slug immediately.
    from app.api.auth import invalidate_tenant_slug_cache

    invalidate_tenant_slug_cache()

    logger.info(
        "provisioning_retry_queued",
        org_id=failed_org.id,
        slug=slug,
        admin_user=perms.user_id,
        platform_admin_org_id=perms.org_id,
    )

    # Audit-trail (audit-tenant-isolation-2026-05-05 C-2): platform-admin
    # actions on another tenant's row MUST be logged. Fire-and-forget via
    # log_event — own session so the trail survives even if the BackgroundTask
    # path raises later.
    await log_event(
        org_id=failed_org.id,
        actor=perms.user_id,
        action="retry_provisioning",
        resource_type="portal_org",
        resource_id=str(failed_org.id),
        details={
            "slug": slug,
            "platform_admin_org_id": perms.org_id,
            "platform_admin_org_slug": perms.org_slug,
        },
    )

    # Schedule the actual provisioning outside the request cycle.
    background_tasks.add_task(provision_tenant, failed_org.id)

    return {"status": "queued"}


# Tell SQLAlchemy `func.now()` is used (silences pyright unused-import in some configs).
_ = func.now
