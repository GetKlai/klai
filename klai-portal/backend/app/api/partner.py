"""Partner API router.

SPEC-API-001: External partner endpoints under /partner/v1/*.
Authenticated via partner API keys (Bearer pk_live_...).
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.partner_dependencies import (
    PartnerAuthContext,
    get_partner_key,
    require_permission,
)
from app.core.database import get_db
from app.models.knowledge_bases import PortalKnowledgeBase

router = APIRouter(prefix="/partner/v1", tags=["Partner API"])


@router.get("/knowledge-bases")
async def list_knowledge_bases(
    auth: PartnerAuthContext = Depends(get_partner_key),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """List knowledge bases the partner key has access to.

    REQ-4.1: Requires chat OR knowledge_append permission.
    Returns id, name, slug, access_level for each accessible KB.
    """
    # Permission: chat OR knowledge_append
    if not auth.permissions.get("chat") and not auth.permissions.get("knowledge_append"):
        require_permission(auth, "chat")  # will raise 403

    if not auth.kb_access:
        return []

    kb_ids = list(auth.kb_access.keys())

    result = await db.execute(
        select(PortalKnowledgeBase).where(
            PortalKnowledgeBase.id.in_(kb_ids),
            PortalKnowledgeBase.org_id == auth.org_id,
        )
    )
    kbs = result.scalars().all()

    return [
        {
            "id": kb.id,
            "name": kb.name,
            "slug": kb.slug,
            "access_level": auth.kb_access[kb.id],
        }
        for kb in kbs
        if kb.id in auth.kb_access
    ]
