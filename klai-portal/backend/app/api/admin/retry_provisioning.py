"""SPEC-PROV-001 M4 — admin-only retry endpoint for failed tenant provisioning.

POST /api/admin/orgs/{slug}/retry-provisioning

Callable only for orgs in `failed_rollback_complete` state. Other states are
rejected with 409 so admins do not accidentally re-start provisioning over a
run that is still mid-flight or needs manual cleanup.

Concurrency guarantee: `SELECT ... FOR UPDATE` on the target row serialises
concurrent retry clicks. The second caller reads the row after the first
committed the transition to `queued` and falls through to the
`not_in_retryable_state` branch.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import _get_caller_org, _require_admin, bearer
from app.core.database import get_db
from app.models.portal import PortalOrg
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
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Retry provisioning for an org in `failed_rollback_complete` state.

    Returns 202 + `{"status": "queued"}` on success.
    Returns 409 with `error` code for non-retryable states:
        - `manual_cleanup_required` (failed_rollback_pending)
        - `not_in_retryable_state` (any other non-failed state)
        - `slug_in_use_by_new_org` (another active row claimed this slug)
    Returns 403 if caller is not an admin.
    Returns 404 if no failed row with this slug exists.
    """
    _, _caller_org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

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

    logger.info(
        "provisioning_retry_queued",
        org_id=failed_org.id,
        slug=slug,
        admin_user=caller_user.zitadel_user_id,
    )

    # Schedule the actual provisioning outside the request cycle.
    background_tasks.add_task(provision_tenant, failed_org.id)

    return {"status": "queued"}


# Tell SQLAlchemy `func.now()` is used (silences pyright unused-import in some configs).
_ = func.now
