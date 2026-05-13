"""Platform-admin endpoints for managing per-org platform-locked features.

SPEC-PORTAL-RBAC-REFACTOR-001 Phase 5D.

Endpoints:
    GET  /api/admin/orgs/{slug}/platform-unlocks  — read current unlocked features
    PATCH /api/admin/orgs/{slug}/platform-unlocks — update unlocked features

Both require ``require_platform_admin()`` (caller must be admin in the platform org).
Changes are audited via ``tenant_lifecycle_events`` with ``actor_type='platform_admin'``.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.extensions_registry import KNOWN_FEATURES
from app.core.permissions import UserPermissions, require_platform_admin
from app.models.portal import PortalOrg
from app.services.audit.tenant_lifecycle import emit_lifecycle_event

logger = structlog.get_logger()

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class PlatformUnlocksResponse(BaseModel):
    slug: str
    platform_unlocked_features: list[str]


class PatchPlatformUnlocksRequest(BaseModel):
    platform_unlocked_features: list[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_org_by_slug(slug: str, db: AsyncSession) -> PortalOrg:
    """Load a PortalOrg by slug; raise 404 if not found."""
    result = await db.execute(select(PortalOrg).where(PortalOrg.slug == slug, PortalOrg.deleted_at.is_(None)))
    org = result.scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organisation not found")
    return org


# ---------------------------------------------------------------------------
# GET /api/admin/orgs/{slug}/platform-unlocks
# ---------------------------------------------------------------------------


@router.get("/orgs/{slug}/platform-unlocks", response_model=PlatformUnlocksResponse)
async def get_platform_unlocks(
    slug: str,
    perms: UserPermissions = Depends(require_platform_admin()),
    db: AsyncSession = Depends(get_db),
) -> PlatformUnlocksResponse:
    """Return the current platform-unlocked features for the given org slug.

    Only platform admins (Klai staff) may call this endpoint.
    """
    org = await _get_org_by_slug(slug, db)
    features = list(org.platform_unlocked_features or [])
    logger.info(
        "platform_unlocks_read",
        target_slug=slug,
        features=features,
        actor_user_id=perms.user_id,
    )
    return PlatformUnlocksResponse(slug=slug, platform_unlocked_features=features)


# ---------------------------------------------------------------------------
# PATCH /api/admin/orgs/{slug}/platform-unlocks
# ---------------------------------------------------------------------------


@router.patch("/orgs/{slug}/platform-unlocks", response_model=PlatformUnlocksResponse)
async def patch_platform_unlocks(
    slug: str,
    body: PatchPlatformUnlocksRequest,
    perms: UserPermissions = Depends(require_platform_admin()),
    db: AsyncSession = Depends(get_db),
) -> PlatformUnlocksResponse:
    """Replace the platform-unlocked features list for the given org slug.

    Replaces the full array — callers must send the complete desired set.
    Only platform admins (Klai staff) may call this endpoint.
    Changes are audited in ``tenant_lifecycle_events``.

    SPEC-PORTAL-EXTENSIONS-UNIFY-001 cleanup: validates every requested
    feature key against ``KNOWN_FEATURES`` (400 on unknown), and
    de-dupes + sorts the persisted set so storage is deterministic.
    """
    unknown = [k for k in body.platform_unlocked_features if k not in KNOWN_FEATURES]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown feature(s): {sorted(unknown)}. Valid: {sorted(KNOWN_FEATURES)}",
        )

    org = await _get_org_by_slug(slug, db)

    previous = list(org.platform_unlocked_features or [])
    new_features = sorted(set(body.platform_unlocked_features))

    org.platform_unlocked_features = new_features  # type: ignore[assignment]

    # Emit audit within the same transaction as the update so both succeed or
    # both fail together. tenant_lifecycle_events has no FK to portal_orgs so
    # the INSERT succeeds even if the org is later hard-deleted.
    await emit_lifecycle_event(
        db,
        event_type="platform_features_updated",
        org_id_snapshot=org.id,
        org_slug_snapshot=org.slug,
        org_name_snapshot=org.name,
        actor_user_id=perms.user_id,
        actor_type="platform_admin",
        properties={
            "previous_features": previous,
            "new_features": new_features,
        },
    )

    await db.commit()

    logger.info(
        "platform_unlocks_updated",
        target_slug=slug,
        previous=previous,
        new=new_features,
        actor_user_id=perms.user_id,
    )
    return PlatformUnlocksResponse(slug=slug, platform_unlocked_features=new_features)
