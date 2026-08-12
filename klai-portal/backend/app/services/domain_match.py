"""Domain-match workspace discovery (SPEC-AUTH-010).

Single query helper shared by the signup flows and password login so the
"which workspaces match this email domain?" contract lives in one place.
The idp_callback matrix (SPEC-AUTH-009 R3) predates this helper and keeps
its inline query; both use identical criteria.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from app.models.portal import PortalOrg
from app.services.domain_validation import primary_domain_for_email_domain

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def find_domain_match_orgs(db: AsyncSession, email: str) -> list[PortalOrg]:
    """Return non-deleted orgs whose primary_domain matches the email's domain.

    Free-email domains never match (primary_domain_for_email_domain returns "").
    Multiple orgs may share a primary_domain (SPEC-AUTH-009 C1.4) — callers
    must present a picker, never auto-select.
    """
    email_domain = email.rsplit("@", 1)[-1].strip().lower() if "@" in email else ""
    claimable = primary_domain_for_email_domain(email_domain)
    if not claimable:
        return []

    result = await db.execute(
        select(PortalOrg).where(
            PortalOrg.primary_domain == claimable,
            PortalOrg.deleted_at.is_(None),
        )
    )
    return list(result.scalars().all())
