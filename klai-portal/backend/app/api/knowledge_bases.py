"""Admin API for managing Knowledge Bases and their group access."""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.permissions import ProfileRole, UserPermissions, get_caller_at_least
from app.models.groups import PortalGroup
from app.models.knowledge_bases import PortalGroupKBAccess, PortalKnowledgeBase
from app.models.portal import PortalOrg
from app.services import docs_client, knowledge_ingest_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["knowledge-bases"])


# @MX:ANCHOR fan_in=6
async def _get_kb_or_404(kb_id: int, org_id: int, db: AsyncSession) -> PortalKnowledgeBase:
    """Fetch KB by ID, always scoped to org_id. Raises 404 if not found or cross-org."""
    result = await db.execute(
        select(PortalKnowledgeBase).where(
            PortalKnowledgeBase.id == kb_id,
            PortalKnowledgeBase.org_id == org_id,
        )
    )
    kb = result.scalar_one_or_none()
    if not kb:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found")
    return kb


async def _load_org_or_500(db: AsyncSession, org_id: int) -> PortalOrg:
    """Load PortalOrg for endpoints that need zitadel_org_id (not on UserPermissions)."""
    result = await db.execute(select(PortalOrg).where(PortalOrg.id == org_id))
    org = result.scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Organisation not found")
    return org


# -- Pydantic schemas --------------------------------------------------------


class KBCreateRequest(BaseModel):
    name: str
    slug: str
    description: str | None = None
    visibility: str = "internal"
    docs_enabled: bool = True


class KBUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    visibility: str | None = None


class KBOut(BaseModel):
    id: int
    name: str
    slug: str
    description: str | None
    created_at: datetime
    created_by: str
    visibility: str
    docs_enabled: bool
    gitea_repo_slug: str | None
    owner_type: str


class KBsResponse(BaseModel):
    knowledge_bases: list[KBOut]


class KBGroupAccessOut(BaseModel):
    group_id: int
    group_name: str
    granted_at: datetime
    granted_by: str
    role: str


class KBGroupsResponse(BaseModel):
    groups: list[KBGroupAccessOut]


class KBGroupGrantRequest(BaseModel):
    group_id: int
    role: str = "viewer"


class MessageResponse(BaseModel):
    message: str


# -- Knowledge Base CRUD -----------------------------------------------------


@router.get("/knowledge-bases", response_model=KBsResponse)
async def list_knowledge_bases(
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> KBsResponse:
    result = await db.execute(
        select(PortalKnowledgeBase).where(PortalKnowledgeBase.org_id == perms.org_id).order_by(PortalKnowledgeBase.name)
    )
    kbs = result.scalars().all()
    return KBsResponse(
        knowledge_bases=[
            KBOut(
                id=kb.id,
                name=kb.name,
                slug=kb.slug,
                description=kb.description,
                created_at=kb.created_at,
                created_by=kb.created_by,
                visibility=kb.visibility,
                docs_enabled=kb.docs_enabled,
                gitea_repo_slug=kb.gitea_repo_slug,
                owner_type=kb.owner_type,
            )
            for kb in kbs
        ]
    )


@router.post("/knowledge-bases", response_model=KBOut, status_code=status.HTTP_201_CREATED)
async def create_knowledge_base(
    body: KBCreateRequest,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> KBOut:
    kb = PortalKnowledgeBase(
        org_id=perms.org_id,
        name=body.name,
        slug=body.slug,
        description=body.description,
        created_by=perms.user_id,
        visibility=body.visibility,
        docs_enabled=body.docs_enabled,
    )
    db.add(kb)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Slug already exists in this organisation",
        ) from exc

    kb.gitea_repo_slug = await docs_client.provision_and_store(
        perms.org_slug, body.name, body.slug, body.visibility, db
    )

    await db.commit()
    return KBOut(
        id=kb.id,
        name=kb.name,
        slug=kb.slug,
        description=kb.description,
        created_at=kb.created_at,
        created_by=kb.created_by,
        visibility=kb.visibility,
        docs_enabled=kb.docs_enabled,
        gitea_repo_slug=kb.gitea_repo_slug,
        owner_type=kb.owner_type,
    )


@router.patch("/knowledge-bases/{kb_id}", response_model=KBOut)
async def update_knowledge_base(
    kb_id: int,
    body: KBUpdateRequest,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> KBOut:
    kb = await _get_kb_or_404(kb_id, perms.org_id, db)
    if body.name is not None:
        kb.name = body.name
    if body.description is not None:
        kb.description = body.description
    visibility_changed = body.visibility is not None and body.visibility != kb.visibility
    if body.visibility is not None:
        kb.visibility = body.visibility
    await db.commit()
    if visibility_changed:
        org = await _load_org_or_500(db, perms.org_id)
        await knowledge_ingest_client.update_kb_visibility(org.zitadel_org_id, kb.slug, kb.visibility)
    return KBOut(
        id=kb.id,
        name=kb.name,
        slug=kb.slug,
        description=kb.description,
        created_at=kb.created_at,
        created_by=kb.created_by,
        visibility=kb.visibility,
        docs_enabled=kb.docs_enabled,
        gitea_repo_slug=kb.gitea_repo_slug,
        owner_type=kb.owner_type,
    )


@router.delete("/knowledge-bases/{kb_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge_base(
    kb_id: int,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> None:
    kb = await _get_kb_or_404(kb_id, perms.org_id, db)
    if perms.role != ProfileRole.ADMIN and kb.created_by != perms.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the creator or an admin can delete a knowledge base",
        )
    org = await _load_org_or_500(db, perms.org_id)
    # Clean up all ingest data before committing the portal deletion.
    # Raises on failure so the portal record is not orphaned from its ingest data.
    await knowledge_ingest_client.delete_kb(org.zitadel_org_id, kb.slug)
    await db.delete(kb)
    await db.commit()


# -- Group access for Knowledge Bases ----------------------------------------


@router.get("/knowledge-bases/{kb_id}/groups", response_model=KBGroupsResponse)
async def list_kb_groups(
    kb_id: int,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> KBGroupsResponse:
    await _get_kb_or_404(kb_id, perms.org_id, db)
    access_result = await db.execute(
        select(PortalGroupKBAccess, PortalGroup)
        .join(PortalGroup, PortalGroup.id == PortalGroupKBAccess.group_id)
        .where(PortalGroupKBAccess.kb_id == kb_id)
        .order_by(PortalGroup.name)
    )
    rows = access_result.all()
    return KBGroupsResponse(
        groups=[
            KBGroupAccessOut(
                group_id=a.group_id,
                group_name=g.name,
                granted_at=a.granted_at,
                granted_by=a.granted_by,
                role=a.role,
            )
            for a, g in rows
        ]
    )


@router.post("/knowledge-bases/{kb_id}/groups", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
async def grant_kb_group_access(
    kb_id: int,
    body: KBGroupGrantRequest,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    await _get_kb_or_404(kb_id, perms.org_id, db)
    group_result = await db.execute(
        select(PortalGroup).where(
            PortalGroup.id == body.group_id,
            PortalGroup.org_id == perms.org_id,
        )
    )
    if not group_result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    access = PortalGroupKBAccess(group_id=body.group_id, kb_id=kb_id, granted_by=perms.user_id, role=body.role)
    db.add(access)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Group already has access to this knowledge base",
        ) from exc
    await db.commit()
    return MessageResponse(message="Access granted")


@router.delete("/knowledge-bases/{kb_id}/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_kb_group_access(
    kb_id: int,
    group_id: int,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> None:
    await _get_kb_or_404(kb_id, perms.org_id, db)  # Verifies KB belongs to caller's org (IDOR guard)
    result = await db.execute(
        select(PortalGroupKBAccess).where(
            PortalGroupKBAccess.kb_id == kb_id,
            PortalGroupKBAccess.group_id == group_id,
        )
    )
    access = result.scalar_one_or_none()
    if not access:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Access not found")
    await db.delete(access)
    await db.commit()
