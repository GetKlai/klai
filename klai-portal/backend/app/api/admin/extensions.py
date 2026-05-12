"""SPEC-PORTAL-EXTENSIONS-UNIFY-001 Phase 3 — tenant extensions API.

Two endpoints power the new ``/admin/settings`` Uitbreidingen-sectie:

- ``GET /api/admin/extensions`` — returns the full known-features list with
  per-feature on/off status for the caller's own org (admin role required).
  When called by a platform-admin (Klai staff), an optional ``?org_slug=…``
  parameter switches the view to another tenant.
- ``PATCH /api/admin/extensions`` — replaces a tenant's
  ``platform_unlocked_features`` set. Platform-admin-only.

The PATCH delegates to the same persistence + audit path as
``platform_unlocks.py::patch_platform_unlocks`` so there is one shared
write path with a uniform ``tenant_lifecycle_events`` trail.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.platform_unlocks import _KNOWN_FEATURES
from app.core.database import get_db
from app.core.features import FEATURE_MIN_PROFILE
from app.core.permissions import ProfileRole, UserPermissions, get_caller_at_least
from app.models.portal import PortalOrg
from app.services.audit.tenant_lifecycle import emit_lifecycle_event

logger = structlog.get_logger()

router = APIRouter()


# ---------------------------------------------------------------------------
# Display registry — labels + descriptions per known feature.
# ---------------------------------------------------------------------------

_EXTENSION_LABELS: dict[str, str] = {
    "partner_api": "API keys",
    "widgets": "Chat-widgets",
    "custom_mcps": "Custom MCP servers",
    "scribe": "Scribe — meeting-transcripties",
    "docs": "Docs — gedeelde KBs",
}

_EXTENSION_DESCRIPTIONS: dict[str, str] = {
    "partner_api": "Programmatische toegang via pk_live_* API-keys.",
    "widgets": "Embed chat-widget op klant-website.",
    "custom_mcps": "Eigen Model Context Protocol servers koppelen.",
    "scribe": "Automatische meeting-transcriptie.",
    "docs": "Documentatie-KBs delen binnen de organisatie.",
}


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


class UpdateExtensionsRequest(BaseModel):
    org_slug: str
    enabled_features: list[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _resolve_target_org(
    perms: UserPermissions,
    org_slug: str | None,
    db: AsyncSession,
) -> PortalOrg:
    """Resolve target org. Without slug, returns caller's own org. With slug,
    requires platform-admin and looks up by slug."""
    if org_slug is None:
        result = await db.execute(select(PortalOrg).where(PortalOrg.id == perms.org_id, PortalOrg.deleted_at.is_(None)))
        org = result.scalar_one_or_none()
        if org is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organisation not found")
        return org

    if not perms.is_platform_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cross-org queries require platform admin",
        )

    result = await db.execute(select(PortalOrg).where(PortalOrg.slug == org_slug, PortalOrg.deleted_at.is_(None)))
    org = result.scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organisation not found")
    return org


def _build_extensions_payload(org: PortalOrg, perms: UserPermissions) -> ExtensionsResponse:
    """Build the response payload for a given target org + caller perms."""
    unlocked = set(org.platform_unlocked_features or [])
    items: list[ExtensionItem] = []
    for key in sorted(_KNOWN_FEATURES):
        items.append(
            ExtensionItem(
                key=key,
                label=_EXTENSION_LABELS.get(key, key),
                description=_EXTENSION_DESCRIPTIONS.get(key, ""),
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
    org_slug: str | None = None,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> ExtensionsResponse:
    """Return all known extensions with on/off status for the target org.

    Without ``org_slug``: returns the caller's own org. With ``org_slug``:
    requires ``is_platform_admin`` and returns the named tenant. Tenant
    admins who attempt a cross-org query get 403.
    """
    target = await _resolve_target_org(perms, org_slug, db)
    return _build_extensions_payload(target, perms)


# ---------------------------------------------------------------------------
# PATCH /api/admin/extensions
# ---------------------------------------------------------------------------


@router.patch("/extensions", response_model=ExtensionsResponse)
async def update_extensions(
    body: UpdateExtensionsRequest,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> ExtensionsResponse:
    """Replace the platform_unlocked_features set for the target org.

    Platform-admin only — tenant admins always get 403 here, even on their
    own org. This is intentional per the SPEC: extension toggling is a
    Klai-staff decision, not a tenant-admin self-service action.

    Body payload mirrors the existing
    ``/api/admin/orgs/{slug}/platform-unlocks`` PATCH — same write path,
    same ``tenant_lifecycle_events`` audit trail, different URL shape that
    fits the frontend's tenant-picker UX.
    """
    if not perms.is_platform_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Extension management requires platform admin",
        )

    # Validate every requested feature against the known-features registry.
    unknown = [k for k in body.enabled_features if k not in _KNOWN_FEATURES]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown feature(s): {sorted(unknown)}. Valid: {sorted(_KNOWN_FEATURES)}",
        )

    target = await _resolve_target_org(perms, body.org_slug, db)
    previous = list(target.platform_unlocked_features or [])
    new_features = sorted(set(body.enabled_features))

    target.platform_unlocked_features = new_features  # type: ignore[assignment]

    # Audit-trail in the same transaction so the write + audit either both
    # succeed or both roll back. tenant_lifecycle_events has no FK to
    # portal_orgs so the audit row survives a hard-delete by design.
    await emit_lifecycle_event(
        db,
        event_type="platform_features_updated",
        org_id_snapshot=target.id,
        org_slug_snapshot=target.slug,
        org_name_snapshot=target.name,
        actor_user_id=perms.user_id,
        actor_type="platform_admin",
        properties={
            "previous_features": previous,
            "new_features": new_features,
            "via": "extensions_api",
        },
    )

    await db.commit()

    logger.info(
        "extensions_updated",
        target_slug=target.slug,
        previous=previous,
        new=new_features,
        actor_user_id=perms.user_id,
    )
    return _build_extensions_payload(target, perms)
