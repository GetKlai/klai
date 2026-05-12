"""SPEC-PORTAL-EXTENSIONS-UNIFY-001 — tenant extensions read API.

``GET /api/admin/extensions`` returns the full known-features list with
per-feature on/off status for the caller's own org (admin role required).
Powers the /admin/settings Uitbreidingen-sectie.

Writes live on ``PATCH /api/admin/orgs/{slug}/platform-unlocks`` —
platform-admin only, sole write-path so there is exactly one audit trail
in ``tenant_lifecycle_events``. This module deliberately does NOT export
a PATCH endpoint of its own anymore; the brief Phase 4 duplicate has
been retired during cleanup so platform_unlocks.py is the single source
of mutation.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.extensions_registry import (
    EXTENSION_DESCRIPTIONS,
    EXTENSION_LABELS,
    KNOWN_FEATURES,
)
from app.core.features import FEATURE_MIN_PROFILE
from app.core.permissions import ProfileRole, UserPermissions, get_caller_at_least
from app.models.portal import PortalOrg

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class ExtensionItem(BaseModel):
    key: str
    label: str
    description: str
    enabled: bool
    requires_profile: str | None
    manageable_by_caller: bool


class ExtensionsResponse(BaseModel):
    org_slug: str
    extensions: list[ExtensionItem]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_extensions_payload(org: PortalOrg, perms: UserPermissions) -> ExtensionsResponse:
    """Build the response payload for a given target org + caller perms."""
    unlocked = set(org.platform_unlocked_features or [])
    items: list[ExtensionItem] = []
    for key in sorted(KNOWN_FEATURES):
        items.append(
            ExtensionItem(
                key=key,
                label=EXTENSION_LABELS.get(key, key),
                description=EXTENSION_DESCRIPTIONS.get(key, ""),
                enabled=key in unlocked,
                requires_profile=FEATURE_MIN_PROFILE.get(key),
                manageable_by_caller=perms.is_platform_admin,
            )
        )
    return ExtensionsResponse(org_slug=org.slug, extensions=items)


# ---------------------------------------------------------------------------
# GET /api/admin/extensions
# ---------------------------------------------------------------------------


@router.get("/extensions", response_model=ExtensionsResponse)
async def list_extensions(
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> ExtensionsResponse:
    """Return all known extensions with on/off status for the caller's own org.

    Tenant-admin sees own-org status read-only. Platform-admin sees same
    payload with ``manageable_by_caller=true`` flag so the frontend renders
    interactive checkboxes — writes still go through PATCH
    /api/admin/orgs/{slug}/platform-unlocks (single source of mutation).
    """
    result = await db.execute(select(PortalOrg).where(PortalOrg.id == perms.org_id, PortalOrg.deleted_at.is_(None)))
    org = result.scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organisation not found")
    return _build_extensions_payload(org, perms)
