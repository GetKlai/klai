"""
Group management endpoints.
All endpoints require authentication and are scoped to the caller's org.

SPEC-PORTAL-RBAC-001 v0.2.0: groups are content-scoping (KB access) only.
Products are derived from (profile, plan, platform_unlocked_features) and
not assigned per group. The legacy product-assignment endpoints below
return 410 Gone.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.permissions import UserPermissions, get_caller_at_least
from app.core.profiles import ProfileRole
from app.models.groups import PortalGroup, PortalGroupMembership
from app.models.portal import PortalUser
from app.services.audit import log_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["groups"])


_GONE_BODY = (
    "Endpoint removed by SPEC-PORTAL-RBAC-001. Products derive from "
    "/admin/settings (plan + add-ons) and /admin/users/<id>/edit (profile)."
)


async def _get_group_or_404(group_id: int, org_id: int, db: AsyncSession) -> PortalGroup:
    result = await db.execute(select(PortalGroup).where(PortalGroup.id == group_id, PortalGroup.org_id == org_id))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
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
    is_system: bool
    created_at: datetime
    created_by: str


class GroupsResponse(BaseModel):
    groups: list[GroupOut]


class UserGroupOut(BaseModel):
    id: int
    name: str
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


# ---------------------------------------------------------------------------
# Group CRUD
# ---------------------------------------------------------------------------


@router.get("/groups", response_model=GroupsResponse)
async def list_groups(
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> GroupsResponse:
    """List all (non-system) groups in the caller's org. Admin only.

    The system_key IS NULL filter is a defence-in-depth net: after the
    SPEC-PORTAL-RBAC-001 migration there are no system groups in any tenant.
    """
    result = await db.execute(
        select(PortalGroup)
        .where(PortalGroup.org_id == perms.org_id, PortalGroup.system_key.is_(None))
        .order_by(PortalGroup.name)
    )
    groups = list(result.scalars().all())

    return GroupsResponse(
        groups=[
            GroupOut(
                id=g.id,
                name=g.name,
                description=g.description,
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
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.GROUP_MANAGER)),
    db: AsyncSession = Depends(get_db),
) -> GroupOut:
    """Create a new group in the caller's org. group_manager+ may create."""
    group = PortalGroup(
        org_id=perms.org_id,
        name=body.name,
        description=body.description,
        created_by=perms.user_id,
    )
    db.add(group)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Group name already exists in this organisation",
        ) from exc

    await db.refresh(group)  # Pre-commit refresh to load server_default columns while tenant context is still set.
    await db.commit()

    return GroupOut(
        id=group.id,
        name=group.name,
        description=group.description,
        is_system=False,
        created_at=group.created_at,
        created_by=group.created_by,
    )


@router.patch("/groups/{group_id}", response_model=GroupOut)
async def update_group(
    group_id: int,
    body: GroupUpdateRequest,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.GROUP_MANAGER)),
    db: AsyncSession = Depends(get_db),
) -> GroupOut:
    """Update a group's name or description."""
    result = await db.execute(
        select(PortalGroup).where(
            PortalGroup.id == group_id,
            PortalGroup.org_id == perms.org_id,
        )
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    if group.is_system:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="System groups cannot be modified")

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
            detail="Group name already exists in this organisation",
        ) from exc

    await db.commit()
    # No post-commit refresh: RLS tenant context is transaction-scoped (see SPEC-SEC-021 post-mortem).

    return GroupOut(
        id=group.id,
        name=group.name,
        description=group.description,
        is_system=group.is_system,
        created_at=group.created_at,
        created_by=group.created_by,
    )


@router.delete("/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: int,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.GROUP_MANAGER)),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a group (CASCADE removes memberships)."""
    result = await db.execute(
        select(PortalGroup).where(
            PortalGroup.id == group_id,
            PortalGroup.org_id == perms.org_id,
        )
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    if group.is_system:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="System groups cannot be deleted",
        )

    await db.delete(group)
    await db.commit()


# ---------------------------------------------------------------------------
# Membership management
# ---------------------------------------------------------------------------


@router.get("/groups/{group_id}/members", response_model=MembersResponse)
async def list_members(
    group_id: int,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.GROUP_MANAGER)),
    db: AsyncSession = Depends(get_db),
) -> MembersResponse:
    """List members of a group. group_manager+ may view."""
    # Verify group belongs to caller's org
    group_result = await db.execute(
        select(PortalGroup).where(
            PortalGroup.id == group_id,
            PortalGroup.org_id == perms.org_id,
        )
    )
    if not group_result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

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
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.GROUP_MANAGER)),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Add a member to a group. group_manager+ may add. Cross-org validation (R5)."""
    # Verify group belongs to caller's org
    group_result = await db.execute(
        select(PortalGroup).where(
            PortalGroup.id == group_id,
            PortalGroup.org_id == perms.org_id,
        )
    )
    group = group_result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    # R5: Cross-org security -- verify user belongs to same org as group
    user_result = await db.execute(select(PortalUser).where(PortalUser.zitadel_user_id == body.zitadel_user_id))
    target_user = user_result.scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if target_user.org_id != group.org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not belong to the same organisation as the group",
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
            detail="User is already a member of this group",
        ) from exc

    await log_event(
        org_id=group.org_id,
        actor=perms.user_id,
        action="group.member_added",
        resource_type="group",
        resource_id=str(group_id),
        details={"user_id": body.zitadel_user_id},
    )
    await db.commit()
    return MessageResponse(message="Member added to group")


@router.delete("/groups/{group_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    group_id: int,
    user_id: str,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.GROUP_MANAGER)),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove a member from a group. group_manager+ may remove."""
    # Verify group belongs to caller's org
    group_result = await db.execute(
        select(PortalGroup).where(
            PortalGroup.id == group_id,
            PortalGroup.org_id == perms.org_id,
        )
    )
    if not group_result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    result = await db.execute(
        select(PortalGroupMembership).where(
            PortalGroupMembership.group_id == group_id,
            PortalGroupMembership.zitadel_user_id == user_id,
        )
    )
    membership = result.scalar_one_or_none()
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")

    await db.delete(membership)
    await log_event(
        org_id=perms.org_id,
        actor=perms.user_id,
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
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.GROUP_MANAGER)),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Toggle is_group_admin for a member. group_manager+ only."""
    # Verify group belongs to caller's org
    group_result = await db.execute(
        select(PortalGroup).where(
            PortalGroup.id == group_id,
            PortalGroup.org_id == perms.org_id,
        )
    )
    if not group_result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    result = await db.execute(
        select(PortalGroupMembership).where(
            PortalGroupMembership.group_id == group_id,
            PortalGroupMembership.zitadel_user_id == user_id,
        )
    )
    membership = result.scalar_one_or_none()
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")

    membership.is_group_admin = body.is_group_admin
    await db.commit()

    action = "granted" if body.is_group_admin else "revoked"
    return MessageResponse(message=f"Group admin rights {action}")


# ---------------------------------------------------------------------------
# Group product entitlements -- REMOVED by SPEC-PORTAL-RBAC-001
# ---------------------------------------------------------------------------


@router.get("/groups/{group_id}/products", status_code=status.HTTP_410_GONE)
async def list_group_products_gone(group_id: int) -> dict:
    raise HTTPException(status_code=status.HTTP_410_GONE, detail=_GONE_BODY)


@router.post("/groups/{group_id}/products", status_code=status.HTTP_410_GONE)
async def assign_group_product_gone(group_id: int) -> dict:
    raise HTTPException(status_code=status.HTTP_410_GONE, detail=_GONE_BODY)


@router.delete("/groups/{group_id}/products/{product}", status_code=status.HTTP_410_GONE)
async def revoke_group_product_gone(group_id: int, product: str) -> dict:
    raise HTTPException(status_code=status.HTTP_410_GONE, detail=_GONE_BODY)


# ---------------------------------------------------------------------------
# User group membership view
# ---------------------------------------------------------------------------


@router.get("/users/{user_id}/groups", response_model=UserGroupsResponse)
async def get_user_groups(
    user_id: str,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> UserGroupsResponse:
    """List (non-system) groups a user belongs to. Org admin only."""
    user_result = await db.execute(
        select(PortalUser).where(PortalUser.zitadel_user_id == user_id, PortalUser.org_id == perms.org_id)
    )
    if not user_result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    memberships_result = await db.execute(
        select(PortalGroupMembership.group_id).where(PortalGroupMembership.zitadel_user_id == user_id)
    )
    group_ids = list(memberships_result.scalars().all())

    if not group_ids:
        return UserGroupsResponse(groups=[])

    groups_result = await db.execute(
        select(PortalGroup)
        .where(
            PortalGroup.id.in_(group_ids),
            PortalGroup.org_id == perms.org_id,
            PortalGroup.system_key.is_(None),
        )
        .order_by(PortalGroup.name)
    )
    groups = groups_result.scalars().all()

    return UserGroupsResponse(groups=[UserGroupOut(id=g.id, name=g.name, is_system=g.is_system) for g in groups])


# ---------------------------------------------------------------------------
# Bulk user-group membership view (for users list page)
# ---------------------------------------------------------------------------


class UserMembershipsResponse(BaseModel):
    memberships: dict[str, list[UserGroupOut]]


@router.get("/group-memberships", response_model=UserMembershipsResponse)
async def get_all_memberships(
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> UserMembershipsResponse:
    """Return all (non-system) user-group memberships for the org, keyed by zitadel_user_id."""
    # Fetch non-system groups in the org
    groups_result = await db.execute(
        select(PortalGroup).where(PortalGroup.org_id == perms.org_id, PortalGroup.system_key.is_(None))
    )
    groups = groups_result.scalars().all()

    if not groups:
        return UserMembershipsResponse(memberships={})

    group_map = {g.id: g for g in groups}

    # Fetch all memberships for these groups in one query
    memberships_result = await db.execute(
        select(PortalGroupMembership).where(PortalGroupMembership.group_id.in_(list(group_map.keys())))
    )
    memberships = memberships_result.scalars().all()

    # Build map: user_id -> list of UserGroupOut
    result: dict[str, list[UserGroupOut]] = {}
    for m in memberships:
        g = group_map.get(m.group_id)
        if g is None:
            continue
        result.setdefault(m.zitadel_user_id, []).append(UserGroupOut(id=g.id, name=g.name, is_system=g.is_system))

    return UserMembershipsResponse(memberships=result)
