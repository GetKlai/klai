"""
Tenant matcher -- resolve an email address to (zitadel_user_id, org_id).

Uses Zitadel to find the user, then looks up the PortalOrg to get the
integer org_id (FK to portal_orgs.id).

Results are cached in-memory with a 5-minute TTL.
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.portal import PortalOrg
from app.services.zitadel import zitadel

logger = logging.getLogger(__name__)

CACHE_TTL = timedelta(minutes=5)

# In-memory cache: email -> (result, expiry)
_cache: dict[str, tuple[tuple[str, int | None] | None, datetime]] = {}


async def find_tenant(email: str) -> tuple[str, int | None] | None:
    """Resolve an email to (zitadel_user_id, portal_org_id).

    Returns None for unknown emails.
    Results are cached for 5 minutes.
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
    """Look up user in Zitadel and resolve portal org_id."""
    user_info = await zitadel.find_user_by_email(email)
    if user_info is None:
        logger.info("Ignoring invite from unregistered sender: %s", email)
        return None

    zitadel_user_id, zitadel_org_id = user_info

    # Resolve zitadel_org_id (string) to portal_orgs.id (int)
    org_id: int | None = None
    try:
        async with AsyncSessionLocal() as db:
            org = await db.scalar(select(PortalOrg.id).where(PortalOrg.zitadel_org_id == zitadel_org_id))
            org_id = org
    except Exception:
        logger.exception("Failed to resolve portal org for zitadel_org_id=%s", zitadel_org_id)

    return zitadel_user_id, org_id


def clear_cache() -> None:
    """Clear the tenant cache (useful in tests)."""
    _cache.clear()
