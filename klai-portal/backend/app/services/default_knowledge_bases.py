"""Helpers for creating default knowledge bases (org + personal per user).

Every tenant has one org KB (slug='org') and every user has a personal KB
(slug='personal-{zitadel_user_id}'). Both are created eagerly — the org KB
during tenant provisioning, the personal KB during user signup or invite.

These helpers are idempotent: calling them multiple times for the same
tenant/user is safe (INSERT ... ON CONFLICT DO NOTHING pattern).

The :func:`personal_kb_slug` template is re-exported from
``klai-libs/kb-slugs`` (SPEC-RAG-PERSONAL-SCOPE-001 REQ-1) so retrieval-api
consumes the same helper for server-side scope=personal narrowing without
the slug template living in two places.
"""

import structlog

# Re-export the canonical template so existing call sites
# ``from app.services.default_knowledge_bases import personal_kb_slug``
# continue to resolve. The shared library owns the string.
from klai_kb_slugs import personal_kb_slug
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import set_tenant
from app.models.knowledge_bases import PortalKnowledgeBase

logger = structlog.get_logger()

__all__ = [
    "create_default_org_kb",
    "create_default_personal_kb",
    "ensure_default_knowledge_bases",
    "personal_kb_slug",
    "resolve_org_kb",
    "resolve_personal_kb",
]


async def resolve_personal_kb(caller_id: str, org_id: int, db: AsyncSession) -> PortalKnowledgeBase:
    """Return the caller's personal KB, creating it as fallback if provisioning missed it.

    Used by the magic-slug ``personal`` shortcut in ``get_kb_with_access``
    (SPEC-PORTAL-KB-OWNERSHIP-001 REQ-3.1) and by direct API consumers.
    Idempotent: returns the existing row when present, creates one and
    commits when missing. The row is locked with ``with_for_update`` to
    serialise concurrent first-time fallbacks.
    """
    slug = personal_kb_slug(caller_id)
    result = await db.execute(
        select(PortalKnowledgeBase)
        .where(PortalKnowledgeBase.org_id == org_id, PortalKnowledgeBase.slug == slug)
        .with_for_update()
    )
    kb = result.scalar_one_or_none()
    if kb:
        return kb

    try:
        kb = await create_default_personal_kb(caller_id, org_id, db)
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("fallback_personal_kb_creation_failed", caller_id=caller_id, org_id=org_id)
        raise
    return kb


async def resolve_org_kb(caller_id: str, org_id: int, db: AsyncSession) -> PortalKnowledgeBase:
    """Return the org KB, creating it as fallback if provisioning missed it.

    Magic-slug ``org`` shortcut backing ``get_kb_with_access``. Same
    idempotent + locked pattern as ``resolve_personal_kb``.
    """
    result = await db.execute(
        select(PortalKnowledgeBase)
        .where(PortalKnowledgeBase.org_id == org_id, PortalKnowledgeBase.slug == "org")
        .with_for_update()
    )
    kb = result.scalar_one_or_none()
    if kb:
        return kb

    try:
        kb = await create_default_org_kb(org_id, created_by=caller_id, db=db)
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("fallback_org_kb_creation_failed", org_id=org_id)
        raise
    return kb


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
        kb = result2.scalar_one_or_none()
        if not kb:
            logger.exception("org_kb_lost_after_integrity_error", org_id=org_id)
            raise
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
        kb = result2.scalar_one_or_none()
        if not kb:
            logger.exception("personal_kb_lost_after_integrity_error", org_id=org_id, user_id=user_id)
            raise
    return kb


async def ensure_default_knowledge_bases(
    org_id: int,
    user_id: str,
    db: AsyncSession,
) -> None:
    """Create both default KBs for a new tenant (org KB + admin's personal KB).

    Raises on failure. Callers decide how to handle it — tenant provisioning
    treats it as fatal so a degraded tenant can never be marked 'ready'.

    Requires a pinned DB connection on the session (caller must have awaited
    pin_session() or session.connection()); otherwise set_tenant() below may
    land on a different pooled connection than the subsequent INSERTs and RLS
    will block them.
    """
    # Provisioning runs with the admin's org_id in the session; override it so
    # the RLS USING/WITH CHECK clause (`org_id = current_setting(...)`) accepts
    # inserts for the new tenant.
    await set_tenant(db, org_id)
    await create_default_org_kb(org_id, created_by=user_id, db=db)
    await create_default_personal_kb(user_id, org_id, db=db)
    await db.commit()
    logger.info("default_kbs_created", org_id=org_id, user_id=user_id)
