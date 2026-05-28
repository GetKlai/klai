"""Admin org settings and plan management endpoints.

SPEC-PORTAL-EXTENSIONS-UNIFY-001 (2026-05-12): the legacy self-service
addons endpoints (GET/PATCH /api/admin/settings/addons) are deprecated.
GET remains as a read-only facade returning the subset of
platform_unlocked_features that are user-facing products (scribe/docs).
PATCH returns 410 Gone — tenants can no longer toggle add-ons themselves.
The full extension-management lives behind /api/admin/extensions and is
gated by require_platform_admin for cross-org writes.
"""

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import _load_org_or_500
from app.core.database import get_db
from app.core.features import FEATURE_MIN_PROFILE, PLAN_FEATURES
from app.core.permissions import ProfileRole, UserPermissions, get_caller_at_least
from app.services.domain_validation import primary_domain_for_email_domain

# Set of user-facing product keys (= keys that appear in derive_user_products
# output). Used by the deprecated /settings/addons GET facade to return only
# the "addon-like" subset of platform_unlocked_features, preserving its
# original response shape for any lingering frontend reader.
_ADDON_PRODUCT_KEYS: frozenset[str] = frozenset(
    k for k, _floor in FEATURE_MIN_PROFILE.items() if k not in {"chat", "knowledge"}
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class MessageResponse(BaseModel):
    message: str


class OrgSettingsOut(BaseModel):
    name: str
    default_language: Literal["nl", "en"]
    mfa_policy: Literal["optional", "recommended", "required"] = "optional"
    # @MX:NOTE SPEC-AUTH-009 R5 -- toggle for auto-accepting same-domain users.
    # Default False; when True, domain_match picker entries skip join-request flow.
    auto_accept_same_domain: bool = False
    # @MX:NOTE SPEC-AUTH-009 R5 -- exposed so the frontend toggle label can show
    # "Automatically accept users with @{primary_domain}". None when not set.
    primary_domain: str | None = None
    # @MX:NOTE SPEC-PRIVACY-QUERY-SHADOW-001 REQ-15 — current telemetry mode,
    # surfaced read-only on this GET so the admin settings UI can render the
    # current state without a second roundtrip. Mutated via the dedicated
    # tenant-self-service endpoint POST /api/orgs/me/telemetry-level.
    telemetry_level: Literal["off", "shadow", "full"] = "shadow"


class OrgSettingsUpdate(BaseModel):
    default_language: Literal["nl", "en"] | None = None
    mfa_policy: Literal["optional", "recommended", "required"] | None = None
    # C5.1: optional field -- omitting it does NOT change the existing value
    auto_accept_same_domain: bool | None = None


class PlanChangeRequest(BaseModel):
    plan: str


def _settings_out(org) -> OrgSettingsOut:
    primary_domain = primary_domain_for_email_domain(org.primary_domain or "")
    return OrgSettingsOut(
        name=org.name,
        default_language=org.default_language,
        mfa_policy=org.mfa_policy,
        auto_accept_same_domain=bool(org.auto_accept_same_domain) if primary_domain else False,
        primary_domain=primary_domain or None,
        telemetry_level=org.telemetry_level,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/settings", response_model=OrgSettingsOut)
async def get_org_settings(
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> OrgSettingsOut:
    org = await _load_org_or_500(db, perms.org_id)
    return _settings_out(org)


@router.patch("/settings", response_model=OrgSettingsOut)
async def update_org_settings(
    body: OrgSettingsUpdate,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> OrgSettingsOut:
    org = await _load_org_or_500(db, perms.org_id)
    if body.default_language is not None:
        org.default_language = body.default_language
    if body.mfa_policy is not None:
        org.mfa_policy = body.mfa_policy
    # C5.1: only update when explicitly provided
    if body.auto_accept_same_domain is not None:
        org.auto_accept_same_domain = bool(body.auto_accept_same_domain) and bool(
            primary_domain_for_email_domain(org.primary_domain or "")
        )
    await db.commit()
    logger.info("Org settings updated: org_id=%d", perms.org_id)
    return _settings_out(org)


@router.patch("/plan", response_model=MessageResponse)
async def change_plan(
    body: PlanChangeRequest,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Upgrade or downgrade org plan.

    SPEC-PORTAL-RBAC-001 v0.2.0: products are derived from (profile, plan,
    platform_unlocked_features), so changing the plan automatically (re-)gates
    the feature set on the next request. No per-user/group product cleanup
    needed -- those tables are no longer the source of truth.
    """
    new_plan = body.plan

    if new_plan not in PLAN_FEATURES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown plan: {new_plan}")

    org = await _load_org_or_500(db, perms.org_id)
    org.plan = new_plan
    await db.commit()
    return MessageResponse(message=f"Plan bijgewerkt naar {new_plan}.")


# ---------------------------------------------------------------------------
# Deprecated add-on toggle endpoints (SPEC-PORTAL-EXTENSIONS-UNIFY-001)
#
# The original self-service toggle behaviour is gone: tenant-admins can no
# longer enable or disable extensions. GET remains as a read-only facade so
# any lingering frontend reader sees a stable response shape during the
# transition window. PATCH returns 410 Gone — the new path is
# `/api/admin/extensions` (Phase 3, gated by require_platform_admin for
# writes).
# ---------------------------------------------------------------------------


class AddonsOut(BaseModel):
    enabled_addons: list[str]


@router.get("/settings/addons", response_model=AddonsOut, deprecated=True)
async def get_addons(
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> AddonsOut:
    """Read-only facade — returns the subset of platform_unlocked_features
    that are user-facing products (scribe/docs).

    Wire-name `enabled_addons` preserved for backward compatibility with the
    legacy frontend. Will be removed once /admin/settings frontend is migrated
    to /api/admin/extensions in Phase 3.
    """
    unlocked = set(perms.platform_unlocked_features)
    addons = sorted(unlocked & _ADDON_PRODUCT_KEYS)
    return AddonsOut(enabled_addons=addons)


@router.patch("/settings/addons", status_code=status.HTTP_410_GONE, deprecated=True)
async def update_addons_gone() -> dict:
    """Deprecated by SPEC-PORTAL-EXTENSIONS-UNIFY-001. All extension toggles
    are platform-admin controlled — see PATCH /api/admin/extensions."""
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail=(
            "Tenant-level add-on toggles are no longer self-service. "
            "Extensions are managed by Klai platform admins via "
            "/api/admin/extensions."
        ),
    )
