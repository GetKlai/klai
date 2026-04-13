"""Helpers for creating default knowledge bases (org + personal per user).

Every tenant has one org KB (slug='org') and every user has a personal KB
(slug='personal-{zitadel_user_id}'). Both are created eagerly — the org KB
during tenant provisioning, the personal KB during user signup or invite.

These helpers are idempotent: calling them multiple times for the same
tenant/user is safe (INSERT ... ON CONFLICT DO NOTHING pattern).
"""

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_bases import PortalKnowledgeBase

logger = structlog.get_logger()


def personal_kb_slug(user_id: str) -> str:
    """Build the canonical personal KB slug for a user."""
    return f"personal-{user_id}"


async def create_default_org_kb(
    org_id: int,
    created_by: str,
    db: AsyncSession,
) -> PortalKnowledgeBase:
    """Create the default org KB for a tenant. Idempotent."""
    result = await db.execute(
        select(PortalKnowledgeBase)
        .where(
            PortalKnowledgeBase.org_id == org_id,
            PortalKnowledgeBase.slug == "org",
        )
        .with_for_update()
    )
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    kb = PortalKnowledgeBase(
        org_id=org_id,
        name="Organisatiekennis",
        slug="org",
        description=None,
        created_by=created_by,
        visibility="internal",
        docs_enabled=False,
        owner_type="org",
        owner_user_id=None,
        default_org_role="viewer",
    )
    db.add(kb)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        result2 = await db.execute(
            select(PortalKnowledgeBase).where(
                PortalKnowledgeBase.org_id == org_id,
                PortalKnowledgeBase.slug == "org",
            )
        )
        kb = result2.scalar_one()
    return kb


async def create_default_personal_kb(
    user_id: str,
    org_id: int,
    db: AsyncSession,
) -> PortalKnowledgeBase:
    """Create the default personal KB for a user. Idempotent."""
    slug = personal_kb_slug(user_id)
    result = await db.execute(
        select(PortalKnowledgeBase)
        .where(
            PortalKnowledgeBase.org_id == org_id,
            PortalKnowledgeBase.slug == slug,
        )
        .with_for_update()
    )
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    kb = PortalKnowledgeBase(
        org_id=org_id,
        name="Persoonlijk",
        slug=slug,
        description=None,
        created_by=user_id,
        visibility="internal",
        docs_enabled=False,
        owner_type="user",
        owner_user_id=user_id,
        default_org_role=None,
    )
    db.add(kb)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        result2 = await db.execute(
            select(PortalKnowledgeBase).where(
                PortalKnowledgeBase.org_id == org_id,
                PortalKnowledgeBase.slug == slug,
            )
        )
        kb = result2.scalar_one()
    return kb


async def ensure_default_knowledge_bases(
    org_id: int,
    user_id: str,
    db: AsyncSession,
) -> None:
    """Create both default KBs for a new tenant (org KB + admin's personal KB).

    Called from tenant provisioning. Non-fatal: logs warning on failure.
    """
    try:
        await create_default_org_kb(org_id, created_by=user_id, db=db)
        await create_default_personal_kb(user_id, org_id, db=db)
        await db.commit()
        logger.info("default_kbs_created", org_id=org_id, user_id=user_id)
    except Exception:
        await db.rollback()
        logger.warning("default_kbs_creation_failed", org_id=org_id, user_id=user_id, exc_info=True)
