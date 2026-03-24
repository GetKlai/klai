"""
Admin user management endpoints.
All endpoints require authentication and resolve the caller's org from their OIDC token.
"""

import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.plans import PLAN_PRODUCTS, get_plan_products
from app.models.audit import PortalAuditLog
from app.models.groups import PortalGroupMembership, PortalGroupProduct
from app.models.portal import PortalOrg, PortalUser
from app.models.products import PortalUserProduct
from app.services.audit import log_event
from app.services.zitadel import zitadel

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])
bearer = HTTPBearer()


async def _get_caller_org(
    credentials: HTTPAuthorizationCredentials,
    db: AsyncSession,
) -> tuple[str, "PortalOrg", "PortalUser"]:
    """Validate token, return (zitadel_user_id, PortalOrg, caller PortalUser)."""
    try:
        info = await zitadel.get_userinfo(credentials.credentials)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Ongeldig token") from exc

    zitadel_user_id = info.get("sub")
    if not zitadel_user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Geen gebruiker gevonden in token")

    result = await db.execute(
        select(PortalOrg, PortalUser)
        .join(PortalUser, PortalUser.org_id == PortalOrg.id)
        .where(PortalUser.zitadel_user_id == zitadel_user_id)
    )
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organisatie niet gevonden")

    org, caller_user = row
    return zitadel_user_id, org, caller_user


def _require_admin(caller_user: "PortalUser") -> None:
    """Raise 403 if the caller is not an admin."""
    if caller_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Geen toegang: admin rechten vereist")


class UserOut(BaseModel):
    zitadel_user_id: str
    email: str
    first_name: str
    last_name: str
    role: Literal["admin", "member"]
    preferred_language: Literal["nl", "en"]
    status: str
    created_at: datetime
    invite_pending: bool


class UsersResponse(BaseModel):
    users: list[UserOut]


class InviteRequest(BaseModel):
    email: EmailStr
    first_name: str
    last_name: str
    role: Literal["admin", "member"] = "member"
    preferred_language: Literal["nl", "en"] = "nl"


class InviteResponse(BaseModel):
    user_id: str
    message: str


class UserUpdateRequest(BaseModel):
    first_name: str
    last_name: str
    preferred_language: Literal["nl", "en"]


class RoleUpdateRequest(BaseModel):
    role: Literal["admin", "member"]


class OrgSettingsOut(BaseModel):
    name: str
    default_language: Literal["nl", "en"]
    mfa_policy: Literal["optional", "recommended", "required"] = "optional"


class OrgSettingsUpdate(BaseModel):
    default_language: Literal["nl", "en"] | None = None
    mfa_policy: Literal["optional", "recommended", "required"] | None = None


class MessageResponse(BaseModel):
    message: str


class ProductAssignRequest(BaseModel):
    product: str


class ProductOut(BaseModel):
    product: str
    enabled_at: datetime
    enabled_by: str


class ProductsResponse(BaseModel):
    products: list[str]


class UserProductsResponse(BaseModel):
    products: list[ProductOut]


class ProductSummaryItem(BaseModel):
    product: str
    user_count: int


class ProductSummaryResponse(BaseModel):
    items: list[ProductSummaryItem]


class PlanChangeRequest(BaseModel):
    plan: str


class AuditLogEntry(BaseModel):
    id: int
    actor_user_id: str
    action: str
    resource_type: str
    resource_id: str
    details: dict | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditLogResponse(BaseModel):
    items: list[AuditLogEntry]
    total: int
    page: int
    size: int


@router.get("/users", response_model=UsersResponse)
async def list_users(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> UsersResponse:
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    # Get portal membership records (mapping + created_at + role)
    result = await db.execute(select(PortalUser).where(PortalUser.org_id == org.id).order_by(PortalUser.created_at))
    portal_users = {u.zitadel_user_id: u for u in result.scalars().all()}

    if not portal_users:
        return UsersResponse(users=[])

    # Fetch live identity details from Zitadel (all users live in portal org)
    zitadel_users = await zitadel.list_org_users(settings.zitadel_portal_org_id)

    users_out: list[UserOut] = []
    for z in zitadel_users:
        uid = z.get("id", "")
        if uid not in portal_users:
            continue  # not in our portal (e.g. service accounts)
        profile = z.get("human", {}).get("profile", {})
        email_obj = z.get("human", {}).get("email", {})
        portal_user = portal_users[uid]
        users_out.append(
            UserOut(
                zitadel_user_id=uid,
                email=email_obj.get("email", ""),
                first_name=profile.get("firstName", ""),
                last_name=profile.get("lastName", ""),
                role=portal_user.role,
                preferred_language=portal_user.preferred_language,
                status=portal_user.status,
                created_at=portal_user.created_at,
                invite_pending=z.get("state") == "USER_STATE_INITIAL",
            )
        )

    return UsersResponse(users=users_out)


@router.post("/users/invite", response_model=InviteResponse, status_code=status.HTTP_201_CREATED)
async def invite_user(
    body: InviteRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> InviteResponse:
    admin_user_id, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    # Seat enforcement: lock the org row to prevent concurrent invites
    locked_result = await db.execute(select(PortalOrg).where(PortalOrg.id == org.id).with_for_update())
    org = locked_result.scalar_one()

    # TODO: filter by status == 'active' once AUTH-001 adds status column
    active_count = await db.scalar(select(func.count()).select_from(PortalUser).where(PortalUser.org_id == org.id))
    if active_count is not None and active_count >= org.seats:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Seat limit reached")

    try:
        user_data = await zitadel.invite_user(
            org_id=settings.zitadel_portal_org_id,
            email=body.email,
            first_name=body.first_name,
            last_name=body.last_name,
            preferred_language=body.preferred_language,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Kon gebruiker niet uitnodigen: {exc}",
        ) from exc

    zitadel_user_id: str = user_data["userId"]

    try:
        await zitadel.grant_user_role(
            org_id=settings.zitadel_portal_org_id,
            user_id=zitadel_user_id,
            role="org:owner",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Kon projectrol niet toewijzen: {exc}",
        ) from exc

    user_row = PortalUser(
        zitadel_user_id=zitadel_user_id,
        org_id=org.id,
        role=body.role,
        preferred_language=body.preferred_language,
    )
    db.add(user_row)

    # Auto-assign products based on org plan
    plan_products = get_plan_products(org.plan)
    for product in plan_products:
        db.add(
            PortalUserProduct(
                zitadel_user_id=zitadel_user_id,
                org_id=org.id,
                product=product,
                enabled_by=admin_user_id,
            )
        )

    await db.commit()

    return InviteResponse(
        user_id=zitadel_user_id,
        message=f"Uitnodiging verstuurd naar {body.email}.",
    )


@router.patch("/users/{zitadel_user_id}", response_model=MessageResponse)
async def update_user(
    zitadel_user_id: str,
    body: UserUpdateRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    result = await db.execute(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalUser.org_id == org.id,
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gebruiker niet gevonden")

    try:
        await zitadel.update_user_profile(
            org_id=settings.zitadel_portal_org_id,
            user_id=zitadel_user_id,
            first_name=body.first_name,
            last_name=body.last_name,
            preferred_language=body.preferred_language,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Kon gebruiker niet bijwerken: {exc}",
        ) from exc

    user.preferred_language = body.preferred_language
    await db.commit()

    return MessageResponse(message="Gebruiker bijgewerkt.")


@router.patch("/users/{zitadel_user_id}/role", response_model=MessageResponse)
async def update_user_role(
    zitadel_user_id: str,
    body: RoleUpdateRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    result = await db.execute(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalUser.org_id == org.id,
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gebruiker niet gevonden")

    user.role = body.role
    await db.commit()

    return MessageResponse(message="Rol bijgewerkt.")


@router.get("/settings", response_model=OrgSettingsOut)
async def get_org_settings(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> OrgSettingsOut:
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)
    return OrgSettingsOut(name=org.name, default_language=org.default_language, mfa_policy=org.mfa_policy)


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
    await db.commit()
    return OrgSettingsOut(name=org.name, default_language=org.default_language, mfa_policy=org.mfa_policy)


@router.post("/users/{zitadel_user_id}/resend-invite", response_model=MessageResponse)
async def resend_invite(
    zitadel_user_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    result = await db.execute(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalUser.org_id == org.id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gebruiker niet gevonden")

    try:
        await zitadel.resend_init_mail(
            org_id=settings.zitadel_portal_org_id,
            user_id=zitadel_user_id,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Kon uitnodiging niet opnieuw sturen: {exc}",
        ) from exc

    return MessageResponse(message="Uitnodiging opnieuw verstuurd.")


@router.delete("/users/{zitadel_user_id}", response_model=MessageResponse)
async def remove_user(
    zitadel_user_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    # Verify user belongs to this org before deleting
    result = await db.execute(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalUser.org_id == org.id,
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gebruiker niet gevonden")

    try:
        await zitadel.remove_user(org_id=settings.zitadel_portal_org_id, zitadel_user_id=zitadel_user_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Kon gebruiker niet verwijderen: {exc}",
        ) from exc

    await db.delete(user)
    await db.commit()

    return MessageResponse(message="Gebruiker verwijderd.")


# ---------------------------------------------------------------------------
# Product entitlement endpoints
# ---------------------------------------------------------------------------


@router.get("/products", response_model=ProductsResponse)
async def list_available_products(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> ProductsResponse:
    """Return products available under the org's current plan."""
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)
    return ProductsResponse(products=get_plan_products(org.plan))


@router.post("/users/{zitadel_user_id}/products", status_code=status.HTTP_201_CREATED)
async def assign_product(
    zitadel_user_id: str,
    body: ProductAssignRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Assign a product to a user within plan ceiling."""
    admin_user_id, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    # Plan ceiling check
    if body.product not in get_plan_products(org.plan):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Product '{body.product}' exceeds plan ceiling",
        )

    # Check user belongs to this org
    user = await db.scalar(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalUser.org_id == org.id,
        )
    )
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gebruiker niet gevonden")

    # Check for duplicate
    existing = await db.scalar(
        select(PortalUserProduct).where(
            PortalUserProduct.zitadel_user_id == zitadel_user_id,
            PortalUserProduct.product == body.product,
        )
    )
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Product already assigned")

    db.add(
        PortalUserProduct(
            zitadel_user_id=zitadel_user_id,
            org_id=org.id,
            product=body.product,
            enabled_by=admin_user_id,
        )
    )
    await db.flush()
    await log_event(
        db,
        org_id=org.id,
        actor=admin_user_id,
        action="product.assigned",
        resource_type="product",
        resource_id=f"{zitadel_user_id}:{body.product}",
        details={"product": body.product, "user_id": zitadel_user_id},
    )
    await db.commit()
    return MessageResponse(message="Product assigned")


@router.delete("/users/{zitadel_user_id}/products/{product}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_product(
    zitadel_user_id: str,
    product: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Revoke a product from a user."""
    admin_user_id, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    result = await db.execute(
        select(PortalUserProduct).where(
            PortalUserProduct.zitadel_user_id == zitadel_user_id,
            PortalUserProduct.product == product,
            PortalUserProduct.org_id == org.id,
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product assignment not found")

    await db.delete(row)
    await log_event(
        db,
        org_id=org.id,
        actor=admin_user_id,
        action="product.revoked",
        resource_type="product",
        resource_id=f"{zitadel_user_id}:{product}",
        details={"product": product, "user_id": zitadel_user_id},
    )
    await db.commit()


@router.get("/products/summary", response_model=ProductSummaryResponse)
async def product_summary(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> ProductSummaryResponse:
    """Return per-product user counts for the org."""
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    rows = await db.execute(
        select(PortalUserProduct.product, func.count().label("user_count"))
        .where(PortalUserProduct.org_id == org.id)
        .group_by(PortalUserProduct.product)
    )
    return ProductSummaryResponse(items=[ProductSummaryItem(product=r.product, user_count=r.user_count) for r in rows])


@router.get("/users/{zitadel_user_id}/products", response_model=UserProductsResponse)
async def get_user_products(
    zitadel_user_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> UserProductsResponse:
    """Return products assigned to a specific user."""
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    # Verify user belongs to org
    user = await db.scalar(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalUser.org_id == org.id,
        )
    )
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gebruiker niet gevonden")

    result = await db.execute(
        select(PortalUserProduct).where(
            PortalUserProduct.zitadel_user_id == zitadel_user_id,
            PortalUserProduct.org_id == org.id,
        )
    )
    products = result.scalars().all()
    return UserProductsResponse(
        products=[ProductOut(product=p.product, enabled_at=p.enabled_at, enabled_by=p.enabled_by) for p in products]
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
            log.info(
                "Plan downgrade: revoking product %s from user %s (org %s, %s -> %s)",
                row.product,
                row.zitadel_user_id,
                org.id,
                old_plan,
                new_plan,
            )
            await db.delete(row)

    # Downgrade: also revoke group products that exceed the new plan ceiling
    group_revoked_result = await db.execute(
        select(PortalGroupProduct).where(PortalGroupProduct.org_id == org.id)
    )
    all_group_assignments = group_revoked_result.scalars().all()
    for row in all_group_assignments:
        if row.product not in new_products:
            log.info(
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


@router.post("/users/{zitadel_user_id}/suspend", response_model=MessageResponse)
async def suspend_user(
    zitadel_user_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Suspend an active user. Preserves group memberships and products."""
    caller_id, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    result = await db.execute(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalUser.org_id == org.id,
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gebruiker niet gevonden")
    if user.status in ("suspended", "offboarded"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Gebruiker heeft status '{user.status}' en kan niet worden geschorst",
        )

    user.status = "suspended"
    await log_event(
        db,
        org_id=org.id,
        actor=caller_id,
        action="user.suspended",
        resource_type="user",
        resource_id=zitadel_user_id,
    )
    await db.commit()
    return MessageResponse(message=f"Gebruiker {zitadel_user_id} geschorst.")


@router.post("/users/{zitadel_user_id}/reactivate", response_model=MessageResponse)
async def reactivate_user(
    zitadel_user_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Reactivate a suspended user."""
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    result = await db.execute(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalUser.org_id == org.id,
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gebruiker niet gevonden")
    if user.status != "suspended":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Gebruiker heeft status '{user.status}' en kan niet worden gereactiveerd",
        )

    user.status = "active"
    await db.commit()
    return MessageResponse(message=f"Gebruiker {zitadel_user_id} gereactiveerd.")


@router.post("/users/{zitadel_user_id}/offboard", response_model=MessageResponse)
async def offboard_user(
    zitadel_user_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Offboard a user: remove memberships + products, deactivate in Zitadel, set status."""
    caller_id, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    result = await db.execute(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalUser.org_id == org.id,
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gebruiker niet gevonden")
    if user.status == "offboarded":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Gebruiker is al offboarded")

    # Cascade: remove group memberships and product assignments
    await db.execute(delete(PortalGroupMembership).where(PortalGroupMembership.zitadel_user_id == zitadel_user_id))
    await db.execute(
        delete(PortalUserProduct).where(
            PortalUserProduct.zitadel_user_id == zitadel_user_id,
            PortalUserProduct.org_id == org.id,
        )
    )

    user.status = "offboarded"
    await log_event(
        db,
        org_id=org.id,
        actor=caller_id,
        action="user.offboarded",
        resource_type="user",
        resource_id=zitadel_user_id,
    )
    await zitadel.deactivate_user(settings.zitadel_portal_org_id, zitadel_user_id)
    await db.commit()
    return MessageResponse(message=f"Gebruiker {zitadel_user_id} offboarded.")


# ---------------------------------------------------------------------------
# Audit log viewer
# ---------------------------------------------------------------------------


@router.get("/audit-log", response_model=AuditLogResponse)
async def get_audit_log(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
    page: int = 1,
    size: int = 20,
    action: str | None = None,
    resource_type: str | None = None,
) -> AuditLogResponse:
    """Paginated audit log for the caller's org. Admin only.

    Filters:
    - action: exact match (e.g. "meeting.created")
    - resource_type: exact match (e.g. "group", "meeting", "product", "user")
    """
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    query = select(PortalAuditLog).where(PortalAuditLog.org_id == org.id)
    count_query = select(func.count(PortalAuditLog.id)).where(PortalAuditLog.org_id == org.id)

    if action:
        query = query.where(PortalAuditLog.action == action)
        count_query = count_query.where(PortalAuditLog.action == action)
    if resource_type:
        query = query.where(PortalAuditLog.resource_type == resource_type)
        count_query = count_query.where(PortalAuditLog.resource_type == resource_type)

    total = await db.scalar(count_query) or 0
    offset = (page - 1) * size
    result = await db.execute(query.order_by(PortalAuditLog.created_at.desc()).offset(offset).limit(size))
    items = result.scalars().all()

    return AuditLogResponse(
        items=[AuditLogEntry.model_validate(e) for e in items],
        total=total,
        page=page,
        size=size,
    )
