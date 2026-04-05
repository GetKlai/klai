"""Taxonomy API for knowledge base categorisation and proposal review."""

import logging
import re
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import _get_caller_org, bearer
from app.core.config import settings
from app.core.database import get_db
from app.models.knowledge_bases import PortalKnowledgeBase
from app.models.taxonomy import PortalTaxonomyNode, PortalTaxonomyProposal
from app.services.access import get_user_role_for_kb

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/app/knowledge-bases", tags=["taxonomy"])


# -- Pydantic schemas ---------------------------------------------------------


class TaxonomyNodeOut(BaseModel):
    id: int
    kb_id: int
    parent_id: int | None
    name: str
    slug: str
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
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TaxonomyNodesResponse:
    """List taxonomy nodes for a KB. Internal endpoint for knowledge-ingest service.

    Uses X-Internal-Token auth (Authorization: Bearer <internal_secret>).
    Lookup by kb_slug only — Zitadel org_id (bigint) cannot be compared to
    portal's internal org_id (postgres integer).
    """
    _require_internal_token(request)

    result = await db.execute(select(PortalKnowledgeBase).where(PortalKnowledgeBase.slug == kb_slug))
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

    valid_types = {"new_node", "merge", "split", "rename"}
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

    payload = proposal.payload

    # Execute type-specific logic
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
        node = PortalTaxonomyNode(
            kb_id=kb.id,
            parent_id=parent_id,
            name=name,
            slug=_slugify(name),
            created_by=caller_id,
        )
        db.add(node)

    elif proposal.proposal_type == "merge":
        source_id = payload.get("source_node_id")
        target_id = payload.get("target_node_id")
        source_result = await db.execute(
            select(PortalTaxonomyNode).where(
                PortalTaxonomyNode.id == source_id,
                PortalTaxonomyNode.kb_id == kb.id,
            )
        )
        source_node = source_result.scalar_one_or_none()
        target_result = await db.execute(
            select(PortalTaxonomyNode).where(
                PortalTaxonomyNode.id == target_id,
                PortalTaxonomyNode.kb_id == kb.id,
            )
        )
        target_node = target_result.scalar_one_or_none()
        if not source_node or not target_node:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Referenced node does not exist",
            )
        # Move children from source to target
        await db.execute(
            update(PortalTaxonomyNode).where(PortalTaxonomyNode.parent_id == source_id).values(parent_id=target_id)
        )
        # Transfer doc_count
        target_node.doc_count += source_node.doc_count
        await db.delete(source_node)

    elif proposal.proposal_type == "split":
        source_id = payload.get("source_node_id")
        new_children = payload.get("new_children", [])
        source_result = await db.execute(
            select(PortalTaxonomyNode).where(
                PortalTaxonomyNode.id == source_id,
                PortalTaxonomyNode.kb_id == kb.id,
            )
        )
        source_node = source_result.scalar_one_or_none()
        if not source_node:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Referenced node does not exist",
            )
        parent_id = source_node.parent_id
        for child_spec in new_children:
            child_name = child_spec if isinstance(child_spec, str) else child_spec.get("name", "")
            if child_name:
                db.add(
                    PortalTaxonomyNode(
                        kb_id=kb.id,
                        parent_id=parent_id,
                        name=child_name,
                        slug=_slugify(child_name),
                        created_by=caller_id,
                    )
                )

    elif proposal.proposal_type == "rename":
        target_node_id = payload.get("node_id")
        new_name = payload.get("new_name", "")
        node_result = await db.execute(
            select(PortalTaxonomyNode).where(
                PortalTaxonomyNode.id == target_node_id,
                PortalTaxonomyNode.kb_id == kb.id,
            )
        )
        node = node_result.scalar_one_or_none()
        if not node:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Referenced node does not exist",
            )
        node.name = new_name
        node.slug = _slugify(new_name)

    proposal.status = "approved"
    proposal.reviewed_by = caller_id
    proposal.reviewed_at = datetime.now(tz=UTC)

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Operation failed due to a naming conflict",
        ) from exc

    await db.refresh(proposal)
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
