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
from app.services import knowledge_ingest_client


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


async def assert_can_add_item_to_kb(
    kb: PortalKnowledgeBase,
    org: PortalOrg,
) -> None:
    """Raise HTTP 403 when adding an item would exceed the plan's item-per-KB quota.

    Checks:
    - KB is personal (owner_type="user"): apply max_items_per_kb limit.
    - KB is org-scoped (owner_type="org"): no limit enforced (core users cannot
      create org KBs, so only complete-plan users see them — they have None limit).
    - Plan has None limit (complete): skip entirely.

    The current item count is fetched from knowledge-ingest (source of truth for
    items). If the count cannot be fetched (None), we fail open (allow the ingest)
    to avoid blocking uploads due to a transient knowledge-ingest outage.

    R-E2: callers MUST route through this function before triggering any ingest.
    """
    if kb.owner_type != "user":
        # Org-scoped KBs are only accessible to complete-plan users.
        return

    limits = get_plan_limits(org.plan)

    if limits.max_items_per_kb is None:
        # Unlimited plan — no quota check needed.
        return

    current_count = await knowledge_ingest_client.get_source_count(
        org_id=org.zitadel_org_id,
        kb_slug=kb.slug,
    )

    if current_count is None:
        # knowledge-ingest unavailable — fail open to avoid blocking uploads.
        return

    if current_count >= limits.max_items_per_kb:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_code": "kb_quota_items_exceeded",
                "plan": org.plan,
                "limit": limits.max_items_per_kb,
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
