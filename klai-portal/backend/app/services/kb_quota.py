"""KB quota enforcement service.

SPEC-PORTAL-UNIFY-KB-001 Phase A (D3, R-E1, R-E3, R-X3).

Provides pure-service functions that raise HTTPException 403 on quota violation.
Keeping quota logic here (instead of inline in routes) ensures:
- All create paths hit the same service (R-X3)
- Routes stay thin and readable
- Unit tests are straightforward
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.plan_limits import get_plan_limits
from app.models.knowledge_bases import PortalKnowledgeBase
from app.models.portal import PortalOrg


async def assert_can_create_personal_kb(
    user_id: str,
    org: PortalOrg,
    db: AsyncSession,
) -> None:
    """Raise HTTP 403 when the user has reached their personal KB quota.

    Checks:
    - count(personal KBs owned by user_id) >= max_personal_kbs_per_user

    Skips the DB query entirely when the plan has no limit (None = unlimited).

    R-X3: callers MUST route through this function to guarantee consistent
    quota enforcement across all create paths.
    """
    limits = get_plan_limits(org.plan)

    if limits.max_personal_kbs_per_user is None:
        # Unlimited plan — no quota check needed.
        return

    result = await db.execute(
        select(func.count()).where(
            PortalKnowledgeBase.org_id == org.id,
            PortalKnowledgeBase.owner_type == "user",
            PortalKnowledgeBase.owner_user_id == user_id,
        )
    )
    current_count: int = result.scalar_one()

    if current_count >= limits.max_personal_kbs_per_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_code": "kb_quota_personal_kb_exceeded",
                "plan": org.plan,
                "limit": limits.max_personal_kbs_per_user,
                "current": current_count,
            },
        )


async def assert_can_create_org_kb(
    org: PortalOrg,
    db: AsyncSession,
) -> None:
    """Raise HTTP 403 when the org's plan does not allow org-scoped KBs.

    R-E3: core and professional plans may not create org KBs.
    """
    limits = get_plan_limits(org.plan)

    if not limits.can_create_org_kbs:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_code": "kb_quota_org_kb_not_allowed",
                "plan": org.plan,
            },
        )
