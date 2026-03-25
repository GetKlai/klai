"""App-facing API for Knowledge Bases (any org member, not admin-only)."""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import _get_caller_org, bearer
from app.core.database import get_db
from app.models.knowledge_bases import PortalKnowledgeBase
from app.services import docs_client

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/app", tags=["app-knowledge-bases"])


# -- Pydantic schemas (shared shape with admin KBOut) ------------------------


class AppKBCreateRequest(BaseModel):
    name: str
    slug: str
    description: str | None = None
    visibility: str = "internal"
    docs_enabled: bool = True


class AppKBOut(BaseModel):
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


class AppKBsResponse(BaseModel):
    knowledge_bases: list[AppKBOut]


# -- Endpoints ----------------------------------------------------------------


@router.get("/knowledge-bases", response_model=AppKBsResponse)
async def list_app_knowledge_bases(
    docs_only: bool = False,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> AppKBsResponse:
    """Return all KBs for the caller's org. Any authenticated org member can call this."""
    _, org, _ = await _get_caller_org(credentials, db)
    query = select(PortalKnowledgeBase).where(PortalKnowledgeBase.org_id == org.id)
    if docs_only:
        query = query.where(
            PortalKnowledgeBase.docs_enabled == True,  # noqa: E712
            PortalKnowledgeBase.gitea_repo_slug.isnot(None),
        )
    result = await db.execute(query.order_by(PortalKnowledgeBase.name))
    kbs = result.scalars().all()
    return AppKBsResponse(
        knowledge_bases=[
            AppKBOut(
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


@router.get("/knowledge-bases/{kb_slug}", response_model=AppKBOut)
async def get_app_knowledge_base(
    kb_slug: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> AppKBOut:
    """Return a single KB by slug for the caller's org."""
    _, org, _ = await _get_caller_org(credentials, db)
    result = await db.execute(
        select(PortalKnowledgeBase).where(
            PortalKnowledgeBase.org_id == org.id,
            PortalKnowledgeBase.slug == kb_slug,
        )
    )
    kb = result.scalar_one_or_none()
    if not kb:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found")
    return AppKBOut(
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


@router.post("/knowledge-bases", response_model=AppKBOut, status_code=status.HTTP_201_CREATED)
async def create_app_knowledge_base(
    body: AppKBCreateRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> AppKBOut:
    """Create a new KB. Any org member can create a KB (they become its owner)."""
    caller_id, org, _ = await _get_caller_org(credentials, db)
    kb = PortalKnowledgeBase(
        org_id=org.id,
        name=body.name,
        slug=body.slug,
        description=body.description,
        created_by=caller_id,
        visibility=body.visibility,
        docs_enabled=body.docs_enabled,
        owner_type="org",
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

    kb.gitea_repo_slug = await docs_client.provision_and_store(org.slug, body.name, body.slug, body.visibility, db)

    await db.commit()
    await db.refresh(kb)
    return AppKBOut(
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
