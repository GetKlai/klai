"""App-facing API for Knowledge Bases (any org member, not admin-only)."""

import datetime as dt
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import _get_caller_org, bearer
from app.core.database import get_db
from app.models.connectors import PortalConnector
from app.models.groups import PortalGroup
from app.models.knowledge_bases import PortalGroupKBAccess, PortalKnowledgeBase, PortalUserKBAccess
from app.services import docs_client
from app.services.access import get_user_role_for_kb

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/app", tags=["app-knowledge-bases"])


# -- Pydantic schemas ---------------------------------------------------------


class AppKBCreateRequest(BaseModel):
    name: str
    slug: str
    description: str | None = None
    visibility: str = "internal"
    docs_enabled: bool = True
    owner_type: str = "org"


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
    owner_user_id: str | None


class AppKBsResponse(BaseModel):
    knowledge_bases: list[AppKBOut]


# Members schemas


class UserMemberOut(BaseModel):
    id: int
    user_id: str
    role: str
    granted_at: datetime
    granted_by: str


class GroupMemberOut(BaseModel):
    id: int
    group_id: int
    group_name: str
    role: str
    granted_at: datetime
    granted_by: str


class MembersResponse(BaseModel):
    users: list[UserMemberOut]
    groups: list[GroupMemberOut]


class InviteUserRequest(BaseModel):
    user_id: str
    role: str


class InviteGroupRequest(BaseModel):
    group_id: int
    role: str


class UpdateRoleRequest(BaseModel):
    role: str


# Stats schema


class ConnectorStatusSummary(BaseModel):
    id: str
    name: str
    connector_type: str
    last_sync_status: str | None
    last_sync_at: datetime | None


class KBStatsOut(BaseModel):
    docs_count: int | None
    connector_count: int
    connectors: list[ConnectorStatusSummary]
    volume: int | None
    usage_last_30d: int | None


# -- Helpers ------------------------------------------------------------------


def _kb_out(kb: PortalKnowledgeBase) -> AppKBOut:
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
        owner_user_id=kb.owner_user_id,
    )


async def _get_kb_or_404(kb_slug: str, org_id: int, db: AsyncSession) -> PortalKnowledgeBase:
    result = await db.execute(
        select(PortalKnowledgeBase).where(
            PortalKnowledgeBase.org_id == org_id,
            PortalKnowledgeBase.slug == kb_slug,
        )
    )
    kb = result.scalar_one_or_none()
    if not kb:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found")
    return kb


async def _require_owner(kb: PortalKnowledgeBase, caller_id: str, db: AsyncSession) -> None:
    role = await get_user_role_for_kb(kb.id, caller_id, db)
    if role != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Owner access required",
        )


def _validate_role(role: str) -> None:
    if role not in ("viewer", "contributor", "owner"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Role must be viewer, contributor, or owner",
        )


# -- KB list / get / create ---------------------------------------------------


@router.get("/knowledge-bases", response_model=AppKBsResponse)
async def list_app_knowledge_bases(
    docs_only: bool = False,
    owner_type: str | None = None,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> AppKBsResponse:
    """Return KBs for the caller's org. Optionally filter by docs_only or owner_type."""
    _, org, _ = await _get_caller_org(credentials, db)
    query = select(PortalKnowledgeBase).where(PortalKnowledgeBase.org_id == org.id)
    if docs_only:
        query = query.where(
            PortalKnowledgeBase.docs_enabled == True,  # noqa: E712
            PortalKnowledgeBase.gitea_repo_slug.isnot(None),
        )
    if owner_type:
        query = query.where(PortalKnowledgeBase.owner_type == owner_type)
    result = await db.execute(query.order_by(PortalKnowledgeBase.name))
    kbs = result.scalars().all()
    return AppKBsResponse(knowledge_bases=[_kb_out(kb) for kb in kbs])


@router.get("/knowledge-bases/{kb_slug}", response_model=AppKBOut)
async def get_app_knowledge_base(
    kb_slug: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> AppKBOut:
    """Return a single KB by slug for the caller's org."""
    _, org, _ = await _get_caller_org(credentials, db)
    kb = await _get_kb_or_404(kb_slug, org.id, db)
    return _kb_out(kb)


@router.post("/knowledge-bases", response_model=AppKBOut, status_code=status.HTTP_201_CREATED)
async def create_app_knowledge_base(
    body: AppKBCreateRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> AppKBOut:
    """Create a new KB. The creator is automatically given the owner role."""
    caller_id, org, _ = await _get_caller_org(credentials, db)

    if body.owner_type not in ("org", "user"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="owner_type must be 'org' or 'user'",
        )

    owner_user_id = caller_id if body.owner_type == "user" else None

    kb = PortalKnowledgeBase(
        org_id=org.id,
        name=body.name,
        slug=body.slug,
        description=body.description,
        created_by=caller_id,
        visibility=body.visibility,
        docs_enabled=body.docs_enabled,
        owner_type=body.owner_type,
        owner_user_id=owner_user_id,
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

    # Add creator as owner in portal_user_kb_access
    db.add(
        PortalUserKBAccess(
            kb_id=kb.id,
            user_id=caller_id,
            org_id=org.id,
            role="owner",
            granted_by=caller_id,
        )
    )

    kb.gitea_repo_slug = await docs_client.provision_and_store(org.slug, body.name, body.slug, body.visibility, db)

    await db.commit()
    await db.refresh(kb)
    return _kb_out(kb)


# -- Stats --------------------------------------------------------------------


@router.get("/knowledge-bases/{kb_slug}/stats", response_model=KBStatsOut)
async def get_kb_stats(
    kb_slug: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> KBStatsOut:
    """Return dashboard stats for a KB: connectors, docs count, volume, usage."""
    _, org, _ = await _get_caller_org(credentials, db)
    kb = await _get_kb_or_404(kb_slug, org.id, db)

    # Connectors from portal DB
    conn_result = await db.execute(select(PortalConnector).where(PortalConnector.kb_id == kb.id))
    connectors = conn_result.scalars().all()
    connector_summaries = [
        ConnectorStatusSummary(
            id=str(c.id),
            name=c.name,
            connector_type=c.connector_type,
            last_sync_status=c.last_sync_status,
            last_sync_at=c.last_sync_at,
        )
        for c in connectors
    ]

    # Docs page count via docs service (best-effort)
    docs_count: int | None = None
    if kb.gitea_repo_slug:
        try:
            docs_count = await docs_client.get_page_count(org.slug, kb_slug)
        except Exception:
            log.debug("Could not fetch docs page count for KB %s", kb_slug)

    # Qdrant volume — not yet integrated; placeholder
    volume: int | None = None

    # Audit log query usage (last 30 days) — placeholder until KB query events are logged
    usage_last_30d: int | None = None
    try:
        from app.models.audit import PortalAuditLog

        cutoff = datetime.now(tz=dt.UTC) - timedelta(days=30)
        usage_result = await db.execute(
            select(func.count()).where(
                PortalAuditLog.org_id == org.id,
                PortalAuditLog.resource_type == "kb_query",
                PortalAuditLog.resource_id == kb_slug,
                PortalAuditLog.created_at >= cutoff,
            )
        )
        usage_last_30d = usage_result.scalar_one()
    except Exception:
        log.debug("Could not fetch KB usage stats for %s", kb_slug)

    return KBStatsOut(
        docs_count=docs_count,
        connector_count=len(connectors),
        connectors=connector_summaries,
        volume=volume,
        usage_last_30d=usage_last_30d,
    )


# -- Members: list ------------------------------------------------------------


@router.get("/knowledge-bases/{kb_slug}/members", response_model=MembersResponse)
async def list_members(
    kb_slug: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> MembersResponse:
    """List all members of a KB (user + group access). Readable by any org member."""
    _, org, _ = await _get_caller_org(credentials, db)
    kb = await _get_kb_or_404(kb_slug, org.id, db)

    user_result = await db.execute(select(PortalUserKBAccess).where(PortalUserKBAccess.kb_id == kb.id))
    user_members = [
        UserMemberOut(
            id=r.id,
            user_id=r.user_id,
            role=r.role,
            granted_at=r.granted_at,
            granted_by=r.granted_by,
        )
        for r in user_result.scalars().all()
    ]

    group_result = await db.execute(
        select(PortalGroupKBAccess, PortalGroup.name)
        .join(PortalGroup, PortalGroup.id == PortalGroupKBAccess.group_id)
        .where(PortalGroupKBAccess.kb_id == kb.id)
    )
    group_members = [
        GroupMemberOut(
            id=row.PortalGroupKBAccess.id,
            group_id=row.PortalGroupKBAccess.group_id,
            group_name=row.name,
            role=row.PortalGroupKBAccess.role,
            granted_at=row.PortalGroupKBAccess.granted_at,
            granted_by=row.PortalGroupKBAccess.granted_by,
        )
        for row in group_result.all()
    ]

    return MembersResponse(users=user_members, groups=group_members)


# -- Members: invite user -----------------------------------------------------


@router.post(
    "/knowledge-bases/{kb_slug}/members/users",
    response_model=UserMemberOut,
    status_code=status.HTTP_201_CREATED,
)
async def invite_user(
    kb_slug: str,
    body: InviteUserRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> UserMemberOut:
    """Invite a user to a KB with the given role. Requires owner access."""
    caller_id, org, _ = await _get_caller_org(credentials, db)
    kb = await _get_kb_or_404(kb_slug, org.id, db)
    await _require_owner(kb, caller_id, db)
    _validate_role(body.role)

    if kb.owner_type == "user":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Personal KBs cannot be shared",
        )

    access = PortalUserKBAccess(
        kb_id=kb.id,
        user_id=body.user_id,
        org_id=org.id,
        role=body.role,
        granted_by=caller_id,
    )
    db.add(access)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User already has access to this knowledge base",
        ) from exc
    await db.refresh(access)
    return UserMemberOut(
        id=access.id,
        user_id=access.user_id,
        role=access.role,
        granted_at=access.granted_at,
        granted_by=access.granted_by,
    )


# -- Members: update user role ------------------------------------------------


@router.patch("/knowledge-bases/{kb_slug}/members/users/{access_id}", response_model=UserMemberOut)
async def update_user_role(
    kb_slug: str,
    access_id: int,
    body: UpdateRoleRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> UserMemberOut:
    """Change a user's role on a KB. Requires owner access."""
    caller_id, org, _ = await _get_caller_org(credentials, db)
    kb = await _get_kb_or_404(kb_slug, org.id, db)
    await _require_owner(kb, caller_id, db)
    _validate_role(body.role)

    result = await db.execute(
        select(PortalUserKBAccess).where(
            PortalUserKBAccess.id == access_id,
            PortalUserKBAccess.kb_id == kb.id,
        )
    )
    access = result.scalar_one_or_none()
    if not access:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    access.role = body.role
    await db.commit()
    await db.refresh(access)
    return UserMemberOut(
        id=access.id,
        user_id=access.user_id,
        role=access.role,
        granted_at=access.granted_at,
        granted_by=access.granted_by,
    )


# -- Members: remove user -----------------------------------------------------


@router.delete("/knowledge-bases/{kb_slug}/members/users/{access_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_user(
    kb_slug: str,
    access_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove a user from a KB. Requires owner access."""
    caller_id, org, _ = await _get_caller_org(credentials, db)
    kb = await _get_kb_or_404(kb_slug, org.id, db)
    await _require_owner(kb, caller_id, db)

    result = await db.execute(
        select(PortalUserKBAccess).where(
            PortalUserKBAccess.id == access_id,
            PortalUserKBAccess.kb_id == kb.id,
        )
    )
    access = result.scalar_one_or_none()
    if not access:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    await db.delete(access)
    await db.commit()


# -- Members: invite group ----------------------------------------------------


@router.post(
    "/knowledge-bases/{kb_slug}/members/groups",
    response_model=GroupMemberOut,
    status_code=status.HTTP_201_CREATED,
)
async def invite_group(
    kb_slug: str,
    body: InviteGroupRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> GroupMemberOut:
    """Invite a group to a KB with the given role. Requires owner access."""
    caller_id, org, _ = await _get_caller_org(credentials, db)
    kb = await _get_kb_or_404(kb_slug, org.id, db)
    await _require_owner(kb, caller_id, db)
    _validate_role(body.role)

    if kb.owner_type == "user":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Personal KBs cannot be shared",
        )

    # Verify group exists in org
    group_result = await db.execute(
        select(PortalGroup).where(
            PortalGroup.id == body.group_id,
            PortalGroup.org_id == org.id,
        )
    )
    group = group_result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found in your organisation")

    access = PortalGroupKBAccess(
        group_id=body.group_id,
        kb_id=kb.id,
        role=body.role,
        granted_by=caller_id,
    )
    db.add(access)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Group already has access to this knowledge base",
        ) from exc
    await db.refresh(access)
    return GroupMemberOut(
        id=access.id,
        group_id=access.group_id,
        group_name=group.name,
        role=access.role,
        granted_at=access.granted_at,
        granted_by=access.granted_by,
    )


# -- Members: update group role -----------------------------------------------


@router.patch("/knowledge-bases/{kb_slug}/members/groups/{access_id}", response_model=GroupMemberOut)
async def update_group_role(
    kb_slug: str,
    access_id: int,
    body: UpdateRoleRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> GroupMemberOut:
    """Change a group's role on a KB. Requires owner access."""
    caller_id, org, _ = await _get_caller_org(credentials, db)
    kb = await _get_kb_or_404(kb_slug, org.id, db)
    await _require_owner(kb, caller_id, db)
    _validate_role(body.role)

    result = await db.execute(
        select(PortalGroupKBAccess, PortalGroup.name)
        .join(PortalGroup, PortalGroup.id == PortalGroupKBAccess.group_id)
        .where(
            PortalGroupKBAccess.id == access_id,
            PortalGroupKBAccess.kb_id == kb.id,
        )
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group member not found")

    row.PortalGroupKBAccess.role = body.role
    await db.commit()
    await db.refresh(row.PortalGroupKBAccess)
    return GroupMemberOut(
        id=row.PortalGroupKBAccess.id,
        group_id=row.PortalGroupKBAccess.group_id,
        group_name=row.name,
        role=row.PortalGroupKBAccess.role,
        granted_at=row.PortalGroupKBAccess.granted_at,
        granted_by=row.PortalGroupKBAccess.granted_by,
    )


# -- Members: remove group ----------------------------------------------------


@router.delete("/knowledge-bases/{kb_slug}/members/groups/{access_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_group(
    kb_slug: str,
    access_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove a group from a KB. Requires owner access."""
    caller_id, org, _ = await _get_caller_org(credentials, db)
    kb = await _get_kb_or_404(kb_slug, org.id, db)
    await _require_owner(kb, caller_id, db)

    result = await db.execute(
        select(PortalGroupKBAccess).where(
            PortalGroupKBAccess.id == access_id,
            PortalGroupKBAccess.kb_id == kb.id,
        )
    )
    access = result.scalar_one_or_none()
    if not access:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group member not found")

    await db.delete(access)
    await db.commit()


# -- Docs accessible list (for /app/docs) -------------------------------------


class KBWithAccessOut(BaseModel):
    id: int
    name: str
    slug: str
    visibility: str
    gitea_repo_slug: str | None
    is_accessible: bool


@router.get("/knowledge-bases-with-access", response_model=list[KBWithAccessOut])
async def list_kbs_with_access(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> list[KBWithAccessOut]:
    """Return all docs-enabled KBs for the org, with is_accessible flag per KB.

    Used by /app/docs to show both accessible and locked KB cards.
    """
    from app.services.access import get_accessible_kb_slugs

    caller_id, org, _ = await _get_caller_org(credentials, db)

    # All docs-enabled KBs (org-owned only; personal KBs stay private)
    result = await db.execute(
        select(PortalKnowledgeBase)
        .where(
            PortalKnowledgeBase.org_id == org.id,
            PortalKnowledgeBase.docs_enabled == True,  # noqa: E712
            PortalKnowledgeBase.owner_type == "org",
        )
        .order_by(PortalKnowledgeBase.name)
    )
    all_kbs = result.scalars().all()

    accessible_slugs = set(await get_accessible_kb_slugs(caller_id, db))

    return [
        KBWithAccessOut(
            id=kb.id,
            name=kb.name,
            slug=kb.slug,
            visibility=kb.visibility,
            gitea_repo_slug=kb.gitea_repo_slug,
            is_accessible=kb.slug in accessible_slugs,
        )
        for kb in all_kbs
    ]
