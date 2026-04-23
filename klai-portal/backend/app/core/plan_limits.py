"""Per-plan KB quota limits and capability definitions.

SPEC-PORTAL-UNIFY-KB-001 Phase A (D2).

Each plan has a KBLimits entry that governs:
- How many personal KBs a user may create
- How many items (documents) per KB
- Whether the user may create org-scoped KBs
- Which advanced KB capabilities are unlocked

R-O1: get_effective_limits() is a stub for future per-org overrides.
      Current implementation delegates directly to get_plan_limits(org.plan).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class KBLimits:
    """Immutable KB quota and capability descriptor for a subscription plan."""

    max_personal_kbs_per_user: int | None  # None = unlimited
    max_items_per_kb: int | None  # None = unlimited
    can_create_org_kbs: bool
    capabilities: frozenset[str]  # e.g. kb.connectors, kb.members, ...


PLAN_LIMITS: dict[str, KBLimits] = {
    "core": KBLimits(
        max_personal_kbs_per_user=5,
        max_items_per_kb=20,
        can_create_org_kbs=False,
        capabilities=frozenset(),
    ),
    "professional": KBLimits(
        max_personal_kbs_per_user=5,
        max_items_per_kb=20,
        can_create_org_kbs=False,
        capabilities=frozenset(),
    ),
    "complete": KBLimits(
        max_personal_kbs_per_user=None,
        max_items_per_kb=None,
        can_create_org_kbs=True,
        capabilities=frozenset(
            {
                "kb.connectors",
                "kb.members",
                "kb.taxonomy",
                "kb.advanced",
                "kb.gaps",
            }
        ),
    ),
}

# Fallback used for unknown plans: most restrictive tier.
_FALLBACK_LIMITS = PLAN_LIMITS["core"]


def get_plan_limits(plan: str) -> KBLimits:
    """Return KBLimits for the given plan. Falls back to core (most restrictive)."""
    return PLAN_LIMITS.get(plan, _FALLBACK_LIMITS)


async def get_effective_limits(org_id: int, db: AsyncSession) -> KBLimits:
    """Return effective KBLimits for an org.

    R-O1 stub: reads org.plan and delegates to get_plan_limits().
    Future per-org overrides will be applied here when SPEC-PORTAL-GRANDFATHER-001
    is implemented.
    """
    from app.models.portal import PortalOrg

    result = await db.execute(select(PortalOrg).where(PortalOrg.id == org_id))
    org = result.scalar_one_or_none()
    if org is None:
        return _FALLBACK_LIMITS
    return get_plan_limits(org.plan)

