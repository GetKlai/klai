"""Helpers for global user-membership decisions.

The portal identity lives once in Zitadel, while ``portal_users`` can contain
one row per tenant. Any code that may delete the global identity must first
check whether other tenant memberships still exist.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.portal import PortalOrg, PortalUser


@dataclass(frozen=True)
class UserMembershipSummary:
    total_count: int
    remaining_count: int
    is_platform_admin: bool


@dataclass(frozen=True)
class UserGlobalMembershipState:
    total_count: int
    active_count: int
    admin_count: int


async def get_user_membership_summary(
    zitadel_user_id: str,
    *,
    excluding_org_id: int | None = None,
) -> UserMembershipSummary:
    """Return global membership counts for one Zitadel identity.

    ``portal_users`` is intentionally Cat-A RLS: a fresh session with no tenant
    GUC can read memberships by ``zitadel_user_id`` so auth bootstrap can find
    the caller's org. This helper uses that exact safe-read shape and returns
    only aggregate facts needed for deletion decisions.
    """

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(PortalUser.org_id, PortalUser.role, PortalOrg.slug)
            .join(PortalOrg, PortalOrg.id == PortalUser.org_id)
            .where(PortalUser.zitadel_user_id == zitadel_user_id)
        )
        rows = result.all()

    remaining = [row for row in rows if excluding_org_id is None or row.org_id != excluding_org_id]
    is_platform_admin = any(row.slug == settings.platform_org_slug and row.role == "admin" for row in rows)
    return UserMembershipSummary(
        total_count=len(rows),
        remaining_count=len(remaining),
        is_platform_admin=is_platform_admin,
    )


async def get_user_global_membership_state(zitadel_user_id: str) -> UserGlobalMembershipState:
    """Return global active/admin membership facts for one Zitadel identity."""

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(PortalUser.org_id, PortalUser.role, PortalUser.status)
            .join(PortalOrg, PortalOrg.id == PortalUser.org_id)
            .where(
                PortalUser.zitadel_user_id == zitadel_user_id,
                PortalOrg.deleted_at.is_(None),
            )
        )
        rows = result.all()

    return UserGlobalMembershipState(
        total_count=len(rows),
        active_count=sum(1 for row in rows if row.status == "active"),
        admin_count=sum(1 for row in rows if row.role == "admin" and row.status != "offboarded"),
    )
