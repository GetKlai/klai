"""Admin org settings and plan management endpoints."""

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.plans import ADDON_PRODUCTS, PLAN_PRODUCTS, get_plan_products
from app.models.groups import PortalGroupProduct
from app.models.products import PortalUserProduct
from app.services.audit import log_event
from app.services.events import emit_event

from . import _get_caller_org, _require_admin, bearer

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


@router.get("/settings", response_model=OrgSettingsOut)
async def get_org_settings(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> OrgSettingsOut:
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)
    return OrgSettingsOut(
        name=org.name,
        default_language=org.default_language,
        mfa_policy=org.mfa_policy,
        auto_accept_same_domain=bool(org.auto_accept_same_domain),
        primary_domain=org.primary_domain or None,
    )


@router.patch("/settings", response_model=OrgSettingsOut)
async def update_org_settings(
    body: OrgSettingsUpdate,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> OrgSettingsOut:
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)
    if body.default_language is not None:
        org.default_language = body.default_language
    if body.mfa_policy is not None:
        org.mfa_policy = body.mfa_policy
    # C5.1: only update when explicitly provided
    if body.auto_accept_same_domain is not None:
        org.auto_accept_same_domain = body.auto_accept_same_domain
    await db.commit()
    logger.info("Org settings updated: org_id=%d", org.id)
    return OrgSettingsOut(
        name=org.name,
        default_language=org.default_language,
        mfa_policy=org.mfa_policy,
        auto_accept_same_domain=bool(org.auto_accept_same_domain),
        primary_domain=org.primary_domain or None,
    )


@router.patch("/plan", response_model=MessageResponse)
async def change_plan(
    body: PlanChangeRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Upgrade or downgrade org plan. On downgrade, revokes over-ceiling products."""
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    old_plan = org.plan
    new_plan = body.plan

    if new_plan not in PLAN_PRODUCTS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown plan: {new_plan}")

    org.plan = new_plan

    # Downgrade: revoke products that exceed the new plan ceiling
    new_products = set(get_plan_products(new_plan))

    revoked_result = await db.execute(select(PortalUserProduct).where(PortalUserProduct.org_id == org.id))
    all_assignments = revoked_result.scalars().all()
    for row in all_assignments:
        if row.product not in new_products:
            logger.info(
                "Plan downgrade: revoking product %s from user %s (org %s, %s -> %s)",
                row.product,
                row.zitadel_user_id,
                org.id,
                old_plan,
                new_plan,
            )
            await db.delete(row)

    # Downgrade: also revoke group products that exceed the new plan ceiling
    group_revoked_result = await db.execute(select(PortalGroupProduct).where(PortalGroupProduct.org_id == org.id))
    all_group_assignments = group_revoked_result.scalars().all()
    for row in all_group_assignments:
        if row.product not in new_products:
            logger.info(
                "Plan downgrade: revoking group product %s from group %s (org %s, %s -> %s)",
                row.product,
                row.group_id,
                org.id,
                old_plan,
                new_plan,
            )
            await db.delete(row)

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
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> AddonsOut:
    """Return the tenant's currently enabled add-ons."""
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)
    return AddonsOut(enabled_addons=list(org.enabled_addons or []))


@router.patch("/settings/addons", response_model=AddonsOut)
async def update_addons(
    body: AddonsUpdate,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> AddonsOut:
    """Enable or disable tenant-level add-ons.

    # @MX:NOTE: SPEC-PORTAL-PROFILES-001 Phase 2 P2.4 — only values in
    # ADDON_PRODUCTS are accepted. Unknown values raise 400. Disabling an
    # add-on does NOT remove user/group entitlements — they become dormant
    # and re-activate when the toggle is turned back on. This is intentional:
    # preserves manual entitlement work done by the admin.
    """
    admin_user_id, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    unknown = [p for p in body.enabled_addons if p not in ADDON_PRODUCTS]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown add-on product(s): {unknown}. Valid: {sorted(ADDON_PRODUCTS)}",
        )

    before = list(org.enabled_addons or [])
    after = list(body.enabled_addons)

    org.enabled_addons = after

    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))

    await log_event(
        org_id=org.id,
        actor=admin_user_id,
        action="addons.updated",
        resource_type="org",
        resource_id=str(org.id),
        details={"before": before, "after": after, "added": added, "removed": removed},
    )
    await db.commit()

    emit_event(
        "tenant.addons_updated",
        org_id=org.id,
        user_id=admin_user_id,
        properties={"enabled_addons": after, "added": added, "removed": removed},
    )

    return AddonsOut(enabled_addons=after)
