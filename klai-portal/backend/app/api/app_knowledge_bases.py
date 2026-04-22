"""App-facing API for Knowledge Bases (any org member, not admin-only)."""

import asyncio
import datetime as dt
from datetime import datetime, timedelta
from typing import Literal

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import _get_caller_org, bearer
from app.core.config import settings
from app.core.database import get_db
from app.models.audit import PortalAuditLog
from app.models.connectors import PortalConnector
from app.models.groups import PortalGroup
from app.models.knowledge_bases import PortalGroupKBAccess, PortalKnowledgeBase, PortalUserKBAccess
from app.models.portal import PortalUser
from app.models.retrieval_gaps import PortalRetrievalGap
from app.services import docs_client, knowledge_ingest_client
from app.services.access import get_user_role_for_kb
from app.services.zitadel import zitadel

logger = structlog.get_logger()
_QDRANT_COLLECTION = "klai_knowledge"


async def _get_non_system_group_or_404(group_id: int, org_id: int, db: AsyncSession) -> PortalGroup:
    """Fetch a non-system group within the org, or 404."""
    result = await db.execute(
        select(PortalGroup).where(
            PortalGroup.id == group_id,
            PortalGroup.org_id == org_id,
            PortalGroup.is_system == False,  # noqa: E712
        )
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found in your organisation",
        )
    return group


async def _qdrant_count_for_kb(zitadel_org_id: str, kb_slug: str) -> int | None:
    """Count Qdrant vectors for a specific org + kb_slug. Returns None on failure."""
    try:
        headers = {"api-key": settings.qdrant_api_key} if settings.qdrant_api_key else {}
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{settings.qdrant_url}/collections/{_QDRANT_COLLECTION}/points/count",
                headers=headers,
                json={
                    "filter": {
                        "must": [
                            {"key": "org_id", "match": {"value": zitadel_org_id}},
                            {"key": "kb_slug", "match": {"value": kb_slug}},
                        ]
                    },
                    "exact": True,
                },
            )
        if resp.status_code == 404:
            return 0
        if not resp.is_success:
            logger.debug("Qdrant count failed for KB %s: HTTP %s", kb_slug, resp.status_code)
            return None
        return resp.json().get("result", {}).get("count", 0) or 0
    except Exception as exc:
        logger.debug("Could not reach Qdrant for KB %s: %s", kb_slug, exc)
        return None


router = APIRouter(prefix="/api/app", tags=["app-knowledge-bases"])


# -- Pydantic schemas ---------------------------------------------------------


class InitialMember(BaseModel):
    type: Literal["user", "group"]
    id: str  # user_id (str) or group_id (str, will be converted to int)
    role: str


class AppKBCreateRequest(BaseModel):
    name: str
    slug: str
    description: str | None = None
    visibility: str = "internal"
    docs_enabled: bool = True
    owner_type: str = "org"
    default_org_role: str | None = "viewer"
    initial_members: list[InitialMember] | None = None


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
    default_org_role: str | None = None


class AppKBsResponse(BaseModel):
    knowledge_bases: list[AppKBOut]


class KBStatsSummary(BaseModel):
    """Cheap, aggregate per-KB stats used to enrich the KB list view.

    Kept intentionally small so the bulk endpoint stays fast. Expensive
    stats (docs count, graph entity count, Neo4j) still live on the
    per-KB detail endpoint.
    """

    items: int  # vector chunks in Qdrant
    connectors: int  # portal_connectors rows for this KB
    gaps_7d: int  # open retrieval gaps pointing at this KB (7 days)
    usage_30d: int  # kb_query audit events for this KB (30 days)


class KBStatsSummaryResponse(BaseModel):
    # Keyed by kb_slug — matches the slug the frontend already has in hand.
    stats: dict[str, KBStatsSummary]


# Members schemas


class UserMemberOut(BaseModel):
    id: int
    user_id: str
    display_name: str | None = None
    email: str | None = None
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
    email: str
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
    org_gap_count_7d: int | None = None
    # Volume breakdown
    source_page_count: int | None = None  # docs pages in PostgreSQL (= docs_count alias)
    vector_chunk_count: int | None = None  # Qdrant vectors (= volume alias)
    graph_entity_count: int | None = None  # FalkorDB entity nodes
    graph_edge_count: int | None = None  # FalkorDB relationship edges


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
        default_org_role=kb.default_org_role,
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


async def _resolve_personal_kb(caller_id: str, org_id: int, db: AsyncSession) -> PortalKnowledgeBase:
    """Return the caller's personal KB, creating it as fallback if provisioning missed it."""
    from app.services.default_knowledge_bases import create_default_personal_kb, personal_kb_slug

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


async def _resolve_org_kb(caller_id: str, org_id: int, db: AsyncSession) -> PortalKnowledgeBase:
    """Return the org KB, creating it as fallback if provisioning missed it."""
    from app.services.default_knowledge_bases import create_default_org_kb

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


async def _require_owner(kb: PortalKnowledgeBase, caller_id: str, db: AsyncSession) -> None:
    role = await get_user_role_for_kb(
        kb.id, caller_id, db, default_org_role=kb.default_org_role, kb_org_id=kb.org_id, kb_created_by=kb.created_by
    )
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
    """Return KBs visible to the caller: all org-owned KBs + caller's own personal KBs.

    Other users' personal KBs are never returned.
    """
    caller_id, org, _ = await _get_caller_org(credentials, db)
    query = select(PortalKnowledgeBase).where(
        PortalKnowledgeBase.org_id == org.id,
        # Org-owned KBs are visible to everyone; personal KBs only to their owner
        (PortalKnowledgeBase.owner_type == "org") | (PortalKnowledgeBase.owner_user_id == caller_id),
    )
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


@router.get("/knowledge-bases/stats-summary", response_model=KBStatsSummaryResponse)
async def knowledge_bases_stats_summary(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> KBStatsSummaryResponse:
    """Return cheap aggregate stats per KB for the caller's org.

    Used to enrich the knowledge base list view with item counts,
    connector counts, gap counts, and recent usage. Expensive stats
    (docs count, Neo4j) stay on the per-KB detail endpoint.

    Scope: all org-owned KBs plus the caller's own personal KBs — the
    same set the app-facing list endpoint returns by default.
    """
    zitadel_user_id, org, _ = await _get_caller_org(credentials, db)

    # Fetch all KBs visible to this caller (org-owned + caller's personal KBs).
    kbs_result = await db.execute(
        select(PortalKnowledgeBase).where(
            PortalKnowledgeBase.org_id == org.id,
            (PortalKnowledgeBase.owner_type == "org") | (PortalKnowledgeBase.owner_user_id == zitadel_user_id),
        )
    )
    kbs = kbs_result.scalars().all()
    if not kbs:
        return KBStatsSummaryResponse(stats={})

    kb_ids = [kb.id for kb in kbs]
    kb_slugs = [kb.slug for kb in kbs]
    slug_by_id = {kb.id: kb.slug for kb in kbs}

    gap_cutoff = datetime.now(tz=dt.UTC) - timedelta(days=7)
    usage_cutoff = datetime.now(tz=dt.UTC) - timedelta(days=30)

    # Connectors per KB (org-scoped, grouped).
    connectors_result = await db.execute(
        select(PortalConnector.kb_id, func.count(PortalConnector.id))
        .where(
            PortalConnector.org_id == org.id,
            PortalConnector.kb_id.in_(kb_ids),
        )
        .group_by(PortalConnector.kb_id)
    )
    connectors_by_slug: dict[str, int] = {}
    for kb_id, count in connectors_result.all():
        slug = slug_by_id.get(kb_id)
        if slug is not None:
            connectors_by_slug[slug] = count

    # Open gaps per KB in the last 7 days (best-effort via nearest_kb_slug).
    gaps_result = await db.execute(
        select(PortalRetrievalGap.nearest_kb_slug, func.count(PortalRetrievalGap.id))
        .where(
            PortalRetrievalGap.org_id == org.id,
            PortalRetrievalGap.nearest_kb_slug.in_(kb_slugs),
            PortalRetrievalGap.resolved_at.is_(None),
            PortalRetrievalGap.occurred_at >= gap_cutoff,
        )
        .group_by(PortalRetrievalGap.nearest_kb_slug)
    )
    gaps_by_slug: dict[str, int] = {slug: count for slug, count in gaps_result.all()}

    # Usage (kb_query audit events) per KB in the last 30 days.
    usage_result = await db.execute(
        select(PortalAuditLog.resource_id, func.count(PortalAuditLog.id))
        .where(
            PortalAuditLog.org_id == org.id,
            PortalAuditLog.resource_type == "kb_query",
            PortalAuditLog.resource_id.in_(kb_slugs),
            PortalAuditLog.created_at >= usage_cutoff,
        )
        .group_by(PortalAuditLog.resource_id)
    )
    usage_by_slug: dict[str, int] = {slug: count for slug, count in usage_result.all()}

    # Qdrant item counts — N parallel calls, one per KB. Each call is
    # a single filtered count query against the shared collection.
    item_counts = await asyncio.gather(
        *(_qdrant_count_for_kb(org.zitadel_org_id, kb.slug) for kb in kbs),
        return_exceptions=False,
    )
    items_by_slug: dict[str, int] = {kb.slug: (count or 0) for kb, count in zip(kbs, item_counts, strict=True)}

    stats: dict[str, KBStatsSummary] = {
        kb.slug: KBStatsSummary(
            items=items_by_slug.get(kb.slug, 0),
            connectors=connectors_by_slug.get(kb.slug, 0),
            gaps_7d=gaps_by_slug.get(kb.slug, 0),
            usage_30d=usage_by_slug.get(kb.slug, 0),
        )
        for kb in kbs
    }
    return KBStatsSummaryResponse(stats=stats)


@router.get("/knowledge-bases/{kb_slug}", response_model=AppKBOut)
async def get_app_knowledge_base(
    kb_slug: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> AppKBOut:
    """Return a single KB by slug for the caller's org.

    Magic slugs:
    - 'personal' resolves to the caller's personal-{user_id} KB
    - 'org' resolves to the org-wide KB
    Both are created as fallback if provisioning missed them.
    """
    caller_id, org, _ = await _get_caller_org(credentials, db)
    if kb_slug == "personal":
        kb = await _resolve_personal_kb(caller_id, org.id, db)
    elif kb_slug == "org":
        kb = await _resolve_org_kb(caller_id, org.id, db)
    else:
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

    if body.default_org_role is not None and body.default_org_role not in ("viewer", "contributor"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="default_org_role must be 'viewer', 'contributor', or null",
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
        default_org_role=body.default_org_role,
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

    # Add initial members (from sharing wizard)
    if body.initial_members:
        for member in body.initial_members:
            _validate_role(member.role)
            if member.type == "user":
                db.add(
                    PortalUserKBAccess(
                        kb_id=kb.id,
                        user_id=member.id,
                        org_id=org.id,
                        role=member.role,
                        granted_by=caller_id,
                    )
                )
            elif member.type == "group":
                db.add(
                    PortalGroupKBAccess(
                        group_id=int(member.id),
                        kb_id=kb.id,
                        role=member.role,
                        granted_by=caller_id,
                    )
                )

    kb.gitea_repo_slug = await docs_client.provision_and_store(org.slug, body.name, body.slug, body.visibility, db)

    await db.commit()

    # Sync initial visibility to knowledge-ingest so new chunks get the correct field.
    # Uses the Zitadel org_id (org.zitadel_org_id) as the tenant key in Qdrant.
    await knowledge_ingest_client.update_kb_visibility(org.zitadel_org_id, body.slug, body.visibility)

    return _kb_out(kb)


@router.delete("/knowledge-bases/{kb_slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_app_knowledge_base(
    kb_slug: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a KB and all associated data. Requires owner access.

    Deletion order:
    1. docs-app (only if gitea_repo_slug or docs_enabled) — Qdrant vectors, Gitea, docs DB row.
    2. knowledge-ingest (always) — FalkorDB graph nodes, Qdrant chunks, PG artifacts.
    3. Portal DB — KB row + cascaded access rows.

    Both step 1 and 2 raise on failure, aborting before the portal record is deleted.
    """
    caller_id, org, _ = await _get_caller_org(credentials, db)
    kb = await _get_kb_or_404(kb_slug, org.id, db)
    await _require_owner(kb, caller_id, db)

    # Step 1: Clean up docs-app (Qdrant vectors managed by docs, Gitea webhook/repo, docs DB row).
    if kb.gitea_repo_slug or kb.docs_enabled:
        await docs_client.deprovision_kb(org.slug, kb.slug)

    # Step 2: Clean up knowledge-ingest data (FalkorDB graph nodes, Qdrant chunks, PG artifacts).
    # Always called, regardless of docs/gitea state — connector-based KBs never have gitea_repo_slug.
    await knowledge_ingest_client.delete_kb(org.zitadel_org_id, kb.slug)

    # Step 3: Portal DB -- delete KB row (cascades access rows).
    # No tombstone: slug is free to reuse after a full delete (all data wiped).
    await db.delete(kb)
    await db.commit()


# -- Default org role ---------------------------------------------------------


class UpdateDefaultOrgRoleRequest(BaseModel):
    default_org_role: str | None  # "viewer", "contributor", or null


@router.put("/knowledge-bases/{kb_slug}/default-org-role", response_model=AppKBOut)
async def update_default_org_role(
    kb_slug: str,
    body: UpdateDefaultOrgRoleRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> AppKBOut:
    """Update the default org role for a KB. Requires owner access."""
    caller_id, org, _ = await _get_caller_org(credentials, db)
    kb = await _get_kb_or_404(kb_slug, org.id, db)
    await _require_owner(kb, caller_id, db)

    if body.default_org_role is not None and body.default_org_role not in ("viewer", "contributor"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="default_org_role must be 'viewer', 'contributor', or null",
        )

    kb.default_org_role = body.default_org_role
    await db.commit()
    # No post-commit refresh: RLS tenant context is transaction-scoped (see SPEC-SEC-021 post-mortem).
    return _kb_out(kb)


# -- Owner update (name, description, visibility) ---------------------------


class AppKBUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    visibility: str | None = None
    default_org_role: str | None = None


@router.patch("/knowledge-bases/{kb_slug}", response_model=AppKBOut)
async def update_knowledge_base(
    kb_slug: str,
    body: AppKBUpdateRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> AppKBOut:
    """Update KB properties. Requires owner access."""
    caller_id, org, _ = await _get_caller_org(credentials, db)
    kb = await _get_kb_or_404(kb_slug, org.id, db)
    await _require_owner(kb, caller_id, db)

    if body.name is not None:
        kb.name = body.name
    if body.description is not None:
        kb.description = body.description

    visibility_changed = body.visibility is not None and body.visibility != kb.visibility
    if body.visibility is not None:
        if body.visibility not in ("public", "internal"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="visibility must be 'public' or 'internal'",
            )
        kb.visibility = body.visibility

    if body.default_org_role is not None:
        if body.default_org_role == "":
            kb.default_org_role = None
        elif body.default_org_role in ("viewer", "contributor"):
            kb.default_org_role = body.default_org_role
        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="default_org_role must be 'viewer', 'contributor', or empty",
            )

    # expire_on_commit=False keeps all attributes valid after commit — no re-fetch needed.
    # A re-fetch after commit acquires a new connection without app.current_org_id set,
    # causing RLS to return no rows and a spurious 404.
    #
    # Visibility is a two-system invariant: portal_knowledge_bases.visibility
    # must match the retrieval-side flag in knowledge-ingest. Propagate to
    # knowledge-ingest FIRST, then commit portal — so a propagation failure
    # leaves both systems in the old, consistent state instead of split-brain.
    if visibility_changed:
        try:
            await knowledge_ingest_client.update_kb_visibility(org.zitadel_org_id, kb.slug, kb.visibility)
        except Exception as exc:
            # Revert the in-memory change; portal hasn't been committed yet.
            await db.rollback()
            logger.exception(
                "kb_visibility_propagation_failed",
                kb_slug=kb.slug,
                org_id=org.id,
                requested_visibility=kb.visibility,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "Could not propagate visibility change to knowledge-ingest; no changes were saved. Please retry."
                ),
            ) from exc

    await db.commit()
    return _kb_out(kb)


# -- Stats --------------------------------------------------------------------


@router.get("/knowledge-bases/{kb_slug}/stats", response_model=KBStatsOut)
async def get_kb_stats(
    kb_slug: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> KBStatsOut:
    """Return dashboard stats for a KB: connectors, docs count, volume, usage."""
    caller_id, org, _ = await _get_caller_org(credentials, db)
    if kb_slug == "personal":
        kb = await _resolve_personal_kb(caller_id, org.id, db)
    elif kb_slug == "org":
        kb = await _resolve_org_kb(caller_id, org.id, db)
    else:
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
            logger.debug("Could not fetch docs page count for KB %s", kb_slug)

    # Qdrant vector count for this KB
    volume = await _qdrant_count_for_kb(org.zitadel_org_id, kb.slug)

    # Source artifact count from knowledge-ingest (PostgreSQL)
    source_count = await knowledge_ingest_client.get_source_count(org.zitadel_org_id, kb.slug)

    # FalkorDB graph stats (entity/edge counts for the org)
    graph_stats = await knowledge_ingest_client.get_graph_stats(org.zitadel_org_id)
    graph_entity_count: int | None = graph_stats.get("entity_count")
    graph_edge_count: int | None = graph_stats.get("edge_count")

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
        logger.debug("Could not fetch KB usage stats for %s", kb_slug)

    # KB-scoped gap count (7 days) — filtered by nearest_kb_slug
    org_gap_count_7d: int | None = None
    try:
        from app.models.retrieval_gaps import PortalRetrievalGap

        gap_cutoff = datetime.now(tz=dt.UTC) - timedelta(days=7)
        gap_result = await db.execute(
            select(func.count()).where(
                PortalRetrievalGap.org_id == org.id,
                PortalRetrievalGap.nearest_kb_slug == kb.slug,
                PortalRetrievalGap.occurred_at >= gap_cutoff,
                PortalRetrievalGap.resolved_at.is_(None),
            )
        )
        org_gap_count_7d = gap_result.scalar_one()
    except Exception:
        logger.debug("Could not fetch gap count for KB stats")

    return KBStatsOut(
        docs_count=docs_count,
        connector_count=len(connectors),
        connectors=connector_summaries,
        volume=volume,
        usage_last_30d=usage_last_30d,
        org_gap_count_7d=org_gap_count_7d,
        source_page_count=source_count,
        vector_chunk_count=volume,
        graph_entity_count=graph_entity_count,
        graph_edge_count=graph_edge_count,
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

    user_result = await db.execute(
        select(PortalUserKBAccess, PortalUser.display_name, PortalUser.email)
        .outerjoin(PortalUser, PortalUser.zitadel_user_id == PortalUserKBAccess.user_id)
        .where(PortalUserKBAccess.kb_id == kb.id)
    )
    user_members = [
        UserMemberOut(
            id=row.PortalUserKBAccess.id,
            user_id=row.PortalUserKBAccess.user_id,
            display_name=row.display_name,
            email=row.email,
            role=row.PortalUserKBAccess.role,
            granted_at=row.PortalUserKBAccess.granted_at,
            granted_by=row.PortalUserKBAccess.granted_by,
        )
        for row in user_result.all()
    ]

    group_result = await db.execute(
        select(PortalGroupKBAccess, PortalGroup.name)
        .join(PortalGroup, PortalGroup.id == PortalGroupKBAccess.group_id)
        .where(PortalGroupKBAccess.kb_id == kb.id)
        .where(PortalGroup.is_system == False)  # noqa: E712
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

    # Resolve email → Zitadel user_id
    resolved_user_id = await zitadel.find_user_id_by_email(body.email)
    if not resolved_user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No user found with that email address",
        )

    # Cache display info in portal_users if they have a row (org member)
    user_row = await db.execute(select(PortalUser).where(PortalUser.zitadel_user_id == resolved_user_id))
    portal_user = user_row.scalar_one_or_none()
    if portal_user and portal_user.email != body.email:
        portal_user.email = body.email

    access = PortalUserKBAccess(
        kb_id=kb.id,
        user_id=resolved_user_id,
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
    # No post-commit refresh: RLS tenant context is transaction-scoped (see SPEC-SEC-021 post-mortem).
    return UserMemberOut(
        id=access.id,
        user_id=access.user_id,
        display_name=portal_user.display_name if portal_user else None,
        email=body.email,
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
    # No post-commit refresh: RLS tenant context is transaction-scoped (see SPEC-SEC-021 post-mortem).

    profile = await db.execute(select(PortalUser).where(PortalUser.zitadel_user_id == access.user_id))
    portal_user = profile.scalar_one_or_none()
    return UserMemberOut(
        id=access.id,
        user_id=access.user_id,
        display_name=portal_user.display_name if portal_user else None,
        email=portal_user.email if portal_user else None,
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

    # Verify group exists in org and is not a system group
    group = await _get_non_system_group_or_404(body.group_id, org.id, db)

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
    # No post-commit refresh: RLS tenant context is transaction-scoped (see SPEC-SEC-021 post-mortem).
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
    # No post-commit refresh: RLS tenant context is transaction-scoped (see SPEC-SEC-021 post-mortem).
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


# -- Crawler preview ----------------------------------------------------------


class CrawlPreviewRequest(BaseModel):
    url: str
    content_selector: str | None = None
    try_ai: bool = False
    cookies: list[dict] | None = None


class CrawlPreviewResponse(BaseModel):
    url: str
    fit_markdown: str
    word_count: int
    warnings: list[str] = []
    content_selector: str | None = None
    selector_source: str | None = None


@router.post("/knowledge-bases/{kb_slug}/connectors/crawl-preview", response_model=CrawlPreviewResponse)
async def crawl_preview(
    kb_slug: str,
    body: CrawlPreviewRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> CrawlPreviewResponse:
    """Preview KB content for a URL using PruningContentFilter. Requires owner role."""
    caller_id, org, _ = await _get_caller_org(credentials, db)
    kb = await _get_kb_or_404(kb_slug, org.id, db)
    await _require_owner(kb, caller_id, db)
    result = await knowledge_ingest_client.preview_crawl(
        url=body.url,
        content_selector=body.content_selector,
        org_id=str(org.id),
        try_ai=body.try_ai,
        cookies=body.cookies,
    )
    return CrawlPreviewResponse(
        url=result.get("url", body.url),
        fit_markdown=result.get("fit_markdown", ""),
        word_count=result.get("word_count", 0),
        warnings=result.get("warnings", []),
        content_selector=result.get("content_selector"),
        selector_source=result.get("selector_source"),
    )


# ---------------------------------------------------------------------------
# App-level member picker endpoints (any org member, no admin required)
# ---------------------------------------------------------------------------


class AppGroupItem(BaseModel):
    id: int
    name: str


class AppGroupsResponse(BaseModel):
    groups: list[AppGroupItem]


class AppUserItem(BaseModel):
    zitadel_user_id: str
    email: str
    display_name: str


class AppUsersResponse(BaseModel):
    users: list[AppUserItem]


@router.get("/groups", response_model=AppGroupsResponse)
async def list_groups_for_picker(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> AppGroupsResponse:
    """Lightweight group list for the member picker. Any org member can access."""
    _, org, _ = await _get_caller_org(credentials, db)
    result = await db.execute(
        select(PortalGroup.id, PortalGroup.name)
        .where(PortalGroup.org_id == org.id)
        .where(PortalGroup.is_system == False)  # noqa: E712
        .order_by(PortalGroup.name)
    )
    return AppGroupsResponse(groups=[AppGroupItem(id=row.id, name=row.name) for row in result.all()])


@router.get("/users", response_model=AppUsersResponse)
async def list_users_for_picker(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> AppUsersResponse:
    """Lightweight user list for the member picker. Any org member can access."""
    _, org, _ = await _get_caller_org(credentials, db)

    result = await db.execute(select(PortalUser).where(PortalUser.org_id == org.id).order_by(PortalUser.created_at))
    portal_users = {u.zitadel_user_id: u for u in result.scalars().all()}

    if not portal_users:
        return AppUsersResponse(users=[])

    zitadel_users = await zitadel.list_org_users(settings.zitadel_portal_org_id)

    users_out: list[AppUserItem] = []
    for z in zitadel_users:
        uid = z.get("id", "")
        if uid not in portal_users:
            continue
        profile = z.get("human", {}).get("profile", {})
        email_obj = z.get("human", {}).get("email", {})
        first = profile.get("firstName", "")
        last = profile.get("lastName", "")
        users_out.append(
            AppUserItem(
                zitadel_user_id=uid,
                email=email_obj.get("email", ""),
                display_name=f"{first} {last}".strip() or email_obj.get("email", uid),
            )
        )

    return AppUsersResponse(users=users_out)
