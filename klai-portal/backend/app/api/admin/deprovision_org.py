"""SPEC-INFRA-TENANT-DELETE-001 R1/R8/R10 — tenant deprovisioning endpoints.

Five endpoints:
  GET    /api/admin/org/me                      — owner read of current org metadata
  DELETE /api/admin/org/me                      — owner self-service
  DELETE /api/admin/orgs/{slug}/deprovision     — platform-admin
  POST   /api/admin/orgs/{slug}/retry-deprovisioning  — admin retry after failure
  GET    /api/admin/org/me/deprovision-status   — owner polling (allow_during_deprovisioning)

All mutating endpoints guard against concurrent calls via SELECT FOR UPDATE.
A second concurrent request reads the row after the first's commit and sees
provisioning_status == 'deprovisioning', falling through to the 409 branch.

# @MX:ANCHOR: SPEC-INFRA-TENANT-DELETE-001 R1/R8/R10. fan_in=2 (owner + platform_admin).
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import _get_caller_org, _require_admin, bearer
from app.core.config import settings
from app.core.database import get_db
from app.models.portal import PortalOrg
from app.services.provisioning.deprovisioning_orchestrator import deprovision_tenant

logger = structlog.get_logger()

router = APIRouter()

# States that block a new deprovisioning request (409 conflict).
_ALREADY_DEPROVISIONING_STATES = frozenset(
    {
        "deprovisioning",
        "deprovisioned",
        "failed_deprovisioning",
    }
)

# States from which the initial-deprovision endpoint may be entered.
#
# Intentionally a STRICT SUBSET of state_machine.DEPROVISION_ENTRY_STATES
# (which adds `failed_deprovisioning` for the admin retry endpoint). The
# initial DELETE endpoints (owner self-service + platform admin) MUST NOT
# accept `failed_deprovisioning` — that state requires the retry endpoint,
# not a fresh delete (otherwise an operator confused about state could
# overwrite last_failure and lose recovery context).
#
# If state_machine.DEPROVISION_ENTRY_STATES grows with new entry states,
# audit whether they belong here too. Today: ready (happy path) +
# failed_rollback_complete (cleanup of failed signup).
INITIAL_DEPROVISION_ENTRY_STATES = frozenset(
    {
        "ready",
        "failed_rollback_complete",
    }
)
# Backward-compat alias (existing callers import DEPROVISION_ENTRY_STATES
# from this module). Remove the alias once all callers are updated.
DEPROVISION_ENTRY_STATES = INITIAL_DEPROVISION_ENTRY_STATES


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _lock_org_for_deprovision(org_id: int, db: AsyncSession) -> PortalOrg:
    """SELECT FOR UPDATE on portal_orgs by pk.

    Returns the locked row. Caller must inspect provisioning_status and
    raise 409 if the org is already in a deprovisioning state, before
    mutating the row.

    # @MX:NOTE: SPEC-INFRA-TENANT-DELETE-001 R1 — serialises concurrent deprovision
    #   clicks. The second caller reads after the first's commit and sees
    #   'deprovisioning', falling to the 409 branch.
    """
    result = await db.execute(select(PortalOrg).where(PortalOrg.id == org_id).with_for_update())
    return result.scalar_one()


async def _find_org_by_slug(slug: str, db: AsyncSession) -> PortalOrg | None:
    """Look up an active (non-deleted) org by slug.

    Returns None when not found. Caller raises 404.
    """
    result = await db.execute(select(PortalOrg).where(PortalOrg.slug == slug, PortalOrg.deleted_at.is_(None)))
    return result.scalar_one_or_none()


def _guard_entry_state(org: PortalOrg) -> None:
    """Raise 409 if the org is already in / past a deprovisioning state.

    # @MX:NOTE: SPEC-INFRA-TENANT-DELETE-001 R1 — 409 guard. Fires for orgs that are
    #   already deprovisioning, deprovisioned, or failed_deprovisioning. The
    #   retry-deprovisioning endpoint has its own guard that allows re-entry
    #   from failed_deprovisioning specifically.
    """
    if org.provisioning_status in _ALREADY_DEPROVISIONING_STATES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "already_deprovisioning",
                "state": org.provisioning_status,
            },
        )
    if org.provisioning_status not in DEPROVISION_ENTRY_STATES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "not_in_deprovisionable_state",
                "state": org.provisioning_status,
            },
        )


def _require_platform_admin(caller_org: PortalOrg) -> None:
    """Raise 403 unless the caller's org is the platform-admin org.

    # @MX:NOTE: SPEC-INFRA-TENANT-DELETE-001 R1 — platform-admin guard. Uses
    #   settings.platform_org_slug (default 'getklai') to identify the platform org.
    """
    if caller_org.slug != settings.platform_org_slug:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: platform admin org required",
        )


# ---------------------------------------------------------------------------
# Owner read: GET /api/admin/org/me
# ---------------------------------------------------------------------------


@router.get(
    "/org/me",
    status_code=status.HTTP_200_OK,
)
async def get_own_org(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Owner-readable metadata for the caller's current organisation.

    Returns the minimum surface needed by the danger-zone delete-modal:
    `{slug, name}`. Caller must be the org owner (portal_role='admin').

    Discovered during the 2026-05-03 e2e walkthrough: the danger-zone page
    was issuing 4× `GET /api/admin/org/me` (one per render of the
    delete-modal precondition) and getting 405 because the only handler
    on this path was DELETE. Frontend was correct — backend was missing
    the GET counterpart. SPEC-INFRA-TENANT-DELETE-001 R10.

    # @MX:NOTE: SPEC-INFRA-TENANT-DELETE-001 R10. Read-only sibling of the
    # DELETE /org/me endpoint. Same auth pattern. No state-machine guard:
    # if the org is already in a deprovisioning state the modal still
    # needs slug+name to render the polling UI.
    """
    _, caller_org, caller_user = await _get_caller_org(
        credentials,
        db,
        allow_during_deprovisioning=True,
    )
    _require_admin(caller_user)

    return {
        "slug": caller_org.slug,
        "name": caller_org.name,
    }


# ---------------------------------------------------------------------------
# Owner self-service: DELETE /api/admin/org/me
# ---------------------------------------------------------------------------


@router.delete(
    "/org/me",
    status_code=status.HTTP_202_ACCEPTED,
)
async def deprovision_own_org(
    background_tasks: BackgroundTasks,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Owner-initiated tenant deletion.

    Requires portal_role='admin'. Returns 202 + {"status": "queued", "org_slug": <slug>}.
    Returns 409 if the org is already in a deprovisioning state.

    # @MX:NOTE: SPEC-INFRA-TENANT-DELETE-001 R1. deprovisioner_type='owner'.
    """
    zitadel_user_id, caller_org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    # Lock the row and verify state before transitioning.
    locked_org = await _lock_org_for_deprovision(caller_org.id, db)
    _guard_entry_state(locked_org)

    # Transition to 'deprovisioning' so auth-flow returns 403 for all subsequent requests.
    locked_org.provisioning_status = "deprovisioning"
    org_slug = locked_org.slug
    org_id = locked_org.id
    await db.commit()

    logger.info(
        "deprovision_queued",
        org_id=org_id,
        slug=org_slug,
        deprovisioner_type="owner",
        actor=zitadel_user_id,
    )

    background_tasks.add_task(
        deprovision_tenant,
        org_id,
        zitadel_user_id,
        "owner",
    )

    return {"status": "queued", "org_slug": org_slug}


# ---------------------------------------------------------------------------
# Platform-admin: DELETE /api/admin/orgs/{slug}/deprovision
# ---------------------------------------------------------------------------


@router.delete(
    "/orgs/{slug}/deprovision",
    status_code=status.HTTP_202_ACCEPTED,
)
async def deprovision_org_by_slug(
    slug: str,
    background_tasks: BackgroundTasks,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Platform-admin tenant deletion by slug.

    Requires caller org == settings.platform_org_slug AND portal_role='admin'.
    Returns 202 + {"status": "queued", "org_slug": <slug>}.
    Returns 404 if slug not found. Returns 409 if already deprovisioning.

    # @MX:NOTE: SPEC-INFRA-TENANT-DELETE-001 R1. deprovisioner_type='platform_admin'.
    """
    zitadel_user_id, caller_org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)
    _require_platform_admin(caller_org)

    target_org = await _find_org_by_slug(slug, db)
    if target_org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organisation not found",
        )

    # Lock the target row (not the caller's row) before state check.
    locked_org = await _lock_org_for_deprovision(target_org.id, db)
    _guard_entry_state(locked_org)

    locked_org.provisioning_status = "deprovisioning"
    org_slug = locked_org.slug
    org_id = locked_org.id
    await db.commit()

    logger.info(
        "deprovision_queued",
        org_id=org_id,
        slug=org_slug,
        deprovisioner_type="platform_admin",
        actor=zitadel_user_id,
    )

    background_tasks.add_task(
        deprovision_tenant,
        org_id,
        zitadel_user_id,
        "platform_admin",
    )

    return {"status": "queued", "org_slug": org_slug}


# ---------------------------------------------------------------------------
# Admin retry: POST /api/admin/orgs/{slug}/retry-deprovisioning
# ---------------------------------------------------------------------------


@router.post(
    "/orgs/{slug}/retry-deprovisioning",
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_deprovisioning(
    slug: str,
    background_tasks: BackgroundTasks,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Retry deprovisioning from failed_deprovisioning state.

    Requires caller org == settings.platform_org_slug AND portal_role='admin'.
    Returns 202 + {"status": "queued"}.
    Returns 404 if slug not found. Returns 409 if org is not in failed_deprovisioning.

    # @MX:NOTE: SPEC-INFRA-TENANT-DELETE-001 R8. Resets last_failure to NULL before
    #   re-queueing so admin knows the retry is fresh. Step idempotency ensures
    #   already-deleted resources are skipped harmlessly.
    """
    zitadel_user_id, caller_org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)
    _require_platform_admin(caller_org)

    target_org = await _find_org_by_slug(slug, db)
    if target_org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organisation not found",
        )

    locked_org = await _lock_org_for_deprovision(target_org.id, db)

    if locked_org.provisioning_status != "failed_deprovisioning":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "not_in_retryable_deprovision_state",
                "state": locked_org.provisioning_status,
            },
        )

    locked_org.provisioning_status = "deprovisioning"
    org_id = locked_org.id
    # Reset last_failure so admin knows the retry is fresh.
    await db.execute(
        text("UPDATE portal_orgs SET last_failure = NULL WHERE id = :id"),
        {"id": org_id},
    )
    await db.commit()

    logger.info(
        "deprovision_retry_queued",
        org_id=org_id,
        slug=slug,
        actor=zitadel_user_id,
    )

    background_tasks.add_task(
        deprovision_tenant,
        org_id,
        zitadel_user_id,
        "platform_admin",
    )

    return {"status": "queued"}


# ---------------------------------------------------------------------------
# Owner status polling: GET /api/admin/org/me/deprovision-status
# ---------------------------------------------------------------------------


@router.get(
    "/org/me/deprovision-status",
    status_code=status.HTTP_200_OK,
)
async def get_deprovision_status(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Owner polling endpoint for deprovisioning progress.

    Uses allow_during_deprovisioning=True so the 403 guard in _get_caller_org
    does not fire while the org is being deleted. Returns:
    - 200 + {"status": "deprovisioning"} while in progress.
    - 200 + {"status": "failed_deprovisioning", "last_failure": {...}} on failure.
    - 200 + {"status": <other>} for orgs not currently being deprovisioned.
    - 404 if the org row is gone (successful deprovisioning).

    # @MX:NOTE: SPEC-INFRA-TENANT-DELETE-001 R10. allow_during_deprovisioning=True
    #   is the ONE exception to the standard 403 deprovisioning guard. Do not add
    #   other endpoints with this flag without SPEC justification.
    """
    try:
        _, org, caller_user = await _get_caller_org(credentials, db, allow_during_deprovisioning=True)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            # Org row is gone — successful deprovisioning completed.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Organisation has been deleted",
            ) from exc
        raise

    # SEC: only org-owner (admin role) may poll deprovisioning status. Without
    # this guard, members/group-admins could see step names + (previously) full
    # error strings during a failed deprovision — info disclosure of internal
    # infrastructure (container hostnames, step orchestration internals).
    _require_admin(caller_user)

    payload: dict = {"status": org.provisioning_status}
    if org.provisioning_status == "failed_deprovisioning" and org.last_failure:
        # SEC: do NOT expose `last_failure.error` to the owner — error strings
        # may include internal hostnames (klai-core-*-1), DSN fragments from
        # asyncpg/httpx exceptions, or other infra detail. Step name + timestamp
        # is enough for the owner to contact support; full error stays in
        # portal_orgs.last_failure (visible only via direct DB access by
        # platform admins) and in VictoriaLogs (queryable via Grafana MCP).
        payload["last_failure"] = {
            "step": org.last_failure.get("step", "unknown"),
            "failed_at": org.last_failure.get("failed_at"),
        }

    return payload
