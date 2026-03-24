"""
Resource access helpers -- all meeting and knowledge base queries go through here.

All query functions enforce org-level scoping. Group-scoped access is layered on top.

@MX:ANCHOR fan_in=3+ -- get_accessible_meetings is the authoritative entry point for
                         meeting queries. Do not bypass with direct select(VexaMeeting).
"""

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.groups import PortalGroupMembership
from app.models.meetings import VexaMeeting


async def get_accessible_meetings(
    user_id: str,
    org_id: int,
    db: AsyncSession,
) -> list[VexaMeeting]:
    """Return meetings the user can access: owned + group-scoped (within the same org)."""
    group_ids_subquery = (
        select(PortalGroupMembership.group_id)
        .where(PortalGroupMembership.zitadel_user_id == user_id)
        .scalar_subquery()
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
    """
    result = await db.execute(
        select(PortalGroupMembership.group_id).where(
            PortalGroupMembership.zitadel_user_id == user_id
        )
    )
    group_ids = [row[0] for row in result.all()]
    return ["personal", "org"] + [f"group:{gid}" for gid in group_ids]


async def is_member_of_group(user_id: str, group_id: int, db: AsyncSession) -> bool:
    """Return True if user_id is a member of the given group."""
    result = await db.execute(
        select(PortalGroupMembership).where(
            PortalGroupMembership.group_id == group_id,
            PortalGroupMembership.zitadel_user_id == user_id,
        )
    )
    return result.scalar_one_or_none() is not None
