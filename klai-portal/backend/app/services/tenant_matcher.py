"""
Tenant matcher -- resolve an email address to (zitadel_user_id, org_id).

Uses Zitadel to find the user, then looks up the PortalOrg to get the
integer org_id (FK to portal_orgs.id).

Includes scribe-add-on check (SPEC-PORTAL-PLAN-RENAME-001): only orgs that
have explicitly enabled the ``scribe`` add-on (via
``portal_orgs.enabled_addons``) are eligible for invite-bot meeting traffic.
This replaces the legacy SCRIBE_PLANS = {"professional", "complete"}
plan-bound check, which is incompatible with the new 2-tier plan model
(chat / knowledge) where scribe is no longer plan-bundled.

Results are cached in-memory with a 60-second TTL (SPEC-SEC-HYGIENE-001
REQ-27 Option A). The previous 5-minute TTL meant a tenant disabling the
scribe add-on could still send invite-bot meeting traffic for up to
5 minutes after the toggle — business-logic hygiene fix. Option A (short
TTL) was chosen over Option B (explicit invalidate_cache hook on the
add-on-toggle path) for simplicity; profiling during /run did not show
measurable Zitadel-load increase from the shorter window.
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

# SPEC-PORTAL-PLAN-RENAME-001: scribe is no longer plan-bundled.
# An org is eligible iff "scribe" is in portal_orgs.enabled_addons.
SCRIBE_ADDON: str = "scribe"

# In-memory cache: email -> (result, expiry)
_cache: dict[str, tuple[tuple[str, int | None] | None, datetime]] = {}


async def find_tenant(email: str) -> tuple[str, int | None] | None:
    """Resolve an email to (zitadel_user_id, portal_org_id).

    Returns None for unknown emails or orgs without the scribe add-on
    enabled. Results are cached for 60 seconds (SPEC-SEC-HYGIENE-001
    REQ-27).
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

    Returns None if the user is not found or their org has not enabled
    the scribe add-on (SPEC-PORTAL-PLAN-RENAME-001).
    """
    user_info = await zitadel.find_user_by_email(email)
    if user_info is None:
        logger.info("Ignoring invite from unregistered sender: %s", email)
        return None

    zitadel_user_id, zitadel_org_id = user_info

    # Resolve zitadel_org_id (string) to portal_orgs.id (int) and check add-on
    org_id: int | None = None
    try:
        async with AsyncSessionLocal() as db:
            row = await db.execute(
                select(PortalOrg.id, PortalOrg.enabled_addons).where(PortalOrg.zitadel_org_id == zitadel_org_id)
            )
            org_row = row.one_or_none()
            if org_row is None:
                logger.info(
                    "No portal org found for zitadel_org_id=%s, email=%s",
                    zitadel_org_id,
                    email,
                )
                return None

            org_id, enabled_addons = org_row.id, list(org_row.enabled_addons or [])

            # SPEC-PORTAL-PLAN-RENAME-001: scribe is now an opt-in add-on.
            if SCRIBE_ADDON not in enabled_addons:
                logger.info(
                    "Scribe add-on not enabled for org_id=%s, email=%s",
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
