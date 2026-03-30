"""
Resource access helpers -- all meeting and knowledge base queries go through here.

All query functions enforce org-level scoping. Group-scoped access is layered on top.

@MX:ANCHOR fan_in=3+ -- get_accessible_meetings is the authoritative entry point for
                         meeting queries. Do not bypass with direct select(VexaMeeting).
@MX:ANCHOR fan_in=3+ -- get_accessible_kb_slugs is the authoritative entry point for
                         KB access checks. Do not bypass.
"""

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.groups import PortalGroupMembership
from app.models.knowledge_bases import PortalGroupKBAccess, PortalKnowledgeBase, PortalUserKBAccess
from app.models.meetings import VexaMeeting


async def get_accessible_meetings(
    user_id: str,
    org_id: int | None,
    db: AsyncSession,
) -> list[VexaMeeting]:
    """Return meetings the user can access: owned + group-scoped (within the same org)."""
    if org_id is None:
        return []
    group_ids_subquery = (
        select(PortalGroupMembership.group_id).where(PortalGroupMembership.zitadel_user_id == user_id).scalar_subquery()
    )
    result = await db.execute(
        select(VexaMeeting).where(
            VexaMeeting.org_id == org_id,
            or_(
                VexaMeeting.zitadel_user_id == user_id,
                VexaMeeting.group_id.in_(group_ids_subquery),
            ),
        )
    )
    return list(result.scalars().all())


async def can_write_meeting(user_id: str, meeting: VexaMeeting, db: AsyncSession) -> bool:
    """Return True if user may write (update/delete) the meeting.

    Rules:
    - Owner always has write access.
    - If meeting is group-scoped, group admins also have write access.
    - Regular group members have read-only access.
    """
    if meeting.zitadel_user_id == user_id:
        return True

    if meeting.group_id is None:
        return False

    result = await db.execute(
        select(PortalGroupMembership).where(
            PortalGroupMembership.group_id == meeting.group_id,
            PortalGroupMembership.zitadel_user_id == user_id,
            PortalGroupMembership.is_group_admin.is_(True),
        )
    )
    return result.scalar_one_or_none() is not None


async def get_accessible_kb_slugs(user_id: str, db: AsyncSession) -> list[str]:
    """Return Qdrant kb_slug values the user can query.

    Includes:
    - "personal" (always, for personal knowledge)
    - "org" (always, for org-wide knowledge)
    - "group:{group_id}" for each group the user belongs to
    - Named KB slugs via group-KB access grants
    - Named KB slugs via direct user-KB access grants (portal_user_kb_access)
    """
    result = await db.execute(
        select(PortalGroupMembership.group_id).where(PortalGroupMembership.zitadel_user_id == user_id)
    )
    group_ids = [row[0] for row in result.all()]

    base_slugs = ["personal", "org"] + [f"group:{gid}" for gid in group_ids]

    # Named KB slugs via group-KB access
    group_kb_slugs: list[str] = []
    if group_ids:
        kb_result = await db.execute(
            select(PortalKnowledgeBase.slug)
            .join(PortalGroupKBAccess, PortalKnowledgeBase.id == PortalGroupKBAccess.kb_id)
            .where(PortalGroupKBAccess.group_id.in_(group_ids))
            .distinct()
        )
        group_kb_slugs = [row[0] for row in kb_result.all()]

    # Named KB slugs via direct user-KB access
    user_kb_result = await db.execute(
        select(PortalKnowledgeBase.slug)
        .join(PortalUserKBAccess, PortalKnowledgeBase.id == PortalUserKBAccess.kb_id)
        .where(PortalUserKBAccess.user_id == user_id)
        .distinct()
    )
    user_kb_slugs = [row[0] for row in user_kb_result.all()]

    all_named = list({*group_kb_slugs, *user_kb_slugs})
    return base_slugs + all_named


async def get_user_role_for_kb(kb_id: int, user_id: str, db: AsyncSession) -> str | None:
    """Return the effective role for user_id on kb_id, or None if no access.

    Checks both portal_user_kb_access (direct) and portal_group_kb_access (via groups).
    Returns the highest role found: owner > contributor > viewer.
    """
    role_rank = {"viewer": 1, "contributor": 2, "owner": 3}

    roles: list[str] = []

    # Direct user assignment
    direct = await db.execute(
        select(PortalUserKBAccess.role).where(
            PortalUserKBAccess.kb_id == kb_id,
            PortalUserKBAccess.user_id == user_id,
        )
    )
    direct_role = direct.scalar_one_or_none()
    if direct_role:
        roles.append(direct_role)

    # Via group membership
    group_ids_subq = (
        select(PortalGroupMembership.group_id).where(PortalGroupMembership.zitadel_user_id == user_id).scalar_subquery()
    )
    group_result = await db.execute(
        select(PortalGroupKBAccess.role).where(
            PortalGroupKBAccess.kb_id == kb_id,
            PortalGroupKBAccess.group_id.in_(group_ids_subq),
        )
    )
    for row in group_result.all():
        roles.append(row[0])

    if not roles:
        return None
    return max(roles, key=lambda r: role_rank.get(r, 0))


async def is_member_of_group(user_id: str, group_id: int, db: AsyncSession) -> bool:
    """Return True if user_id is a member of the given group."""
    result = await db.execute(
        select(PortalGroupMembership).where(
            PortalGroupMembership.group_id == group_id,
            PortalGroupMembership.zitadel_user_id == user_id,
        )
    )
    return result.scalar_one_or_none() is not None
