"""Admin org settings and plan management endpoints."""

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.features import ADDON_FEATURES, PLAN_FEATURES
from app.core.permissions import ProfileRole, UserPermissions, get_caller_at_least
from app.models.portal import PortalOrg
from app.services.audit import log_event
from app.services.events import emit_event

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


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


async def _load_org_or_500(db: AsyncSession, org_id: int) -> PortalOrg:
    """Fetch the caller's PortalOrg ORM row.

    UserPermissions only carries `org_id`/`org_slug`/`plan`/`enabled_addons`/
    `platform_unlocked_features`. Endpoints that need other PortalOrg fields
    (name, default_language, mfa_policy, primary_domain, telemetry_level,
    auto_accept_same_domain) re-fetch the row through this helper. The
    tenant GUC is already set by `get_caller`, so RLS on portal_orgs is fine.
    """
    org = await db.get(PortalOrg, org_id)
    if org is None:
        # get_caller resolved the org just before this; a missing row means
        # something deleted it mid-request. Treat as 500 — the resolver and
        # this endpoint disagree on reality.
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Organisation row vanished")
    return org


@router.get("/settings", response_model=OrgSettingsOut)
async def get_org_settings(
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> OrgSettingsOut:
    org = await _load_org_or_500(db, perms.org_id)
    return OrgSettingsOut(
        name=org.name,
        default_language=org.default_language,
        mfa_policy=org.mfa_policy,
        auto_accept_same_domain=bool(org.auto_accept_same_domain),
        primary_domain=org.primary_domain or None,
        telemetry_level=org.telemetry_level,
    )


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
        org.auto_accept_same_domain = body.auto_accept_same_domain
    await db.commit()
    logger.info("Org settings updated: org_id=%d", perms.org_id)
    return OrgSettingsOut(
        name=org.name,
        default_language=org.default_language,
        mfa_policy=org.mfa_policy,
        auto_accept_same_domain=bool(org.auto_accept_same_domain),
        primary_domain=org.primary_domain or None,
        telemetry_level=org.telemetry_level,
    )


@router.patch("/plan", response_model=MessageResponse)
async def change_plan(
    body: PlanChangeRequest,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Upgrade or downgrade org plan.

    SPEC-PORTAL-RBAC-001 v0.2.0: products are derived from (profile, plan,
    enabled_addons), so changing the plan automatically (re-)gates the
    feature set on the next request. No per-user/group product cleanup
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
# Add-on toggle endpoints (SPEC-PORTAL-PROFILES-001 Phase 2 P2.4)
# ---------------------------------------------------------------------------


class AddonsOut(BaseModel):
    enabled_addons: list[str]


class AddonsUpdate(BaseModel):
    enabled_addons: list[str]


@router.get("/settings/addons", response_model=AddonsOut)
async def get_addons(
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> AddonsOut:
    """Return the tenant's currently enabled add-ons."""
    return AddonsOut(enabled_addons=list(perms.enabled_addons))


@router.patch("/settings/addons", response_model=AddonsOut)
async def update_addons(
    body: AddonsUpdate,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> AddonsOut:
    """Enable or disable tenant-level add-ons.

    # @MX:NOTE: SPEC-PORTAL-PROFILES-001 Phase 2 P2.4 — only values in
    # ADDON_FEATURES are accepted. Unknown values raise 400. Per
    # SPEC-PORTAL-RBAC-001 v0.2.0, products are derived purely from
    # (role, plan, enabled_addons); there are no per-user / per-group
    # entitlement tables to keep in sync. Disabling an add-on simply
    # stops surfacing it to the derivation function on the next request.
    """
    unknown = [p for p in body.enabled_addons if p not in ADDON_FEATURES]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown add-on product(s): {unknown}. Valid: {sorted(ADDON_FEATURES)}",
        )

    org = await _load_org_or_500(db, perms.org_id)
    before = list(org.enabled_addons or [])
    after = list(body.enabled_addons)

    org.enabled_addons = after

    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))

    await log_event(
        org_id=perms.org_id,
        actor=perms.user_id,
        action="addons.updated",
        resource_type="org",
        resource_id=str(perms.org_id),
        details={"before": before, "after": after, "added": added, "removed": removed},
    )
    await db.commit()

    emit_event(
        "tenant.addons_updated",
        org_id=perms.org_id,
        user_id=perms.user_id,
        properties={"enabled_addons": after, "added": added, "removed": removed},
    )

    return AddonsOut(enabled_addons=after)
