"""
Group management endpoints.
All endpoints require authentication and are scoped to the caller's org.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    _get_caller_org,
    _require_admin,
    _require_admin_or_group_admin,
    _require_admin_or_group_manager,
    bearer,
)
from app.core.database import get_db
from app.core.plans import get_plan_products
from app.models.groups import PortalGroup, PortalGroupMembership, PortalGroupProduct
from app.models.portal import PortalUser
from app.services.audit import log_event

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["groups"])


async def _get_group_or_404(group_id: int, org_id: int, db: AsyncSession) -> PortalGroup:
    result = await db.execute(select(PortalGroup).where(PortalGroup.id == group_id, PortalGroup.org_id == org_id))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Groep niet gevonden")
    return group


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class GroupCreateRequest(BaseModel):
    name: str
    description: str | None = None


class GroupUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None


class GroupOut(BaseModel):
    id: int
    name: str
    description: str | None
    products: list[str]
    is_system: bool
    created_at: datetime
    created_by: str


class GroupsResponse(BaseModel):
    groups: list[GroupOut]


class UserGroupOut(BaseModel):
    id: int
    name: str
    products: list[str]
    is_system: bool


class UserGroupsResponse(BaseModel):
    groups: list[UserGroupOut]


class MemberAddRequest(BaseModel):
    zitadel_user_id: str


class MemberOut(BaseModel):
    zitadel_user_id: str
    is_group_admin: bool
    joined_at: datetime


class MembersResponse(BaseModel):
    members: list[MemberOut]


class GroupAdminToggleRequest(BaseModel):
    is_group_admin: bool


class MessageResponse(BaseModel):
    message: str


class GroupProductAssignRequest(BaseModel):
    product: str


class GroupProductOut(BaseModel):
    product: str
    enabled_at: datetime
    enabled_by: str


class GroupProductsResponse(BaseModel):
    products: list[GroupProductOut]


# ---------------------------------------------------------------------------
# Group CRUD
# ---------------------------------------------------------------------------


@router.get("/groups", response_model=GroupsResponse)
async def list_groups(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> GroupsResponse:
    """List all groups in the caller's org."""
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    result = await db.execute(select(PortalGroup).where(PortalGroup.org_id == org.id).order_by(PortalGroup.name))
    groups = result.scalars().all()

    products_by_group: dict[int, list[str]] = {g.id: [] for g in groups}
    if groups:
        prods_result = await db.execute(
            select(PortalGroupProduct.group_id, PortalGroupProduct.product)
            .where(PortalGroupProduct.group_id.in_([g.id for g in groups]))
            .order_by(PortalGroupProduct.product)
        )
        for row in prods_result:
            products_by_group[row.group_id].append(row.product)

    return GroupsResponse(
        groups=[
            GroupOut(
                id=g.id,
                name=g.name,
                description=g.description,
                products=products_by_group[g.id],
                is_system=g.is_system,
                created_at=g.created_at,
                created_by=g.created_by,
            )
            for g in groups
        ]
    )


@router.post("/groups", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
async def create_group(
    body: GroupCreateRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> GroupOut:
    """Create a new group in the caller's org."""
    caller_user_id, org, caller_user = await _get_caller_org(credentials, db)
    await _require_admin_or_group_manager(caller_user, org.id, db)

    group = PortalGroup(
        org_id=org.id,
        name=body.name,
        description=body.description,
        created_by=caller_user_id,
    )
    db.add(group)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Groepsnaam bestaat al in deze organisatie",
        ) from exc

    await db.commit()
    await db.refresh(group)

    return GroupOut(
        id=group.id,
        name=group.name,
        description=group.description,
        products=[],
        is_system=False,
        created_at=group.created_at,
        created_by=group.created_by,
    )


@router.patch("/groups/{group_id}", response_model=GroupOut)
async def update_group(
    group_id: int,
    body: GroupUpdateRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> GroupOut:
    """Update a group's name or description."""
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    result = await db.execute(
        select(PortalGroup).where(
            PortalGroup.id == group_id,
            PortalGroup.org_id == org.id,
        )
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Groep niet gevonden")

    if group.is_system:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Systeemgroepen kunnen niet worden gewijzigd")

    if body.name is not None:
        group.name = body.name
    if body.description is not None:
        group.description = body.description

    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Groepsnaam bestaat al in deze organisatie",
        ) from exc

    await db.commit()
    await db.refresh(group)

    return GroupOut(
        id=group.id,
        name=group.name,
        description=group.description,
        products=[],
        is_system=group.is_system,
        created_at=group.created_at,
        created_by=group.created_by,
    )


@router.delete("/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a group (CASCADE removes memberships)."""
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    result = await db.execute(
        select(PortalGroup).where(
            PortalGroup.id == group_id,
            PortalGroup.org_id == org.id,
        )
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Groep niet gevonden")

    if group.is_system:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Systeemgroepen kunnen niet worden verwijderd",
        )

    await db.delete(group)
    await db.commit()


# ---------------------------------------------------------------------------
# Membership management
# ---------------------------------------------------------------------------


@router.get("/groups/{group_id}/members", response_model=MembersResponse)
async def list_members(
    group_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> MembersResponse:
    """List members of a group. Accessible by org admin or group admin."""
    _, org, caller_user = await _get_caller_org(credentials, db)

    # Verify group belongs to caller's org
    group_result = await db.execute(
        select(PortalGroup).where(
            PortalGroup.id == group_id,
            PortalGroup.org_id == org.id,
        )
    )
    if not group_result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Groep niet gevonden")

    await _require_admin_or_group_admin(group_id, caller_user, db)

    result = await db.execute(
        select(PortalGroupMembership)
        .where(PortalGroupMembership.group_id == group_id)
        .order_by(PortalGroupMembership.joined_at)
    )
    members = result.scalars().all()
    return MembersResponse(
        members=[
            MemberOut(
                zitadel_user_id=m.zitadel_user_id,
                is_group_admin=m.is_group_admin,
                joined_at=m.joined_at,
            )
            for m in members
        ]
    )


@router.post("/groups/{group_id}/members", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
async def add_member(
    group_id: int,
    body: MemberAddRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Add a member to a group. Admin or group admin. Cross-org validation (R5)."""
    caller_id, org, caller_user = await _get_caller_org(credentials, db)

    # Verify group belongs to caller's org
    group_result = await db.execute(
        select(PortalGroup).where(
            PortalGroup.id == group_id,
            PortalGroup.org_id == org.id,
        )
    )
    group = group_result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Groep niet gevonden")

    await _require_admin_or_group_admin(group_id, caller_user, db)

    # R5: Cross-org security -- verify user belongs to same org as group
    user_result = await db.execute(select(PortalUser).where(PortalUser.zitadel_user_id == body.zitadel_user_id))
    target_user = user_result.scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gebruiker niet gevonden")

    if target_user.org_id != group.org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Gebruiker behoort niet tot dezelfde organisatie als de groep",
        )

    membership = PortalGroupMembership(
        group_id=group_id,
        zitadel_user_id=body.zitadel_user_id,
    )
    db.add(membership)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Gebruiker is al lid van deze groep",
        ) from exc

    await log_event(
        db,
        org_id=group.org_id,
        actor=caller_id,
        action="group.member_added",
        resource_type="group",
        resource_id=str(group_id),
        details={"user_id": body.zitadel_user_id},
    )
    await db.commit()
    return MessageResponse(message="Lid toegevoegd aan groep")


@router.delete("/groups/{group_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    group_id: int,
    user_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove a member from a group. Admin or group admin."""
    caller_id, org, caller_user = await _get_caller_org(credentials, db)

    # Verify group belongs to caller's org
    group_result = await db.execute(
        select(PortalGroup).where(
            PortalGroup.id == group_id,
            PortalGroup.org_id == org.id,
        )
    )
    if not group_result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Groep niet gevonden")

    await _require_admin_or_group_admin(group_id, caller_user, db)

    result = await db.execute(
        select(PortalGroupMembership).where(
            PortalGroupMembership.group_id == group_id,
            PortalGroupMembership.zitadel_user_id == user_id,
        )
    )
    membership = result.scalar_one_or_none()
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lidmaatschap niet gevonden")

    await db.delete(membership)
    await log_event(
        db,
        org_id=org.id,
        actor=caller_id,
        action="group.member_removed",
        resource_type="group",
        resource_id=str(group_id),
        details={"user_id": user_id},
    )
    await db.commit()


@router.patch("/groups/{group_id}/members/{user_id}", response_model=MessageResponse)
async def toggle_group_admin(
    group_id: int,
    user_id: str,
    body: GroupAdminToggleRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Toggle is_group_admin for a member. Admin only."""
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    # Verify group belongs to caller's org
    group_result = await db.execute(
        select(PortalGroup).where(
            PortalGroup.id == group_id,
            PortalGroup.org_id == org.id,
        )
    )
    if not group_result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Groep niet gevonden")

    result = await db.execute(
        select(PortalGroupMembership).where(
            PortalGroupMembership.group_id == group_id,
            PortalGroupMembership.zitadel_user_id == user_id,
        )
    )
    membership = result.scalar_one_or_none()
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lidmaatschap niet gevonden")

    membership.is_group_admin = body.is_group_admin
    await db.commit()

    action = "toegekend" if body.is_group_admin else "ingetrokken"
    return MessageResponse(message=f"Groepsbeheerder rechten {action}")


# ---------------------------------------------------------------------------
# Group product entitlements
# ---------------------------------------------------------------------------


@router.get("/groups/{group_id}/products", response_model=GroupProductsResponse)
async def list_group_products(
    group_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> GroupProductsResponse:
    """List products assigned to a group. Org admin only."""
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)
    await _get_group_or_404(group_id, org.id, db)

    result = await db.execute(
        select(PortalGroupProduct).where(PortalGroupProduct.group_id == group_id).order_by(PortalGroupProduct.product)
    )
    products = result.scalars().all()
    return GroupProductsResponse(
        products=[
            GroupProductOut(product=p.product, enabled_at=p.enabled_at, enabled_by=p.enabled_by) for p in products
        ]
    )


@router.post("/groups/{group_id}/products", response_model=GroupProductOut, status_code=status.HTTP_201_CREATED)
async def assign_group_product(
    group_id: int,
    body: GroupProductAssignRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> GroupProductOut:
    """Assign a product to a group. Org admin only, plan ceiling enforced."""
    caller_user_id, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)
    await _get_group_or_404(group_id, org.id, db)

    # Plan ceiling check
    if body.product not in get_plan_products(org.plan):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Product not included in org plan",
        )

    record = PortalGroupProduct(
        group_id=group_id,
        org_id=org.id,
        product=body.product,
        enabled_by=caller_user_id,
    )
    db.add(record)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Product already assigned to group",
        ) from exc

    await log_event(
        db,
        org_id=org.id,
        actor=caller_user_id,
        action="group_product.assigned",
        resource_type="group_product",
        resource_id=f"{group_id}:{body.product}",
        details={"product": body.product, "group_id": group_id},
    )
    await db.commit()
    await db.refresh(record)
    return GroupProductOut(product=record.product, enabled_at=record.enabled_at, enabled_by=record.enabled_by)


@router.delete("/groups/{group_id}/products/{product}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_group_product(
    group_id: int,
    product: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Revoke a product from a group. Org admin only."""
    caller_user_id, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)
    await _get_group_or_404(group_id, org.id, db)

    result = await db.execute(
        select(PortalGroupProduct).where(
            PortalGroupProduct.group_id == group_id,
            PortalGroupProduct.product == product,
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product toewijzing niet gevonden")

    await db.delete(row)
    await log_event(
        db,
        org_id=org.id,
        actor=caller_user_id,
        action="group_product.revoked",
        resource_type="group_product",
        resource_id=f"{group_id}:{product}",
        details={"product": product, "group_id": group_id},
    )
    await db.commit()


# ---------------------------------------------------------------------------
# User group membership view
# ---------------------------------------------------------------------------


@router.get("/users/{user_id}/groups", response_model=UserGroupsResponse)
async def get_user_groups(
    user_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> UserGroupsResponse:
    """List groups a user belongs to, with product info. Org admin only."""
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    user_result = await db.execute(
        select(PortalUser).where(PortalUser.zitadel_user_id == user_id, PortalUser.org_id == org.id)
    )
    if not user_result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gebruiker niet gevonden")

    memberships_result = await db.execute(
        select(PortalGroupMembership.group_id).where(PortalGroupMembership.zitadel_user_id == user_id)
    )
    group_ids = list(memberships_result.scalars().all())

    if not group_ids:
        return UserGroupsResponse(groups=[])

    groups_result = await db.execute(
        select(PortalGroup)
        .where(PortalGroup.id.in_(group_ids), PortalGroup.org_id == org.id)
        .order_by(PortalGroup.name)
    )
    groups = groups_result.scalars().all()

    products_by_group: dict[int, list[str]] = {g.id: [] for g in groups}
    prods_result = await db.execute(
        select(PortalGroupProduct.group_id, PortalGroupProduct.product)
        .where(PortalGroupProduct.group_id.in_([g.id for g in groups]))
        .order_by(PortalGroupProduct.product)
    )
    for row in prods_result:
        products_by_group[row.group_id].append(row.product)

    return UserGroupsResponse(
        groups=[
            UserGroupOut(id=g.id, name=g.name, products=products_by_group[g.id], is_system=g.is_system)
            for g in groups
        ]
    )
