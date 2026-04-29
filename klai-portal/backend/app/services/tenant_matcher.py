"""
Tenant matcher -- resolve an email address to (zitadel_user_id, org_id).

Uses Zitadel to find the user, then looks up the PortalOrg to get the
integer org_id (FK to portal_orgs.id).

Includes plan check (AC-14a): only plans with the scribe feature are allowed.

Results are cached in-memory with a 60-second TTL (SPEC-SEC-HYGIENE-001
REQ-27 Option A). The previous 5-minute TTL meant a tenant downgrading
from `professional` to `free` could still send invite-bot meeting traffic
for up to 5 minutes after the downgrade — business-logic hygiene fix.
Option A (short TTL) was chosen over Option B (explicit invalidate_cache
hook on the plan-change path) for simplicity; profiling during /run did
not show measurable Zitadel-load increase from the shorter window.
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.portal import PortalOrg
from app.services.zitadel import zitadel

logger = logging.getLogger(__name__)

# SPEC-SEC-HYGIENE-001 REQ-27.1 Option A: 60-second TTL (was 5 minutes).
CACHE_TTL = timedelta(seconds=60)

# Plans that include the scribe (invite-bot) feature (AC-14a)
SCRIBE_PLANS: frozenset[str] = frozenset({"professional", "complete"})

# In-memory cache: email -> (result, expiry)
_cache: dict[str, tuple[tuple[str, int | None] | None, datetime]] = {}


async def find_tenant(email: str) -> tuple[str, int | None] | None:
    """Resolve an email to (zitadel_user_id, portal_org_id).

    Returns None for unknown emails or users on plans without scribe.
    Results are cached for 60 seconds (SPEC-SEC-HYGIENE-001 REQ-27).
    """
    now = datetime.now(UTC)

    if email in _cache:
        result, expires = _cache[email]
        if now < expires:
            return result

    result = await _lookup(email)
    _cache[email] = (result, now + CACHE_TTL)
    return result


async def _lookup(email: str) -> tuple[str, int | None] | None:
    """Look up user in Zitadel and resolve portal org_id.

    Returns None if the user is not found or their org plan
    does not include the scribe feature (AC-14a).
    """
    user_info = await zitadel.find_user_by_email(email)
    if user_info is None:
        logger.info("Ignoring invite from unregistered sender: %s", email)
        return None

    zitadel_user_id, zitadel_org_id = user_info

    # Resolve zitadel_org_id (string) to portal_orgs.id (int) and check plan
    org_id: int | None = None
    try:
        async with AsyncSessionLocal() as db:
            row = await db.execute(
                select(PortalOrg.id, PortalOrg.plan).where(PortalOrg.zitadel_org_id == zitadel_org_id)
            )
            org_row = row.one_or_none()
            if org_row is None:
                logger.info(
                    "No portal org found for zitadel_org_id=%s, email=%s",
                    zitadel_org_id,
                    email,
                )
                return None

            org_id, plan = org_row.id, org_row.plan

            # AC-14a: plan must include scribe feature
            if plan not in SCRIBE_PLANS:
                logger.info(
                    "Plan '%s' does not include scribe for org_id=%s, email=%s",
                    plan,
                    org_id,
                    email,
                )
                return None
    except Exception:
        logger.exception("Failed to resolve portal org for zitadel_org_id=%s", zitadel_org_id)
        return None

    return zitadel_user_id, org_id


def clear_cache() -> None:
    """Clear the tenant cache (useful in tests)."""
    _cache.clear()
