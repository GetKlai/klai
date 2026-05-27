"""App-facing API for Knowledge Bases (any org member, not admin-only)."""

import asyncio
import datetime as dt
import json
from datetime import datetime, timedelta
from typing import Literal

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import _load_org_or_500, get_kb_with_access, require_capability
from app.core.config import settings
from app.core.database import get_db
from app.core.permissions import UserPermissions, get_caller
from app.core.profiles import Capability, ProfileRole
from app.models.connectors import PortalConnector
from app.models.groups import PortalGroup
from app.models.kb_uploads import KBUpload
from app.models.knowledge_bases import PortalGroupKBAccess, PortalKnowledgeBase, PortalUserKBAccess
from app.models.portal import PortalUser
from app.models.retrieval_gaps import PortalRetrievalGap
from app.services import docs_client, knowledge_ingest_client
from app.services.access import get_user_role_for_kb, is_personal_kb
from app.services.audit import log_event
from app.services.connector_credentials import credential_store
from app.services.kb_quota import assert_can_create_org_kb, assert_can_create_personal_kb
from app.services.zitadel import zitadel

# SPEC-PORTAL-KB-OWNERSHIP-001 REQ-1.1 — header-based admin-override token.
# Header value mirrors the I-CONFIRM-REMOVAL precedent in
# klai-infra/sync-env.yml: a typed string forces explicit operator intent
# rather than a click-through boolean. The dual-confirmation modal in the
# frontend is what gives the operator the chance to abort.
ADMIN_OVERRIDE_HEADER = "X-Admin-Override-Confirm"
ADMIN_OVERRIDE_VALUE = "I-WAS-NOT-CREATOR"

logger = structlog.get_logger()
_QDRANT_COLLECTION = "klai_knowledge"
_VISIBLE_UPLOAD_STATUSES: tuple[str, ...] = ("processing", "ingesting", "failed")


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
            logger.warning(
                "kb_stats_qdrant_http_error",
                kb_slug=kb_slug,
                status=resp.status_code,
            )
            return None
        return resp.json().get("result", {}).get("count", 0) or 0
    except Exception:
        logger.warning(
            "kb_stats_qdrant_unreachable",
            kb_slug=kb_slug,
            exc_info=True,
        )
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
    chunks: int = 0  # SPEC-PORTAL-KENNIS-001: parent_chunks count from knowledge schema
    gaps_7d: int  # open retrieval gaps pointing at this KB (7 days)
    usage_30d: int  # knowledge.queried events for this KB (30 days)
    unique_users_30d: int  # distinct users that queried this KB (30 days)
    active_days_30d: int  # distinct days with at least one query (30 days, max 30)
    sources: int = 0  # SPEC-PORTAL-KENNIS-001: total sources count (connectors + direct uploads)


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
    unique_users_30d: int | None = None
    active_days_30d: int | None = None
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
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> AppKBsResponse:
    """Return KBs visible to the caller: all org-owned KBs + caller's own personal KBs.

    Other users' personal KBs are never returned.
    """
    query = select(PortalKnowledgeBase).where(
        PortalKnowledgeBase.org_id == perms.org_id,
        # Org-owned KBs are visible to everyone; personal KBs only to their owner
        (PortalKnowledgeBase.owner_type == "org") | (PortalKnowledgeBase.owner_user_id == perms.user_id),
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
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> KBStatsSummaryResponse:
    """Return cheap aggregate stats per KB for the caller's org.

    Used to enrich the knowledge base list view with item counts,
    connector counts, gap counts, and recent usage. Expensive stats
    (docs count, Neo4j) stay on the per-KB detail endpoint.

    Scope: all org-owned KBs plus the caller's own personal KBs — the
    same set the app-facing list endpoint returns by default.
    """
    org = await _load_org_or_500(db, perms.org_id)

    # Fetch all KBs visible to this caller (org-owned + caller's personal KBs).
    kbs_result = await db.execute(
        select(PortalKnowledgeBase).where(
            PortalKnowledgeBase.org_id == perms.org_id,
            (PortalKnowledgeBase.owner_type == "org") | (PortalKnowledgeBase.owner_user_id == perms.user_id),
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

    uploads_result = await db.execute(
        select(KBUpload.kb_id, func.count(KBUpload.id))
        .where(
            KBUpload.org_id == org.id,
            KBUpload.kb_id.in_(kb_ids),
            KBUpload.status.in_(_VISIBLE_UPLOAD_STATUSES),
        )
        .group_by(KBUpload.kb_id)
    )
    visible_uploads_by_slug: dict[str, int] = {}
    for kb_id, count in uploads_result.all():
        slug = slug_by_id.get(kb_id)
        if slug is not None:
            visible_uploads_by_slug[slug] = count

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

    # Usage from knowledge.queried product events for the last 30 days.
    # Each event carries a kb_slugs[] array (a single retrieve call may target
    # multiple KBs); jsonb_array_elements_text fans the array out so a query
    # against KBs A and B counts once for A and once for B. The
    # `jsonb_typeof = 'array'` guard skips legacy events that predate the
    # kb_slugs property without raising on jsonb_array_elements_text(NULL).
    usage_result = await db.execute(
        text("""
            SELECT
                s.kb_slug AS slug,
                COUNT(*) AS queries,
                COUNT(DISTINCT pe.user_id) AS users,
                COUNT(DISTINCT date_trunc('day', pe.created_at)) AS active_days
            FROM product_events pe
            CROSS JOIN LATERAL jsonb_array_elements_text(
                pe.properties->'kb_slugs'
            ) AS s(kb_slug)
            WHERE pe.org_id = :org_id
              AND pe.event_type = 'knowledge.queried'
              AND pe.created_at >= :cutoff
              AND jsonb_typeof(pe.properties->'kb_slugs') = 'array'
              AND s.kb_slug = ANY(:slugs)
            GROUP BY s.kb_slug
        """),
        {"org_id": org.id, "cutoff": usage_cutoff, "slugs": kb_slugs},
    )
    usage_by_slug: dict[str, tuple[int, int, int]] = {
        row.slug: (row.queries, row.users, row.active_days) for row in usage_result.all()
    }

    # Qdrant item counts — N parallel calls, one per KB. Each call is
    # a single filtered count query against the shared collection.
    item_counts = await asyncio.gather(
        *(_qdrant_count_for_kb(org.zitadel_org_id, kb.slug) for kb in kbs),
        return_exceptions=False,
    )
    items_by_slug: dict[str, int] = {kb.slug: (count or 0) for kb, count in zip(kbs, item_counts, strict=True)}

    # SPEC-PORTAL-KENNIS-001: chunks + sources per KB (one bulk call to
    # knowledge-ingest). Empty dicts on failure → falls back to 0 / connector
    # count in the response.
    _parent_chunks_by_slug, sources_by_slug = await knowledge_ingest_client.get_chunks_summary(
        org.zitadel_org_id, kb_slugs
    )
    # NOTE: parent_chunks counts are unreliable for the user-facing "M chunks"
    # display — the parent_chunks table is only populated for KBs that ran the
    # citation-enrichment pipeline. The Qdrant point count (`items_by_slug`) is
    # what the user actually thinks of as "chunks": indexed, retrievable units.
    del _parent_chunks_by_slug

    stats: dict[str, KBStatsSummary] = {}
    for kb in kbs:
        queries, users, active_days = usage_by_slug.get(kb.slug, (0, 0, 0))
        connectors_count = connectors_by_slug.get(kb.slug, 0)
        items_count = items_by_slug.get(kb.slug, 0)
        # sources = distinct connector_ids + direct upload artifacts (from
        # knowledge-ingest). Fall back to portal_connectors count when the
        # ingest call failed and we have no aggregate data.
        sources_count = sources_by_slug.get(kb.slug, connectors_count) + visible_uploads_by_slug.get(kb.slug, 0)
        stats[kb.slug] = KBStatsSummary(
            items=items_count,
            connectors=connectors_count,
            chunks=items_count,
            gaps_7d=gaps_by_slug.get(kb.slug, 0),
            usage_30d=queries,
            unique_users_30d=users,
            active_days_30d=active_days,
            sources=sources_count,
        )
    return KBStatsSummaryResponse(stats=stats)


@router.get("/knowledge-bases/{kb_slug}", response_model=AppKBOut, dependencies=[Depends(get_kb_with_access)])
async def get_app_knowledge_base(
    kb_slug: str,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> AppKBOut:
    """Return a single KB by slug for the caller's org.

    Magic slugs:
    - 'personal' resolves to the caller's personal-{user_id} KB
    - 'org' resolves to the org-wide KB
    Both are created as fallback if provisioning missed them.
    """
    if kb_slug == "personal":
        kb = await _resolve_personal_kb(perms.user_id, perms.org_id, db)
    elif kb_slug == "org":
        kb = await _resolve_org_kb(perms.user_id, perms.org_id, db)
    else:
        kb = await _get_kb_or_404(kb_slug, perms.org_id, db)
    return _kb_out(kb)


@router.post("/knowledge-bases", response_model=AppKBOut, status_code=status.HTTP_201_CREATED)
async def create_app_knowledge_base(
    body: AppKBCreateRequest,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> AppKBOut:
    """Create a new KB. The creator is automatically given the owner role."""
    org = await _load_org_or_500(db, perms.org_id)

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

    # Quota enforcement — SPEC-PORTAL-UNIFY-KB-001 Phase A (R-E1, R-E3, R-X3).
    # _resolve_personal_kb auto-provisioning is explicitly exempt (D8).
    if body.owner_type == "user":
        await assert_can_create_personal_kb(user_id=perms.user_id, org=org, db=db, role=perms.role)
    elif body.owner_type == "org":
        await assert_can_create_org_kb(org=org, db=db, role=perms.role)

    owner_user_id = perms.user_id if body.owner_type == "user" else None

    kb = PortalKnowledgeBase(
        org_id=org.id,
        name=body.name,
        slug=body.slug,
        description=body.description,
        created_by=perms.user_id,
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
            user_id=perms.user_id,
            org_id=org.id,
            role="owner",
            granted_by=perms.user_id,
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
                        granted_by=perms.user_id,
                    )
                )
            elif member.type == "group":
                db.add(
                    PortalGroupKBAccess(
                        group_id=int(member.id),
                        kb_id=kb.id,
                        role=member.role,
                        granted_by=perms.user_id,
                    )
                )

    kb.gitea_repo_slug = await docs_client.provision_and_store(org.slug, body.name, body.slug, body.visibility, db)

    await db.commit()

    # Sync initial visibility to knowledge-ingest so new chunks get the correct field.
    # Uses the Zitadel org_id (org.zitadel_org_id) as the tenant key in Qdrant.
    await knowledge_ingest_client.update_kb_visibility(org.zitadel_org_id, body.slug, body.visibility)

    return _kb_out(kb)


@router.delete(
    "/knowledge-bases/{kb_slug}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(get_kb_with_access)]
)
async def delete_app_knowledge_base(
    kb_slug: str,
    request: Request,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a KB and all associated data.

    Two paths to a delete (SPEC-PORTAL-KB-OWNERSHIP-001 REQ-1):

    1. **Owner pad**: caller has owner role on the KB (creator implicitly,
       or explicit owner via portal_user_kb_access). No override header.
    2. **Admin-override pad**: caller has ProfileRole.ADMIN, the KB is
       org-owned (``owner_type='org'``), AND the request carries
       ``X-Admin-Override-Confirm: I-WAS-NOT-CREATOR``. The header forces
       explicit intent — the frontend only attaches it after a typed
       "DELETE" confirmation in a second modal.

    Personal KBs of other users are 404 (firewall in
    ``get_kb_with_access`` runs before this body). The handler body
    re-checks the personal-firewall as belt-and-braces: if a future
    refactor accidentally moves the dep, the body still refuses to
    admin-override on personal KBs.

    Deletion order (identical for both paths — REQ-1.5):
    1. docs-app (only if gitea_repo_slug or docs_enabled) — Qdrant vectors, Gitea, docs DB row.
    2. knowledge-ingest (always) — FalkorDB graph nodes, Qdrant chunks, PG artifacts.
    3. Portal DB — KB row + cascaded access rows.

    Both step 1 and 2 raise on failure, aborting before the portal record is deleted.
    """
    org = await _load_org_or_500(db, perms.org_id)
    kb = await _get_kb_or_404(kb_slug, perms.org_id, db)

    role = await get_user_role_for_kb(
        kb.id,
        perms.user_id,
        db,
        default_org_role=kb.default_org_role,
        kb_org_id=kb.org_id,
        kb_created_by=kb.created_by,
    )
    is_owner = role == "owner"

    admin_override_used = False
    if not is_owner:
        # Admin-override pad gating (REQ-1.1, REQ-1.2, REQ-1.3).
        override_header = request.headers.get(ADMIN_OVERRIDE_HEADER, "")
        is_admin = perms.effective_role == ProfileRole.ADMIN
        header_present = override_header == ADMIN_OVERRIDE_VALUE
        # Belt-and-braces: even with the override header + admin role, refuse
        # to delete a personal KB of someone else. The route-level firewall
        # already returns 404 before this body, but a future refactor that
        # removes the dep must not silently expose personal data to admins.
        if is_admin and header_present and is_personal_kb(kb) and kb.owner_user_id != perms.user_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Knowledge base not found",
            )
        if not (is_admin and header_present and not is_personal_kb(kb)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Owner access required, or set header "
                    f"'{ADMIN_OVERRIDE_HEADER}: {ADMIN_OVERRIDE_VALUE}' as an admin"
                    " to delete an org KB you did not create"
                ),
            )
        admin_override_used = True

    # Step 1: Clean up docs-app (Qdrant vectors managed by docs, Gitea webhook/repo, docs DB row).
    if kb.gitea_repo_slug or kb.docs_enabled:
        await docs_client.deprovision_kb(org.slug, kb.slug)

    # Step 2: Clean up knowledge-ingest data (FalkorDB graph nodes, Qdrant chunks, PG artifacts).
    # Always called, regardless of docs/gitea state — connector-based KBs never have gitea_repo_slug.
    await knowledge_ingest_client.delete_kb(org.zitadel_org_id, kb.slug)

    # REQ-1.4 + REQ-4.1 — emit audit event when the admin-override pad fired.
    # Owner deletes still leave an auth trail via Caddy access logs; admin
    # cross-user deletes need the explicit application-level event so the
    # actor and previous_owner are queryable from portal_audit_log.
    if admin_override_used:
        await log_event(
            org_id=perms.org_id,
            actor=perms.user_id,
            action="kb.admin_deleted",
            resource_type="kb",
            resource_id=str(kb.id),
            details={
                "previous_owner": kb.created_by,
                "kb_name": kb.name,
                "kb_slug": kb.slug,
            },
        )
        # REQ-4.2 — structlog event so the same data lands in VictoriaLogs
        # for cross-service trace correlation. structlog kwargs become
        # top-level JSON keys, queryable as `event:kb_admin_deleted` etc.
        logger.info(
            "kb_admin_deleted",
            org_id=perms.org_id,
            actor_user_id=perms.user_id,
            kb_id=kb.id,
            kb_slug=kb.slug,
            previous_owner=kb.created_by,
        )

    # Step 3: Portal DB -- delete KB row (cascades access rows).
    # No tombstone: slug is free to reuse after a full delete (all data wiped).
    await db.delete(kb)
    await db.commit()


# -- Default org role ---------------------------------------------------------


class UpdateDefaultOrgRoleRequest(BaseModel):
    default_org_role: str | None  # "viewer", "contributor", or null


@router.put(
    "/knowledge-bases/{kb_slug}/default-org-role", response_model=AppKBOut, dependencies=[Depends(get_kb_with_access)]
)
async def update_default_org_role(
    kb_slug: str,
    body: UpdateDefaultOrgRoleRequest,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> AppKBOut:
    """Update the default org role for a KB. Requires owner access."""
    kb = await _get_kb_or_404(kb_slug, perms.org_id, db)
    await _require_owner(kb, perms.user_id, db)

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


@router.patch("/knowledge-bases/{kb_slug}", response_model=AppKBOut, dependencies=[Depends(get_kb_with_access)])
async def update_knowledge_base(
    kb_slug: str,
    body: AppKBUpdateRequest,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> AppKBOut:
    """Update KB properties. Requires owner access."""
    org = await _load_org_or_500(db, perms.org_id)
    kb = await _get_kb_or_404(kb_slug, perms.org_id, db)
    await _require_owner(kb, perms.user_id, db)

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


@router.get("/knowledge-bases/{kb_slug}/stats", response_model=KBStatsOut, dependencies=[Depends(get_kb_with_access)])
async def get_kb_stats(
    kb_slug: str,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> KBStatsOut:
    """Return dashboard stats for a KB: connectors, docs count, volume, usage."""
    org = await _load_org_or_500(db, perms.org_id)
    if kb_slug == "personal":
        kb = await _resolve_personal_kb(perms.user_id, perms.org_id, db)
    elif kb_slug == "org":
        kb = await _resolve_org_kb(perms.user_id, perms.org_id, db)
    else:
        kb = await _get_kb_or_404(kb_slug, perms.org_id, db)

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
            logger.warning(
                "kb_stats_docs_count_failed",
                kb_slug=kb_slug,
                org_slug=org.slug,
                exc_info=True,
            )

    # Qdrant vector count for this KB
    volume = await _qdrant_count_for_kb(org.zitadel_org_id, kb.slug)

    # Source artifact count from knowledge-ingest (PostgreSQL)
    source_count = await knowledge_ingest_client.get_source_count(org.zitadel_org_id, kb.slug)

    # FalkorDB graph stats (entity/edge counts for the org)
    graph_stats = await knowledge_ingest_client.get_graph_stats(org.zitadel_org_id)
    graph_entity_count: int | None = graph_stats.get("entity_count")
    graph_edge_count: int | None = graph_stats.get("edge_count")

    # Usage from knowledge.queried product events for the last 30 days.
    # Three signals together describe adoption: total volume, distinct
    # users (so one power user does not dominate), and distinct active
    # days (so one hackathon-spike does not look like daily use).
    usage_last_30d: int | None = None
    unique_users_30d: int | None = None
    active_days_30d: int | None = None
    try:
        cutoff = datetime.now(tz=dt.UTC) - timedelta(days=30)
        # NOTE: must use CAST(:p AS jsonb) NOT :p::jsonb — `::` immediately
        # after a bind parameter is a SQLAlchemy text() syntax collision.
        # See klai/projects/portal-backend.md.
        usage_result = await db.execute(
            text("""
                SELECT
                    COUNT(*) AS queries,
                    COUNT(DISTINCT user_id) AS users,
                    COUNT(DISTINCT date_trunc('day', created_at)) AS active_days
                FROM product_events
                WHERE org_id = :org_id
                  AND event_type = 'knowledge.queried'
                  AND created_at >= :cutoff
                  AND properties->'kb_slugs' @> CAST(:slug_jsonb AS jsonb)
            """),
            {
                "org_id": org.id,
                "cutoff": cutoff,
                # JSON-encode the slug so it becomes a JSONB scalar string,
                # which @> compares against the array elements.
                "slug_jsonb": json.dumps(kb.slug),
            },
        )
        row = usage_result.one()
        usage_last_30d = row.queries
        unique_users_30d = row.users
        active_days_30d = row.active_days
    except Exception:
        # Fail-loud: the original "Usage unavailable" tile fell back to
        # null when this query raised, and the debug-level log meant the
        # failure was invisible in VictoriaLogs. Warning + traceback so
        # the next regression surfaces in Grafana within minutes.
        logger.warning(
            "kb_stats_usage_query_failed",
            kb_slug=kb_slug,
            org_id=org.id,
            exc_info=True,
        )

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
        logger.warning(
            "kb_stats_gap_count_failed",
            kb_slug=kb_slug,
            org_id=org.id,
            exc_info=True,
        )

    return KBStatsOut(
        docs_count=docs_count,
        connector_count=len(connectors),
        connectors=connector_summaries,
        volume=volume,
        usage_last_30d=usage_last_30d,
        unique_users_30d=unique_users_30d,
        active_days_30d=active_days_30d,
        org_gap_count_7d=org_gap_count_7d,
        source_page_count=source_count,
        vector_chunk_count=volume,
        graph_entity_count=graph_entity_count,
        graph_edge_count=graph_edge_count,
    )


# -- SPEC-PORTAL-KENNIS-001: Bronnen ---------------------------------------
#
# "Alles is een bron" — connectors and direct uploads share a single row
# shape on the KB detail page. The frontend hits ONE endpoint per KB
# (this one) instead of two (connectors + items).


class SourceOut(BaseModel):
    """Uniform shape for one row on the Sources tab."""

    kind: str  # "connector" or "upload"
    id: str  # connector_id (for connectors) or artifact_id (for uploads)
    name: str  # display name (connector.name OR artifact.path)
    type_label: str  # "Notion", "GitHub", "PDF", "URL", etc. — frontend may translate
    connector_type: str | None = None  # raw connector_type for connectors; None for uploads
    source_url: str | None = None  # direct URL uploads only; stable source URL for drill-down display
    items_count: int = 0  # number of artifacts under a connector; 1 for direct uploads
    chunks_count: int = 0  # parent_chunks across the bron's artifact(s)
    status: str | None = None  # raw last_sync_status for connectors; None for uploads
    last_sync_at: datetime | None = None
    created_at: datetime | None = None  # for uploads — sort key
    index_status: str | None = None  # "synced", "pending", or "not_synced" for uploads; None for connectors


class SourcesResponse(BaseModel):
    sources: list[SourceOut] = []


class RenameUploadRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class RenameUploadResponse(BaseModel):
    artifact_id: str
    display_name: str


class SourceContentItem(BaseModel):
    """One artifact under a connector (drill-down)."""

    id: str
    path: str
    content_type: str
    chunks_count: int = 0
    created_at: datetime


class SourceContentChunk(BaseModel):
    """One parent_chunk under a direct-upload artifact (drill-down)."""

    id: int
    position: int
    text: str
    token_count: int


class SourceContentResponse(BaseModel):
    kind: str  # "connector" or "upload"
    items: list[SourceContentItem] = []  # populated when kind == connector
    chunks: list[SourceContentChunk] = []  # populated when kind == upload
    total: int = 0
    limit: int
    offset: int


def _connector_type_label(connector_type: str) -> str:
    """Render a human label for a connector type. Frontend may further translate."""
    mapping = {
        "github": "GitHub",
        "notion": "Notion",
        "google_drive": "Google Drive",
        "ms_docs": "Microsoft Docs",
        "web_crawler": "Website (pagina's)",
        "airtable": "Airtable",
        "confluence": "Confluence",
        "mcp_connector": "MCP",
    }
    return mapping.get(connector_type, connector_type.replace("_", " ").title())


def _upload_type_label(content_type: str) -> str:
    """Render a human label for an upload's content_type."""
    if not content_type or content_type == "unknown":
        return "Bestand"
    ct = content_type.lower()
    if ct in {"pdf", "application/pdf"}:
        return "PDF"
    if ct == "web_page":
        return "Websitepagina"
    if ct in {"url", "html", "text/html"}:
        return "Link"
    if ct in {"text", "markdown", "txt", "text/plain", "text/markdown"}:
        return "Tekst"
    if ct.startswith("image"):
        return "Afbeelding"
    # Fallback for unrecognised types: split MIME by "/" and uppercase the
    # subtype ("text/plain" → "PLAIN"), or for non-MIME slugs replace
    # underscores with spaces before title-casing ("plain_text" → "Plain Text").
    # Previously str.title() left underscores intact and rendered "Plain_Text".
    if "/" in content_type:
        return content_type.split("/")[-1].upper()
    return content_type.replace("_", " ").title()


@router.get(
    "/knowledge-bases/{kb_slug}/sources",
    response_model=SourcesResponse,
    dependencies=[Depends(get_kb_with_access)],
)
async def list_kb_sources(
    kb_slug: str,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> SourcesResponse:
    """Unified sources list for a KB.

    SPEC-PORTAL-KENNIS-001 Phase E. Combines:
      - portal_connectors (display name, sync status, type)
      - knowledge-ingest aggregates (items_count, chunks_count per connector;
        plus direct uploads from artifacts.extra->>'source_connector_id' IS NULL)

    Connectors with zero items still appear (a newly-created connector before
    its first sync) so users can see what they configured.
    """
    if kb_slug == "personal":
        kb = await _resolve_personal_kb(perms.user_id, perms.org_id, db)
    elif kb_slug == "org":
        kb = await _resolve_org_kb(perms.user_id, perms.org_id, db)
    else:
        kb = await _get_kb_or_404(kb_slug, perms.org_id, db)

    org = await _load_org_or_500(db, perms.org_id)

    # Portal-side connectors (display name + sync status).
    # Fetch ALL states so we can distinguish active rows (show in list)
    # from rows in ``'deleting'`` (hide entirely — the async purge owns
    # them, SPEC-CONNECTOR-DELETE-LIFECYCLE-001 REQ-02). Without this
    # distinction the sources list keeps showing the row until purge
    # completes, which the user reads as "delete didn't work".
    conn_result = await db.execute(select(PortalConnector).where(PortalConnector.kb_id == kb.id))
    all_portal_connectors = list(conn_result.scalars().all())
    portal_connectors = [c for c in all_portal_connectors if c.state == "active"]
    deleting_ids = {str(c.id) for c in all_portal_connectors if c.state == "deleting"}
    connector_by_id: dict[str, PortalConnector] = {str(c.id): c for c in portal_connectors}

    # Knowledge-ingest aggregates (per connector_id and direct uploads)
    aggregates = await knowledge_ingest_client.get_kb_sources(org.zitadel_org_id, kb.slug)
    if aggregates is None:
        aggregates = {"connectors": [], "uploads": []}

    sources: list[SourceOut] = []

    # 1) Connector rows: merge knowledge-ingest counts with portal display data.
    seen_connector_ids: set[str] = set()
    for agg in aggregates.get("connectors", []):
        cid = str(agg.get("connector_id") or "")
        if not cid:
            continue
        # Hide aggregates for connectors mid-purge — the row would
        # otherwise reappear in the orphan branch below for the duration
        # of the async cleanup, which reads as "delete failed".
        if cid in deleting_ids:
            seen_connector_ids.add(cid)
            continue
        seen_connector_ids.add(cid)
        portal_conn = connector_by_id.get(cid)
        if portal_conn is None:
            # Orphan: artifacts reference a connector that no longer exists in
            # portal DB. Surface anyway so the user can see the data and clean
            # it up via Geavanceerd. Display fallbacks keep the row meaningful.
            sources.append(
                SourceOut(
                    kind="connector",
                    id=cid,
                    name=f"(verwijderde koppeling) {cid[:8]}",
                    type_label="Koppeling",
                    connector_type=None,
                    items_count=int(agg.get("items_count") or 0),
                    chunks_count=int(agg.get("chunks_count") or 0),
                    status="orphan",
                )
            )
        else:
            sources.append(
                SourceOut(
                    kind="connector",
                    id=cid,
                    name=portal_conn.name or _connector_type_label(portal_conn.connector_type),
                    type_label=_connector_type_label(portal_conn.connector_type),
                    connector_type=portal_conn.connector_type,
                    items_count=int(agg.get("items_count") or 0),
                    chunks_count=int(agg.get("chunks_count") or 0),
                    status=portal_conn.last_sync_status,
                    last_sync_at=portal_conn.last_sync_at,
                )
            )

    # 2) Connectors that exist in portal DB but have no artifacts yet (never synced).
    for portal_conn in portal_connectors:
        cid = str(portal_conn.id)
        if cid in seen_connector_ids:
            continue
        sources.append(
            SourceOut(
                kind="connector",
                id=cid,
                name=portal_conn.name or _connector_type_label(portal_conn.connector_type),
                type_label=_connector_type_label(portal_conn.connector_type),
                connector_type=portal_conn.connector_type,
                items_count=0,
                chunks_count=0,
                status=portal_conn.last_sync_status,
                last_sync_at=portal_conn.last_sync_at,
            )
        )

    # 3) Direct uploads — one row per artifact without source_connector_id.
    for upload in aggregates.get("uploads", []):
        created_at_unix = upload.get("created_at")
        created_at_dt = datetime.fromtimestamp(int(created_at_unix), tz=dt.UTC) if created_at_unix is not None else None
        sources.append(
            SourceOut(
                kind="upload",
                id=str(upload.get("id") or ""),
                name=str(upload.get("display_name") or upload.get("path") or "(zonder naam)"),
                type_label=_upload_type_label(str(upload.get("content_type") or "")),
                connector_type=None,
                source_url=upload.get("source_url"),
                items_count=1,
                chunks_count=int(upload.get("chunks_count") or 0),
                status=None,
                created_at=created_at_dt,
                index_status=str(upload.get("index_status") or "synced"),
            )
        )

    upload_rows_result = await db.execute(
        select(KBUpload)
        .where(
            KBUpload.org_id == org.id,
            KBUpload.kb_id == kb.id,
            KBUpload.status.in_(_VISIBLE_UPLOAD_STATUSES),
        )
        .order_by(KBUpload.created_at.desc())
    )
    for upload in upload_rows_result.scalars().all():
        sources.append(
            SourceOut(
                kind="upload",
                id=str(upload.id),
                name=upload.filename,
                type_label=_upload_type_label(upload.mime or upload.extension),
                connector_type=None,
                items_count=1,
                chunks_count=0,
                status=upload.status,
                created_at=upload.created_at,
            )
        )

    return SourcesResponse(sources=sources)


@router.get(
    "/knowledge-bases/{kb_slug}/sources/{source_id}/content",
    response_model=SourceContentResponse,
    dependencies=[Depends(get_kb_with_access)],
)
async def get_source_content(
    kb_slug: str,
    source_id: str,
    kind: str,
    limit: int = 50,
    offset: int = 0,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> SourceContentResponse:
    """Drill-down: items under a connector, or chunks under a direct upload.

    The ``kind`` query param disambiguates whether ``source_id`` is a
    portal connector ID or an artifact UUID — we don't trust the ID space
    not to overlap. Frontend always knows the kind from the parent
    /sources call.
    """
    if kind not in {"connector", "upload"}:
        raise HTTPException(status_code=400, detail="kind must be 'connector' or 'upload'")
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 200")
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset must be >= 0")

    if kb_slug == "personal":
        kb = await _resolve_personal_kb(perms.user_id, perms.org_id, db)
    elif kb_slug == "org":
        kb = await _resolve_org_kb(perms.user_id, perms.org_id, db)
    else:
        kb = await _get_kb_or_404(kb_slug, perms.org_id, db)

    org = await _load_org_or_500(db, perms.org_id)

    if kind == "connector":
        # Validate the connector belongs to this org+KB before proxying.
        conn_result = await db.execute(
            select(PortalConnector).where(
                PortalConnector.id == source_id,
                PortalConnector.kb_id == kb.id,
                PortalConnector.org_id == perms.org_id,
            )
        )
        if conn_result.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="Connector not found in this KB")
        data = await knowledge_ingest_client.list_connector_items(
            org.zitadel_org_id, kb.slug, source_id, limit=limit, offset=offset
        )
        if data is None:
            return SourceContentResponse(kind=kind, items=[], total=0, limit=limit, offset=offset)
        items = [
            SourceContentItem(
                id=str(row.get("id") or ""),
                path=str(row.get("path") or ""),
                content_type=str(row.get("content_type") or "unknown"),
                chunks_count=int(row.get("chunks_count") or 0),
                created_at=datetime.fromtimestamp(int(row.get("created_at") or 0), tz=dt.UTC),
            )
            for row in (data.get("items") or [])
        ]
        return SourceContentResponse(
            kind=kind,
            items=items,
            total=int(data.get("total") or 0),
            limit=limit,
            offset=offset,
        )

    # kind == "upload"
    # No portal-side ownership check on individual artifacts: the org_id +
    # kb_slug pinning in knowledge-ingest's tenant_scoped_connection is the
    # tenant guard. The KB-level _get_kb_or_404 above already proved the
    # caller has access to this KB.
    data = await knowledge_ingest_client.list_upload_chunks(org.zitadel_org_id, source_id, limit=limit, offset=offset)
    if data is None:
        return SourceContentResponse(kind=kind, chunks=[], total=0, limit=limit, offset=offset)
    chunks = [
        SourceContentChunk(
            id=int(row.get("id") or 0),
            position=int(row.get("position") or 0),
            text=str(row.get("text") or ""),
            token_count=int(row.get("token_count") or 0),
        )
        for row in (data.get("chunks") or [])
    ]
    return SourceContentResponse(
        kind=kind,
        chunks=chunks,
        total=int(data.get("total") or 0),
        limit=limit,
        offset=offset,
    )


# -- Uploads: reindex / delete ------------------------------------------------


@router.post(
    "/knowledge-bases/{kb_slug}/uploads/{artifact_id}/reindex",
    status_code=202,
    dependencies=[Depends(get_kb_with_access)],
)
async def reindex_upload(
    kb_slug: str,
    artifact_id: str,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Re-enqueue a direct-upload artifact for indexing.

    Requires at least contributor role on the KB.
    Returns 202 Accepted; indexing happens asynchronously.
    SPEC-PORTAL-KENNIS-002 A4.
    """
    org = await _load_org_or_500(db, perms.org_id)
    kb = await _get_kb_or_404(kb_slug, perms.org_id, db)

    # B1 viewer gate: contributors and above may trigger reindex
    role = await get_user_role_for_kb(
        kb.id,
        perms.user_id,
        db,
        default_org_role=kb.default_org_role,
        kb_org_id=kb.org_id,
        kb_created_by=kb.created_by,
    )
    if role not in ("contributor", "owner"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Contributor or owner access required",
        )

    await knowledge_ingest_client.reindex_artifact(org.zitadel_org_id, artifact_id)
    return {"artifact_id": artifact_id, "status": "pending"}


@router.patch(
    "/knowledge-bases/{kb_slug}/uploads/{artifact_id}",
    response_model=RenameUploadResponse,
    dependencies=[Depends(get_kb_with_access)],
)
async def rename_kb_upload(
    kb_slug: str,
    artifact_id: str,
    body: RenameUploadRequest,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> RenameUploadResponse:
    """Rename the display label for a direct-upload artifact.

    The ingest-side artifact path is left untouched because it is the stable
    Qdrant document key. This endpoint changes only the user-facing name.
    """
    org = await _load_org_or_500(db, perms.org_id)
    kb = await _get_kb_or_404(kb_slug, perms.org_id, db)

    role = await get_user_role_for_kb(
        kb.id,
        perms.user_id,
        db,
        default_org_role=kb.default_org_role,
        kb_org_id=kb.org_id,
        kb_created_by=kb.created_by,
    )
    if role not in ("contributor", "owner"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Contributor or owner access required",
        )

    display_name = body.name.strip()
    result = await knowledge_ingest_client.rename_kb_upload(
        org.zitadel_org_id,
        kb.slug,
        artifact_id,
        display_name,
    )
    return RenameUploadResponse(
        artifact_id=str(result.get("artifact_id") or artifact_id),
        display_name=str(result.get("display_name") or display_name),
    )


@router.delete(
    "/knowledge-bases/{kb_slug}/uploads/{artifact_id}", status_code=204, dependencies=[Depends(get_kb_with_access)]
)
async def delete_kb_upload(
    kb_slug: str,
    artifact_id: str,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a direct-upload artifact from the KB.

    Owners may delete any upload.
    Contributors may only delete their own uploads (ownership enforced by
    knowledge-ingest via X-User-ID check).
    Viewers get 403.
    SPEC-PORTAL-KENNIS-002 B2.
    """
    org = await _load_org_or_500(db, perms.org_id)
    kb = await _get_kb_or_404(kb_slug, perms.org_id, db)

    # B1 viewer gate
    role = await get_user_role_for_kb(
        kb.id,
        perms.user_id,
        db,
        default_org_role=kb.default_org_role,
        kb_org_id=kb.org_id,
        kb_created_by=kb.created_by,
    )
    if role not in ("contributor", "owner"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Contributor or owner access required",
        )

    # Contributors: pass user_id so knowledge-ingest enforces ownership.
    # Owners: omit user_id to allow cross-user deletes.
    caller_user_id = perms.user_id if role == "contributor" else None
    await knowledge_ingest_client.delete_kb_upload(
        org.zitadel_org_id,
        kb.slug,
        artifact_id,
        user_id=caller_user_id,
    )


# -- Members: list ------------------------------------------------------------


@router.get(
    "/knowledge-bases/{kb_slug}/members",
    response_model=MembersResponse,
    dependencies=[Depends(require_capability(Capability.KB_MEMBERS)), Depends(get_kb_with_access)],
)
async def list_members(
    kb_slug: str,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> MembersResponse:
    """List all members of a KB (user + group access). Readable by any org member."""
    kb = await _get_kb_or_404(kb_slug, perms.org_id, db)

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
    dependencies=[Depends(require_capability(Capability.KB_MEMBERS)), Depends(get_kb_with_access)],
)
async def invite_user(
    kb_slug: str,
    body: InviteUserRequest,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> UserMemberOut:
    """Invite a user to a KB with the given role. Requires owner access."""
    kb = await _get_kb_or_404(kb_slug, perms.org_id, db)
    await _require_owner(kb, perms.user_id, db)
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
        org_id=perms.org_id,
        role=body.role,
        granted_by=perms.user_id,
    )
    db.add(access)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User already has access to this knowledge base",
        ) from exc
    await db.refresh(access)  # Pre-commit refresh to load server_default columns while tenant context is still set.
    await db.commit()
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


@router.patch(
    "/knowledge-bases/{kb_slug}/members/users/{access_id}",
    response_model=UserMemberOut,
    dependencies=[Depends(require_capability(Capability.KB_MEMBERS)), Depends(get_kb_with_access)],
)
async def update_user_role(
    kb_slug: str,
    access_id: int,
    body: UpdateRoleRequest,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> UserMemberOut:
    """Change a user's role on a KB. Requires owner access."""
    kb = await _get_kb_or_404(kb_slug, perms.org_id, db)
    await _require_owner(kb, perms.user_id, db)
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


@router.delete(
    "/knowledge-bases/{kb_slug}/members/users/{access_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_capability(Capability.KB_MEMBERS)), Depends(get_kb_with_access)],
)
async def remove_user(
    kb_slug: str,
    access_id: int,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove a user from a KB. Requires owner access."""
    kb = await _get_kb_or_404(kb_slug, perms.org_id, db)
    await _require_owner(kb, perms.user_id, db)

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
    dependencies=[Depends(require_capability(Capability.KB_MEMBERS)), Depends(get_kb_with_access)],
)
async def invite_group(
    kb_slug: str,
    body: InviteGroupRequest,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> GroupMemberOut:
    """Invite a group to a KB with the given role. Requires owner access."""
    kb = await _get_kb_or_404(kb_slug, perms.org_id, db)
    await _require_owner(kb, perms.user_id, db)
    _validate_role(body.role)

    if kb.owner_type == "user":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Personal KBs cannot be shared",
        )

    # Verify group exists in org and is not a system group
    group = await _get_non_system_group_or_404(body.group_id, perms.org_id, db)

    access = PortalGroupKBAccess(
        group_id=body.group_id,
        kb_id=kb.id,
        role=body.role,
        granted_by=perms.user_id,
    )
    db.add(access)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Group already has access to this knowledge base",
        ) from exc
    await db.refresh(access)  # Pre-commit refresh to load server_default columns while tenant context is still set.
    await db.commit()
    return GroupMemberOut(
        id=access.id,
        group_id=access.group_id,
        group_name=group.name,
        role=access.role,
        granted_at=access.granted_at,
        granted_by=access.granted_by,
    )


# -- Members: update group role -----------------------------------------------


@router.patch(
    "/knowledge-bases/{kb_slug}/members/groups/{access_id}",
    response_model=GroupMemberOut,
    dependencies=[Depends(require_capability(Capability.KB_MEMBERS)), Depends(get_kb_with_access)],
)
async def update_group_role(
    kb_slug: str,
    access_id: int,
    body: UpdateRoleRequest,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> GroupMemberOut:
    """Change a group's role on a KB. Requires owner access."""
    kb = await _get_kb_or_404(kb_slug, perms.org_id, db)
    await _require_owner(kb, perms.user_id, db)
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


@router.delete(
    "/knowledge-bases/{kb_slug}/members/groups/{access_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_capability(Capability.KB_MEMBERS)), Depends(get_kb_with_access)],
)
async def remove_group(
    kb_slug: str,
    access_id: int,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove a group from a KB. Requires owner access."""
    kb = await _get_kb_or_404(kb_slug, perms.org_id, db)
    await _require_owner(kb, perms.user_id, db)

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
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> list[KBWithAccessOut]:
    """Return all docs-enabled KBs for the org, with is_accessible flag per KB.

    Used by /app/docs to show both accessible and locked KB cards.
    """
    from app.services.access import get_accessible_kb_slugs

    # All docs-enabled KBs (org-owned only; personal KBs stay private)
    result = await db.execute(
        select(PortalKnowledgeBase)
        .where(
            PortalKnowledgeBase.org_id == perms.org_id,
            PortalKnowledgeBase.docs_enabled == True,  # noqa: E712
            PortalKnowledgeBase.owner_type == "org",
        )
        .order_by(PortalKnowledgeBase.name)
    )
    all_kbs = result.scalars().all()

    # REQ-6: pass effective_role so personal-role callers do not see org slug
    # / default_org_role KBs in the access list.
    accessible_slugs = set(await get_accessible_kb_slugs(perms.user_id, db, user_role=perms.effective_role.value))

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
    connector_id: str | None = None
    use_saved_credentials: bool = False


class CrawlPreviewResponse(BaseModel):
    url: str
    fit_markdown: str
    word_count: int
    warnings: list[str] = []
    content_selector: str | None = None
    selector_source: str | None = None
    # SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-3 — five-way classification
    # used by the wizard to gate step-5 → step-6 advance.
    # Default is "unknown" (fail-closed): absence of classification must
    # never be treated as success.
    classification: str = "unknown"
    classification_reason: str | None = None


async def _load_saved_web_crawler_cookies(
    kb: PortalKnowledgeBase,
    connector_id: str | None,
    org_id: int,
    db: AsyncSession,
) -> list[dict]:
    """Return encrypted web-crawler cookies for preview/probe without exposing them."""
    if not connector_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error_code": "connector_id_required_for_saved_credentials"},
        )
    result = await db.execute(
        select(PortalConnector).where(
            PortalConnector.id == connector_id,
            PortalConnector.kb_id == kb.id,
            PortalConnector.org_id == org_id,
            PortalConnector.connector_type == "web_crawler",
            PortalConnector.state == "active",
        )
    )
    connector = result.scalar_one_or_none()
    if not connector:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connector not found")
    if connector.encrypted_credentials is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "saved_credentials_missing"},
        )
    if credential_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "credential_store_unavailable"},
        )
    try:
        credentials = await credential_store.decrypt_credentials(
            org_id=org_id,
            encrypted_credentials=bytes(connector.encrypted_credentials),
            db=db,
        )
    except Exception as exc:
        logger.warning(
            "saved_web_crawler_credentials_decrypt_failed",
            connector_id=connector_id,
            org_id=org_id,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "saved_credentials_unavailable"},
        ) from exc
    cookies = credentials.get("cookies")
    if not isinstance(cookies, list) or not cookies:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "saved_credentials_missing"},
        )
    return cookies


async def _resolve_web_crawler_probe_cookies(
    *,
    kb: PortalKnowledgeBase,
    org_id: int,
    db: AsyncSession,
    cookies: list[dict] | None,
    connector_id: str | None,
    use_saved_credentials: bool,
) -> list[dict] | None:
    if use_saved_credentials and cookies:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error_code": "saved_credentials_conflict"},
        )
    if use_saved_credentials:
        return await _load_saved_web_crawler_cookies(kb, connector_id, org_id, db)
    return cookies


@router.post(
    "/knowledge-bases/{kb_slug}/connectors/crawl-preview",
    response_model=CrawlPreviewResponse,
    dependencies=[Depends(get_kb_with_access)],
)
async def crawl_preview(
    kb_slug: str,
    body: CrawlPreviewRequest,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> CrawlPreviewResponse:
    """Preview KB content for a URL using PruningContentFilter. Requires owner role."""
    kb = await _get_kb_or_404(kb_slug, perms.org_id, db)
    await _require_owner(kb, perms.user_id, db)
    cookies = await _resolve_web_crawler_probe_cookies(
        kb=kb,
        org_id=perms.org_id,
        db=db,
        cookies=body.cookies,
        connector_id=body.connector_id,
        use_saved_credentials=body.use_saved_credentials,
    )
    # SPEC-CONNECTOR-INPUT-VALIDATION-001 hotfix: knowledge-ingest
    # identity verifier expects the Zitadel resourceowner ID (the
    # 18-digit numeric string, e.g. "100000000000000002"), NOT the
    # portal_orgs int PK. The deprovisioning audit on 2026-05-05 flagged
    # this same bug pattern across other internal call paths; this
    # pass-through inherited the bug because it was modeled on the older
    # broken callsite. The auth-probe pass-through below has the same fix.
    org = await _load_org_or_500(db, perms.org_id)
    result = await knowledge_ingest_client.preview_crawl(
        url=body.url,
        content_selector=body.content_selector,
        org_id=org.zitadel_org_id,
        try_ai=body.try_ai,
        cookies=cookies,
    )
    return CrawlPreviewResponse(
        url=result.get("url", body.url),
        fit_markdown=result.get("fit_markdown", ""),
        word_count=result.get("word_count", 0),
        warnings=result.get("warnings", []),
        content_selector=result.get("content_selector"),
        selector_source=result.get("selector_source"),
        classification=result.get("classification", "unknown"),
        classification_reason=result.get("classification_reason"),
    )


# -- Auth probe (SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-2) -------------------


class AuthProbeRequest(BaseModel):
    url: str
    cookies: list[dict] | None = None
    connector_id: str | None = None
    use_saved_credentials: bool = False


class AuthProbeResponse(BaseModel):
    """Five-way classification of the seed-page fetch outcome.

    See SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-2 for full semantics.
    """

    classification: str
    match_reasons: list[str] = []
    word_count: int = 0
    auth_guard: dict | None = None


@router.post(
    "/knowledge-bases/{kb_slug}/connectors/auth-probe",
    response_model=AuthProbeResponse,
    dependencies=[Depends(get_kb_with_access)],
)
async def auth_probe(
    kb_slug: str,
    body: AuthProbeRequest,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> AuthProbeResponse:
    """SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-2 — wizard step-4 auth probe.

    Validates that supplied cookies actually unlock the seed URL before the
    wizard allows the user to advance to the selector step. Owner role
    required (mirrors crawl_preview).
    """
    kb = await _get_kb_or_404(kb_slug, perms.org_id, db)
    await _require_owner(kb, perms.user_id, db)
    cookies = await _resolve_web_crawler_probe_cookies(
        kb=kb,
        org_id=perms.org_id,
        db=db,
        cookies=body.cookies,
        connector_id=body.connector_id,
        use_saved_credentials=body.use_saved_credentials,
    )
    # See crawl_preview above for the org_id rationale (Zitadel ID, not int PK).
    org = await _load_org_or_500(db, perms.org_id)
    result = await knowledge_ingest_client.auth_probe(
        url=body.url,
        org_id=org.zitadel_org_id,
        cookies=cookies,
    )
    return AuthProbeResponse(
        classification=result.get("classification", "auth_failed_unreachable"),
        match_reasons=result.get("match_reasons", []),
        word_count=result.get("word_count", 0),
        auth_guard=result.get("auth_guard"),
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
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> AppGroupsResponse:
    """Lightweight group list for the member picker. Any org member can access."""
    result = await db.execute(
        select(PortalGroup.id, PortalGroup.name)
        .where(PortalGroup.org_id == perms.org_id)
        .where(PortalGroup.is_system == False)  # noqa: E712
        .order_by(PortalGroup.name)
    )
    return AppGroupsResponse(groups=[AppGroupItem(id=row.id, name=row.name) for row in result.all()])


@router.get("/users", response_model=AppUsersResponse)
async def list_users_for_picker(
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> AppUsersResponse:
    """Lightweight user list for the member picker. Any org member can access."""
    result = await db.execute(
        select(PortalUser).where(PortalUser.org_id == perms.org_id).order_by(PortalUser.created_at)
    )
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
