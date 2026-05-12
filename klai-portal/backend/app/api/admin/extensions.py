"""SPEC-PORTAL-EXTENSIONS-UNIFY-001 — tenant extensions read API.

``GET /api/admin/extensions`` returns the full known-features list with
per-feature on/off status for the caller's own org (admin role required).
Powers the /admin/settings Uitbreidingen-sectie.

Writes live on ``PATCH /api/admin/orgs/{slug}/platform-unlocks`` —
platform-admin only, sole write-path so there is exactly one audit trail
in ``tenant_lifecycle_events``. This module deliberately does NOT export
a PATCH endpoint of its own; the brief Phase 4 duplicate has been retired
so platform_unlocks.py is the single source of mutation.

The response payload is intentionally language-agnostic: only the feature
``key`` is returned. The frontend maps each key to its Paraglide messages
``admin_extension_{key}_label`` / ``..._description``, keeping NL/EN
switching client-side without a server round-trip.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import _load_org_or_500
from app.core.database import get_db
from app.core.extensions_registry import KNOWN_FEATURES
from app.core.features import FEATURE_MIN_PROFILE
from app.core.permissions import ProfileRole, UserPermissions, get_caller_at_least
from app.models.portal import PortalOrg

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class ExtensionItem(BaseModel):
    key: str
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
    items = [
        ExtensionItem(
            key=key,
            enabled=key in unlocked,
            requires_profile=FEATURE_MIN_PROFILE.get(key),
            manageable_by_caller=perms.is_platform_admin,
        )
        for key in sorted(KNOWN_FEATURES)
    ]
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

    Tenant-admin sees own-org status read-only. Platform-admin sees the same
    payload with ``manageable_by_caller=true`` so the frontend renders
    interactive checkboxes — writes still go through
    ``PATCH /api/admin/orgs/{slug}/platform-unlocks`` (single source of
    mutation).
    """
    org = await _load_org_or_500(db, perms.org_id)
    return _build_extensions_payload(org, perms)
