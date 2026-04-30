"""Admin org settings and plan management endpoints."""

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.plans import PLAN_PRODUCTS, get_plan_products
from app.models.groups import PortalGroupProduct
from app.models.products import PortalUserProduct

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
