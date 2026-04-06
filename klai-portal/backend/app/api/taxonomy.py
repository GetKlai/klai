"""Taxonomy API for knowledge base categorisation and proposal review."""

import asyncio
import logging
import re
import time
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import _get_caller_org, _require_admin, bearer
from app.core.config import settings
from app.core.database import get_db, set_tenant
from app.models.knowledge_bases import PortalKnowledgeBase
from app.models.portal import PortalOrg
from app.models.retrieval_gaps import PortalRetrievalGap
from app.models.taxonomy import PortalTaxonomyNode, PortalTaxonomyProposal
from app.services.access import get_user_role_for_kb
from app.trace import get_trace_headers

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/app/knowledge-bases", tags=["taxonomy"])

# Background task tracking for fire-and-forget operations (SPEC-KB-024)
_background_tasks: set[asyncio.Task] = set()


async def _trigger_auto_categorise(
    org_id: str,
    kb_slug: str,
    node_id: int,
    cluster_centroid: list[float],
) -> None:
    """Fire-and-forget POST to knowledge-ingest auto-categorise endpoint (SPEC-KB-024 R4)."""
    url = f"{settings.knowledge_ingest_url}/ingest/v1/taxonomy/auto-categorise"
    headers = {
        **get_trace_headers(),
    }
    if settings.knowledge_ingest_secret:
        headers["x-internal-secret"] = settings.knowledge_ingest_secret

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                url,
                json={
                    "org_id": org_id,
                    "kb_slug": kb_slug,
                    "node_id": node_id,
                    "cluster_centroid": cluster_centroid,
                },
                headers=headers,
            )
            if resp.status_code != 200:
                log.warning(
                    "auto_categorise_failed",
                    extra={
                        "status": resp.status_code,
                        "body": resp.text[:200],
                        "kb_slug": kb_slug,
                        "node_id": node_id,
                    },
                )
            else:
                data = resp.json()
                log.info(
                    "auto_categorise_triggered",
                    extra={
                        "kb_slug": kb_slug,
                        "node_id": node_id,
                        "categorised": data.get("categorised"),
                    },
                )
    except Exception:
        log.exception(
            "auto_categorise_error",
            extra={"kb_slug": kb_slug, "node_id": node_id},
        )


# -- Pydantic schemas ---------------------------------------------------------


class TaxonomyNodeOut(BaseModel):
    id: int
    kb_id: int
    parent_id: int | None
    name: str
    slug: str
    description: str | None = None
    doc_count: int
    sort_order: int
    created_at: datetime
    created_by: str


class TaxonomyNodesResponse(BaseModel):
    nodes: list[TaxonomyNodeOut]


class CreateNodeRequest(BaseModel):
    name: str
    parent_id: int | None = None


class UpdateNodeRequest(BaseModel):
    name: str | None = None
    parent_id: int | None = None
    description: str | None = None


class ProposalOut(BaseModel):
    id: int
    kb_id: int
    proposal_type: str
    status: str
    title: str
    payload: dict
    confidence_score: float | None
    created_at: datetime
    reviewed_at: datetime | None
    reviewed_by: str | None
    rejection_reason: str | None


class ProposalsResponse(BaseModel):
    proposals: list[ProposalOut]


class CreateProposalRequest(BaseModel):
    proposal_type: str
    title: str
    payload: dict
    confidence_score: float | None = None


class RejectRequest(BaseModel):
    reason: str


# -- Helpers ------------------------------------------------------------------


def _slugify(name: str) -> str:
    """Convert a name to a URL-safe slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    return re.sub(r"[-\s]+", "-", slug).strip("-")


def _node_out(node: PortalTaxonomyNode) -> TaxonomyNodeOut:
    return TaxonomyNodeOut(
        id=node.id,
        kb_id=node.kb_id,
        parent_id=node.parent_id,
        name=node.name,
        slug=node.slug,
        description=node.description,
        doc_count=node.doc_count,
        sort_order=node.sort_order,
        created_at=node.created_at,
        created_by=node.created_by,
    )


def _proposal_out(p: PortalTaxonomyProposal) -> ProposalOut:
    return ProposalOut(
        id=p.id,
        kb_id=p.kb_id,
        proposal_type=p.proposal_type,
        status=p.status,
        title=p.title,
        payload=p.payload,
        confidence_score=p.confidence_score,
        created_at=p.created_at,
        reviewed_at=p.reviewed_at,
        reviewed_by=p.reviewed_by,
        rejection_reason=p.rejection_reason,
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


async def _require_role(
    kb_id: int,
    caller_id: str,
    db: AsyncSession,
    min_role: str,
) -> str:
    """Require at least min_role. Returns the actual role."""
    role = await get_user_role_for_kb(kb_id, caller_id, db)
    rank = {"viewer": 1, "contributor": 2, "owner": 3}
    if role is None or rank.get(role, 0) < rank.get(min_role, 0):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"{min_role} access required")
    return role


async def _check_circular_reference(
    node_id: int,
    new_parent_id: int,
    kb_id: int,
    db: AsyncSession,
) -> None:
    """Walk up the ancestor chain from new_parent_id. Reject if node_id appears."""
    result = await db.execute(select(PortalTaxonomyNode).where(PortalTaxonomyNode.kb_id == kb_id))
    nodes_by_id = {n.id: n for n in result.scalars().all()}

    current = new_parent_id
    while current is not None:
        if current == node_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot move node under its own descendant",
            )
        parent_node = nodes_by_id.get(current)
        current = parent_node.parent_id if parent_node else None


# -- Taxonomy nodes -----------------------------------------------------------


@router.get("/{kb_slug}/taxonomy/nodes", response_model=TaxonomyNodesResponse)
async def list_taxonomy_nodes(
    kb_slug: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> TaxonomyNodesResponse:
    """List all taxonomy nodes for a KB (flat list, frontend builds tree)."""
    _, org, _ = await _get_caller_org(credentials, db)
    kb = await _get_kb_or_404(kb_slug, org.id, db)

    result = await db.execute(
        select(PortalTaxonomyNode)
        .where(PortalTaxonomyNode.kb_id == kb.id)
        .order_by(PortalTaxonomyNode.sort_order, PortalTaxonomyNode.name)
    )
    nodes = result.scalars().all()
    return TaxonomyNodesResponse(nodes=[_node_out(n) for n in nodes])


@router.post(
    "/{kb_slug}/taxonomy/nodes",
    response_model=TaxonomyNodeOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_taxonomy_node(
    kb_slug: str,
    body: CreateNodeRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> TaxonomyNodeOut:
    """Create a taxonomy node. Requires contributor role."""
    caller_id, org, _ = await _get_caller_org(credentials, db)
    kb = await _get_kb_or_404(kb_slug, org.id, db)
    await _require_role(kb.id, caller_id, db, "contributor")

    # Validate parent exists if specified
    if body.parent_id is not None:
        parent_result = await db.execute(
            select(PortalTaxonomyNode).where(
                PortalTaxonomyNode.id == body.parent_id,
                PortalTaxonomyNode.kb_id == kb.id,
            )
        )
        if not parent_result.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Parent node not found")

    node = PortalTaxonomyNode(
        kb_id=kb.id,
        parent_id=body.parent_id,
        name=body.name.strip(),
        slug=_slugify(body.name),
        created_by=caller_id,
    )
    db.add(node)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A sibling node with this name already exists",
        ) from exc

    await db.commit()
    await db.refresh(node)
    return _node_out(node)


@router.patch("/{kb_slug}/taxonomy/nodes/{node_id}", response_model=TaxonomyNodeOut)
async def update_taxonomy_node(
    kb_slug: str,
    node_id: int,
    body: UpdateNodeRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> TaxonomyNodeOut:
    """Rename or reparent a taxonomy node. Requires contributor role."""
    caller_id, org, _ = await _get_caller_org(credentials, db)
    kb = await _get_kb_or_404(kb_slug, org.id, db)
    await _require_role(kb.id, caller_id, db, "contributor")

    result = await db.execute(
        select(PortalTaxonomyNode).where(
            PortalTaxonomyNode.id == node_id,
            PortalTaxonomyNode.kb_id == kb.id,
        )
    )
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")

    if body.name is not None:
        node.name = body.name.strip()
        node.slug = _slugify(body.name)

    if body.description is not None:
        if len(body.description) > 500:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Description must be at most 500 characters",
            )
        node.description = body.description

    if body.parent_id is not None:
        if body.parent_id == node.id:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Node cannot be its own parent")
        await _check_circular_reference(node.id, body.parent_id, kb.id, db)
        # Validate new parent exists
        parent_result = await db.execute(
            select(PortalTaxonomyNode).where(
                PortalTaxonomyNode.id == body.parent_id,
                PortalTaxonomyNode.kb_id == kb.id,
            )
        )
        if not parent_result.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="New parent node not found")
        node.parent_id = body.parent_id

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A sibling node with this name already exists",
        ) from exc

    await db.refresh(node)
    return _node_out(node)


@router.delete("/{kb_slug}/taxonomy/nodes/{node_id}", status_code=status.HTTP_200_OK)
async def delete_taxonomy_node(
    kb_slug: str,
    node_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Delete a taxonomy node. Reassigns children and docs to parent. Requires owner role."""
    caller_id, org, _ = await _get_caller_org(credentials, db)
    kb = await _get_kb_or_404(kb_slug, org.id, db)
    await _require_role(kb.id, caller_id, db, "owner")

    result = await db.execute(
        select(PortalTaxonomyNode).where(
            PortalTaxonomyNode.id == node_id,
            PortalTaxonomyNode.kb_id == kb.id,
        )
    )
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")

    # Reassign children to the deleted node's parent
    await db.execute(
        update(PortalTaxonomyNode)
        .where(
            PortalTaxonomyNode.parent_id == node_id,
            PortalTaxonomyNode.kb_id == kb.id,
        )
        .values(parent_id=node.parent_id)
    )

    # Update parent doc_count if parent exists
    reassigned_docs = node.doc_count
    if node.parent_id is not None and reassigned_docs > 0:
        parent_result = await db.execute(select(PortalTaxonomyNode).where(PortalTaxonomyNode.id == node.parent_id))
        parent = parent_result.scalar_one_or_none()
        if parent:
            parent.doc_count += reassigned_docs

    await db.delete(node)
    await db.commit()
    return {"reassigned_docs": reassigned_docs}


# -- Taxonomy proposals -------------------------------------------------------


@router.get("/{kb_slug}/taxonomy/proposals", response_model=ProposalsResponse)
async def list_taxonomy_proposals(
    kb_slug: str,
    proposal_status: str = "pending",
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> ProposalsResponse:
    """List taxonomy proposals for a KB, filterable by status."""
    _, org, _ = await _get_caller_org(credentials, db)
    kb = await _get_kb_or_404(kb_slug, org.id, db)

    query = select(PortalTaxonomyProposal).where(PortalTaxonomyProposal.kb_id == kb.id)
    if proposal_status != "all":
        query = query.where(PortalTaxonomyProposal.status == proposal_status)
    query = query.order_by(PortalTaxonomyProposal.created_at.desc())

    result = await db.execute(query)
    proposals = result.scalars().all()
    return ProposalsResponse(proposals=[_proposal_out(p) for p in proposals])


def _require_internal_token(request: Request) -> None:
    """Reject requests without the correct internal shared secret."""
    if not settings.internal_secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Internal API not configured")
    token = request.headers.get("Authorization", "")
    if token != f"Bearer {settings.internal_secret}":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


@router.get(
    "/{kb_slug}/taxonomy/nodes/internal",
    response_model=TaxonomyNodesResponse,
)
async def list_taxonomy_nodes_internal(
    kb_slug: str,
    zitadel_org_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TaxonomyNodesResponse:
    """List taxonomy nodes for a KB. Internal endpoint for knowledge-ingest service.

    Requires ?zitadel_org_id=<bigint> so we can set RLS tenant before querying.
    portal_knowledge_bases and portal_taxonomy_nodes both have strict RLS.
    """
    _require_internal_token(request)

    # portal_orgs has no RLS — safe to query without tenant
    org_result = await db.execute(select(PortalOrg).where(PortalOrg.zitadel_org_id == zitadel_org_id))
    org = org_result.scalar_one_or_none()
    if not org:
        return TaxonomyNodesResponse(nodes=[])

    # Set RLS tenant so subsequent queries on portal_knowledge_bases and
    # portal_taxonomy_nodes are correctly scoped
    await set_tenant(db, org.id)

    result = await db.execute(
        select(PortalKnowledgeBase).where(
            PortalKnowledgeBase.slug == kb_slug,
            PortalKnowledgeBase.org_id == org.id,
        )
    )
    kb = result.scalar_one_or_none()
    if not kb:
        return TaxonomyNodesResponse(nodes=[])

    nodes_result = await db.execute(
        select(PortalTaxonomyNode)
        .where(PortalTaxonomyNode.kb_id == kb.id)
        .order_by(PortalTaxonomyNode.sort_order, PortalTaxonomyNode.name)
    )
    nodes = nodes_result.scalars().all()
    return TaxonomyNodesResponse(nodes=[_node_out(n) for n in nodes])


@router.post(
    "/{kb_slug}/taxonomy/proposals",
    response_model=ProposalOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_proposal(
    kb_slug: str,
    body: CreateProposalRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ProposalOut:
    """Submit a taxonomy proposal. Internal endpoint for knowledge-ingest service."""
    _require_internal_token(request)

    valid_types = {"new_node", "merge", "split", "rename", "tag"}
    if body.proposal_type not in valid_types:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"proposal_type must be one of {valid_types}",
        )

    # Look up KB by slug across all orgs (internal endpoint, no org scoping)
    result = await db.execute(select(PortalKnowledgeBase).where(PortalKnowledgeBase.slug == kb_slug))
    kb = result.scalar_one_or_none()
    if not kb:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found")

    proposal = PortalTaxonomyProposal(
        kb_id=kb.id,
        proposal_type=body.proposal_type,
        title=body.title,
        payload=body.payload,
        confidence_score=body.confidence_score,
    )
    db.add(proposal)
    await db.commit()
    await db.refresh(proposal)
    return _proposal_out(proposal)


async def _execute_proposal_action(
    proposal: PortalTaxonomyProposal,
    kb: PortalKnowledgeBase,
    caller_id: str,
    db: AsyncSession,
) -> PortalTaxonomyNode | None:
    """Execute the DB mutations for a proposal. Returns the new node for new_node proposals."""
    payload = proposal.payload
    new_node: PortalTaxonomyNode | None = None

    if proposal.proposal_type == "new_node":
        parent_id = payload.get("parent_id")
        name = payload.get("name", proposal.title)
        if parent_id is not None:
            parent_check = await db.execute(
                select(PortalTaxonomyNode).where(
                    PortalTaxonomyNode.id == parent_id,
                    PortalTaxonomyNode.kb_id == kb.id,
                )
            )
            if not parent_check.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Referenced parent node does not exist",
                )
        description = payload.get("description")
        new_node = PortalTaxonomyNode(
            kb_id=kb.id,
            parent_id=parent_id,
            name=name,
            slug=_slugify(name),
            description=description[:200] if description else None,
            created_by=caller_id,
        )
        db.add(new_node)

    elif proposal.proposal_type == "merge":
        await _execute_merge(payload, kb, db)

    elif proposal.proposal_type == "split":
        await _execute_split(payload, kb, caller_id, db)

    elif proposal.proposal_type == "rename":
        await _execute_rename(payload, kb, db)

    return new_node


async def _execute_merge(
    payload: dict,
    kb: PortalKnowledgeBase,
    db: AsyncSession,
) -> None:
    source_id = payload.get("source_node_id")
    target_id = payload.get("target_node_id")
    source_result = await db.execute(
        select(PortalTaxonomyNode).where(
            PortalTaxonomyNode.id == source_id, PortalTaxonomyNode.kb_id == kb.id
        )
    )
    source_node = source_result.scalar_one_or_none()
    target_result = await db.execute(
        select(PortalTaxonomyNode).where(
            PortalTaxonomyNode.id == target_id, PortalTaxonomyNode.kb_id == kb.id
        )
    )
    target_node = target_result.scalar_one_or_none()
    if not source_node or not target_node:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Referenced node does not exist")
    await db.execute(
        update(PortalTaxonomyNode).where(PortalTaxonomyNode.parent_id == source_id).values(parent_id=target_id)
    )
    target_node.doc_count += source_node.doc_count
    await db.delete(source_node)


async def _execute_split(
    payload: dict,
    kb: PortalKnowledgeBase,
    caller_id: str,
    db: AsyncSession,
) -> None:
    source_id = payload.get("source_node_id")
    new_children = payload.get("new_children", [])
    source_result = await db.execute(
        select(PortalTaxonomyNode).where(
            PortalTaxonomyNode.id == source_id, PortalTaxonomyNode.kb_id == kb.id
        )
    )
    source_node = source_result.scalar_one_or_none()
    if not source_node:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Referenced node does not exist")
    parent_id = source_node.parent_id
    for child_spec in new_children:
        child_name = child_spec if isinstance(child_spec, str) else child_spec.get("name", "")
        if child_name:
            db.add(PortalTaxonomyNode(
                kb_id=kb.id, parent_id=parent_id, name=child_name,
                slug=_slugify(child_name), created_by=caller_id,
            ))


async def _execute_rename(
    payload: dict,
    kb: PortalKnowledgeBase,
    db: AsyncSession,
) -> None:
    target_node_id = payload.get("node_id")
    new_name = payload.get("new_name", "")
    node_result = await db.execute(
        select(PortalTaxonomyNode).where(
            PortalTaxonomyNode.id == target_node_id, PortalTaxonomyNode.kb_id == kb.id
        )
    )
    node = node_result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Referenced node does not exist")
    node.name = new_name
    node.slug = _slugify(new_name)


@router.post(
    "/{kb_slug}/taxonomy/proposals/{proposal_id}/approve",
    response_model=ProposalOut,
)
async def approve_proposal(
    kb_slug: str,
    proposal_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> ProposalOut:
    """Approve a pending proposal and execute the corresponding action. Requires contributor role."""
    caller_id, org, _ = await _get_caller_org(credentials, db)
    kb = await _get_kb_or_404(kb_slug, org.id, db)
    await _require_role(kb.id, caller_id, db, "contributor")

    result = await db.execute(
        select(PortalTaxonomyProposal).where(
            PortalTaxonomyProposal.id == proposal_id,
            PortalTaxonomyProposal.kb_id == kb.id,
        )
    )
    proposal = result.scalar_one_or_none()
    if not proposal:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proposal not found")
    if proposal.status != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Proposal is not pending")

    _new_node = await _execute_proposal_action(proposal, kb, caller_id, db)

    proposal.status = "approved"
    proposal.reviewed_by = caller_id
    proposal.reviewed_at = datetime.now(tz=UTC)

    # Capture data for post-commit auto-categorise (SPEC-KB-024 R4)
    _cluster_centroid_for_autocategorise: list | None = None
    if _new_node is not None:
        _cluster_centroid_for_autocategorise = proposal.payload.get("cluster_centroid")

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Operation failed due to a naming conflict",
        ) from exc

    await db.refresh(proposal)

    # R4: trigger auto-categorise for documents matching this cluster centroid
    if _new_node is not None and _cluster_centroid_for_autocategorise:
        await db.refresh(_new_node)  # ensure _new_node.id is populated after commit
        _t = asyncio.create_task(
            _trigger_auto_categorise(
                org_id=str(org.zitadel_org_id),
                kb_slug=kb_slug,
                node_id=_new_node.id,
                cluster_centroid=_cluster_centroid_for_autocategorise,
            )
        )
        _background_tasks.add(_t)
        _t.add_done_callback(_background_tasks.discard)

    return _proposal_out(proposal)


@router.post(
    "/{kb_slug}/taxonomy/proposals/{proposal_id}/reject",
    response_model=ProposalOut,
)
async def reject_proposal(
    kb_slug: str,
    proposal_id: int,
    body: RejectRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> ProposalOut:
    """Reject a pending proposal. Requires contributor role."""
    caller_id, org, _ = await _get_caller_org(credentials, db)
    kb = await _get_kb_or_404(kb_slug, org.id, db)
    await _require_role(kb.id, caller_id, db, "contributor")

    result = await db.execute(
        select(PortalTaxonomyProposal).where(
            PortalTaxonomyProposal.id == proposal_id,
            PortalTaxonomyProposal.kb_id == kb.id,
        )
    )
    proposal = result.scalar_one_or_none()
    if not proposal:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proposal not found")
    if proposal.status != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Proposal is not pending")

    proposal.status = "rejected"
    proposal.reviewed_by = caller_id
    proposal.reviewed_at = datetime.now(tz=UTC)
    proposal.rejection_reason = body.reason

    await db.commit()
    await db.refresh(proposal)
    return _proposal_out(proposal)


# -- Bootstrap & backfill triggers -------------------------------------------


@router.post("/{kb_slug}/taxonomy/bootstrap")
async def trigger_bootstrap(
    kb_slug: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Trigger taxonomy bootstrap proposal generation. Requires contributor role.

    Calls knowledge-ingest to scan existing chunks and propose categories.
    Proposals appear in the review queue once generated.
    """
    caller_id, org, _ = await _get_caller_org(credentials, db)
    kb = await _get_kb_or_404(kb_slug, org.id, db)
    await _require_role(kb.id, caller_id, db, "contributor")

    # Resolve Zitadel org_id for the ingest service call
    org_result = await db.execute(select(PortalOrg).where(PortalOrg.id == org.id))
    portal_org = org_result.scalar_one_or_none()
    zitadel_org_id = portal_org.zitadel_org_id if portal_org else str(org.id)

    from app.services.knowledge_ingest_client import trigger_taxonomy_bootstrap

    try:
        result = await trigger_taxonomy_bootstrap(zitadel_org_id, kb_slug)
    except Exception:
        log.exception(
            "taxonomy_bootstrap_failed",
            extra={"org_id": zitadel_org_id, "kb_slug": kb_slug},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not generate taxonomy suggestions",
        ) from None

    return result


@router.post("/{kb_slug}/taxonomy/backfill-trigger")
async def trigger_backfill(
    kb_slug: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Trigger taxonomy backfill to tag all existing chunks. Requires contributor role.

    Enqueues a background job in knowledge-ingest that classifies and tags
    all existing chunks with the approved taxonomy nodes.
    """
    caller_id, org, _ = await _get_caller_org(credentials, db)
    kb = await _get_kb_or_404(kb_slug, org.id, db)
    await _require_role(kb.id, caller_id, db, "contributor")

    org_result = await db.execute(select(PortalOrg).where(PortalOrg.id == org.id))
    portal_org = org_result.scalar_one_or_none()
    zitadel_org_id = portal_org.zitadel_org_id if portal_org else str(org.id)

    from app.services.knowledge_ingest_client import trigger_taxonomy_backfill

    try:
        result = await trigger_taxonomy_backfill(zitadel_org_id, kb_slug)
    except Exception:
        log.exception(
            "taxonomy_backfill_trigger_failed",
            extra={"org_id": zitadel_org_id, "kb_slug": kb_slug},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not trigger taxonomy backfill",
        ) from None

    return result


# -- Coverage stats -----------------------------------------------------------

# 5-minute in-memory cache: key = (org_id_str, kb_slug), value = (monotonic_ts, data_dict)
_coverage_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_COVERAGE_CACHE_TTL = 300.0  # 5 minutes


class CoverageNodeOut(BaseModel):
    taxonomy_node_id: int
    taxonomy_node_name: str
    chunk_count: int
    gap_count: int
    health: str  # "healthy", "attention_needed", "empty"


class CoverageResponse(BaseModel):
    nodes: list[CoverageNodeOut]
    total_chunks: int
    untagged_count: int
    untagged_percentage: float


async def _fetch_ingest_coverage(org_id: str, kb_slug: str) -> dict | None:
    """Fetch coverage stats from knowledge-ingest service.

    Returns parsed JSON dict on success, None on any failure.
    """
    try:
        async with httpx.AsyncClient(
            base_url=settings.knowledge_ingest_url,
            headers={
                "X-Internal-Secret": settings.knowledge_ingest_secret,
                **get_trace_headers(),
            },
            timeout=10.0,
        ) as client:
            resp = await client.get(
                "/ingest/v1/taxonomy/coverage-stats",
                params={"org_id": org_id, "kb_slug": kb_slug},
            )
            resp.raise_for_status()
            return resp.json()
    except Exception:
        log.warning(
            "coverage_stats_ingest_fetch_failed",
            extra={"org_id": org_id, "kb_slug": kb_slug},
        )
        return None


def _make_coverage_response(
    ingest_data: dict,
    gap_counts: dict[int, int],
    node_names: dict[int, str],
) -> CoverageResponse:
    """Merge ingest chunk data with gap counts to build coverage response."""
    total_chunks = ingest_data.get("total_chunks", 0)
    untagged_count = ingest_data.get("untagged_count", 0)
    untagged_pct = round((untagged_count / total_chunks * 100), 2) if total_chunks > 0 else 0.0

    nodes: list[CoverageNodeOut] = []
    for node_data in ingest_data.get("nodes", []):
        nid = node_data["taxonomy_node_id"]
        chunk_count = node_data["chunk_count"]
        gap_count = gap_counts.get(nid, 0)

        if chunk_count == 0:
            health = "empty"
        elif chunk_count < 10 or gap_count >= 5:
            health = "attention_needed"
        else:
            health = "healthy"

        nodes.append(
            CoverageNodeOut(
                taxonomy_node_id=nid,
                taxonomy_node_name=node_names.get(nid, f"Node {nid}"),
                chunk_count=chunk_count,
                gap_count=gap_count,
                health=health,
            )
        )

    return CoverageResponse(
        nodes=nodes,
        total_chunks=total_chunks,
        untagged_count=untagged_count,
        untagged_percentage=untagged_pct,
    )


@router.get("/{kb_slug}/taxonomy/coverage", response_model=CoverageResponse)
async def taxonomy_coverage(
    kb_slug: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> CoverageResponse:
    """Coverage dashboard: per-node chunk counts, gap counts, health status.

    Combines data from knowledge-ingest (Qdrant chunk counts) with portal DB
    (gap counts per taxonomy node). Results cached for 5 minutes.
    """
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)
    kb = await _get_kb_or_404(kb_slug, org.id, db)

    # Resolve Zitadel org_id for the ingest service call
    from app.models.portal import PortalOrg

    org_result = await db.execute(select(PortalOrg).where(PortalOrg.id == org.id))
    portal_org = org_result.scalar_one_or_none()
    zitadel_org_id = portal_org.zitadel_org_id if portal_org else str(org.id)

    # Check cache
    cache_key = (zitadel_org_id, kb_slug)
    cached = _coverage_cache.get(cache_key)
    if cached is not None:
        ts, cached_response = cached
        if time.monotonic() - ts < _COVERAGE_CACHE_TTL:
            return CoverageResponse(**cached_response)

    # Fetch chunk counts from knowledge-ingest
    ingest_data = await _fetch_ingest_coverage(zitadel_org_id, kb_slug)
    if ingest_data is None:
        ingest_data = {"nodes": [], "total_chunks": 0, "untagged_count": 0}

    # Get taxonomy node names from DB
    nodes_result = await db.execute(select(PortalTaxonomyNode).where(PortalTaxonomyNode.kb_id == kb.id))
    nodes = nodes_result.scalars().all()
    node_names = {n.id: n.name for n in nodes}

    # Count open gaps per taxonomy node (last 30 days)
    cutoff = datetime.now(tz=UTC) - timedelta(days=30)
    gaps_result = await db.execute(
        select(PortalRetrievalGap).where(
            PortalRetrievalGap.org_id == org.id,
            PortalRetrievalGap.occurred_at >= cutoff,
            PortalRetrievalGap.resolved_at.is_(None),
            PortalRetrievalGap.taxonomy_node_ids.isnot(None),
        )
    )
    gaps = gaps_result.scalars().all()
    gap_counts: dict[int, int] = {}
    for gap in gaps:
        if gap.taxonomy_node_ids:
            for nid in gap.taxonomy_node_ids:
                gap_counts[nid] = gap_counts.get(nid, 0) + 1

    response = _make_coverage_response(ingest_data, gap_counts, node_names)

    # Cache the response
    _coverage_cache[cache_key] = (time.monotonic(), response.model_dump())

    return response


# -- Top tags -----------------------------------------------------------------

_top_tags_cache: dict[tuple[str, str, int | None], tuple[float, dict]] = {}
_TOP_TAGS_CACHE_TTL = 300.0  # 5 minutes


class TopTagEntryOut(BaseModel):
    tag: str
    count: int


class TopTagsResponse(BaseModel):
    tags: list[TopTagEntryOut]
    total_chunks_sampled: int


async def _fetch_ingest_top_tags(org_id: str, kb_slug: str, limit: int, taxonomy_node_id: int | None) -> dict | None:
    """Fetch top tags from knowledge-ingest service."""
    try:
        params: dict = {"org_id": org_id, "kb_slug": kb_slug, "limit": limit}
        if taxonomy_node_id is not None:
            params["taxonomy_node_id"] = taxonomy_node_id
        async with httpx.AsyncClient(
            base_url=settings.knowledge_ingest_url,
            headers={
                "X-Internal-Secret": settings.knowledge_ingest_secret,
                **get_trace_headers(),
            },
            timeout=25.0,
        ) as client:
            resp = await client.get("/ingest/v1/taxonomy/top-tags", params=params)
            resp.raise_for_status()
            return resp.json()
    except Exception:
        log.warning(
            "top_tags_ingest_fetch_failed",
            extra={"org_id": org_id, "kb_slug": kb_slug},
        )
        return None


@router.get("/{kb_slug}/taxonomy/top-tags", response_model=TopTagsResponse)
async def taxonomy_top_tags(
    kb_slug: str,
    limit: int = 20,
    taxonomy_node_id: int | None = None,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> TopTagsResponse:
    """Top tags by frequency across KB chunks. Cached for 5 minutes.

    Optionally filter by taxonomy_node_id to get tags within a category.
    Accessible to all KB members (viewer+).
    """
    _, org, _ = await _get_caller_org(credentials, db)
    await _get_kb_or_404(kb_slug, org.id, db)

    from app.models.portal import PortalOrg

    org_result = await db.execute(select(PortalOrg).where(PortalOrg.id == org.id))
    portal_org = org_result.scalar_one_or_none()
    zitadel_org_id = portal_org.zitadel_org_id if portal_org else str(org.id)

    cache_key = (zitadel_org_id, kb_slug, taxonomy_node_id)
    cached = _top_tags_cache.get(cache_key)
    if cached is not None:
        ts, cached_response = cached
        if time.monotonic() - ts < _TOP_TAGS_CACHE_TTL:
            return TopTagsResponse(**cached_response)

    data = await _fetch_ingest_top_tags(zitadel_org_id, kb_slug, limit, taxonomy_node_id)
    if data is None:
        data = {"tags": [], "total_chunks_sampled": 0}

    response = TopTagsResponse(
        tags=[TopTagEntryOut(**t) for t in data.get("tags", [])],
        total_chunks_sampled=data.get("total_chunks_sampled", 0),
    )

    _top_tags_cache[cache_key] = (time.monotonic(), response.model_dump())
    return response
