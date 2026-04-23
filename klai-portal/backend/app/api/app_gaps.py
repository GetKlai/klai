"""App-level gap dashboard API (admin-only)."""

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import _get_caller_org, _require_admin, bearer, require_capability
from app.core.database import get_db
from app.models.retrieval_gaps import PortalRetrievalGap
from app.models.taxonomy import PortalTaxonomyNode

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/app",
    tags=["gaps"],
    # R-X2 / AC-3: all gap endpoints require the kb.gaps capability.
    dependencies=[Depends(require_capability("kb.gaps"))],
)


class GapOut(BaseModel):
    query_text: str
    gap_type: str
    top_score: float | None
    nearest_kb_slug: str | None
    occurrence_count: int
    last_occurred: datetime
    resolved_at: datetime | None = None


class GapsResponse(BaseModel):
    gaps: list[GapOut]
    total: int


class GapSummaryResponse(BaseModel):
    total_7d: int
    hard_7d: int
    soft_7d: int


class GapByTaxonomyOut(BaseModel):
    taxonomy_node_id: int
    taxonomy_node_name: str
    open_gaps: int
    frequency_per_day: float
    priority: str  # "high", "medium", "low"


class GapsByTaxonomyResponse(BaseModel):
    items: list[GapByTaxonomyOut]


@router.get("/gaps", response_model=GapsResponse)
async def list_gaps(
    days: int = Query(default=30, ge=1, le=90),
    gap_type: str | None = Query(default=None),
    taxonomy_node_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    include_resolved: bool = Query(default=False),
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> GapsResponse:
    """List gap events for the caller's org, grouped by query text.

    Optional taxonomy_node_id filter: only return gaps classified to that node.
    """
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)
    cutoff = datetime.now(tz=UTC) - timedelta(days=days)

    stmt = (
        select(
            PortalRetrievalGap.query_text,
            PortalRetrievalGap.gap_type,
            func.max(PortalRetrievalGap.top_score).label("top_score"),
            func.max(PortalRetrievalGap.nearest_kb_slug).label("nearest_kb_slug"),
            func.count().label("occurrence_count"),
            func.max(PortalRetrievalGap.occurred_at).label("last_occurred"),
            func.max(PortalRetrievalGap.resolved_at).label("resolved_at"),
        )
        .where(
            PortalRetrievalGap.org_id == org.id,
            PortalRetrievalGap.occurred_at >= cutoff,
        )
        .group_by(PortalRetrievalGap.query_text, PortalRetrievalGap.gap_type)
        .order_by(func.count().desc())
        .limit(limit)
    )
    if gap_type:
        stmt = stmt.where(PortalRetrievalGap.gap_type == gap_type)
    if not include_resolved:
        stmt = stmt.where(PortalRetrievalGap.resolved_at.is_(None))
    # SPEC-KB-022 R7: filter by taxonomy node
    if taxonomy_node_id is not None:
        stmt = stmt.where(PortalRetrievalGap.taxonomy_node_ids.contains([taxonomy_node_id]))

    result = await db.execute(stmt)
    rows = result.all()
    gaps = [
        GapOut(
            query_text=r.query_text,
            gap_type=r.gap_type,
            top_score=r.top_score,
            nearest_kb_slug=r.nearest_kb_slug,
            occurrence_count=r.occurrence_count,
            last_occurred=r.last_occurred,
            resolved_at=r.resolved_at,
        )
        for r in rows
    ]
    return GapsResponse(gaps=gaps, total=len(gaps))


@router.get("/gaps/summary", response_model=GapSummaryResponse)
async def get_gap_summary(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> GapSummaryResponse:
    """Return gap summary stats for the caller's org (last 7 days)."""
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)
    cutoff = datetime.now(tz=UTC) - timedelta(days=7)

    count_result = await db.execute(
        select(
            PortalRetrievalGap.gap_type,
            func.count().label("cnt"),
        )
        .where(
            PortalRetrievalGap.org_id == org.id,
            PortalRetrievalGap.occurred_at >= cutoff,
            PortalRetrievalGap.resolved_at.is_(None),  # only open gaps
        )
        .group_by(PortalRetrievalGap.gap_type)
    )
    counts = {row.gap_type: row.cnt for row in count_result}

    return GapSummaryResponse(
        total_7d=counts.get("hard", 0) + counts.get("soft", 0),
        hard_7d=counts.get("hard", 0),
        soft_7d=counts.get("soft", 0),
    )


@router.get("/gaps/by-taxonomy", response_model=GapsByTaxonomyResponse)
async def get_gaps_by_taxonomy(
    days: int = Query(default=30, ge=1, le=90),
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> GapsByTaxonomyResponse:
    """Aggregate open gaps per taxonomy node.

    Returns per-node: open_gaps count, frequency_per_day, priority level.
    Priority: >= 2.0/day = high, >= 0.5/day = medium, < 0.5/day = low.
    Sorted by open_gaps descending.
    """
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)
    cutoff = datetime.now(tz=UTC) - timedelta(days=days)

    # Get all open gaps with taxonomy_node_ids in the time window
    gaps_result = await db.execute(
        select(PortalRetrievalGap).where(
            PortalRetrievalGap.org_id == org.id,
            PortalRetrievalGap.occurred_at >= cutoff,
            PortalRetrievalGap.resolved_at.is_(None),
            PortalRetrievalGap.taxonomy_node_ids.isnot(None),
        )
    )
    gaps = gaps_result.scalars().all()

    # Count gaps per node (a gap can belong to multiple nodes)
    node_counts: dict[int, int] = {}
    for gap in gaps:
        if gap.taxonomy_node_ids:
            for nid in gap.taxonomy_node_ids:
                node_counts[nid] = node_counts.get(nid, 0) + 1

    if not node_counts:
        return GapsByTaxonomyResponse(items=[])

    # Fetch node names
    node_ids = list(node_counts.keys())
    nodes_result = await db.execute(select(PortalTaxonomyNode).where(PortalTaxonomyNode.id.in_(node_ids)))
    nodes_by_id = {n.id: n for n in nodes_result.scalars().all()}

    # Build response
    items: list[GapByTaxonomyOut] = []
    for nid, count in node_counts.items():
        node = nodes_by_id.get(nid)
        if not node:
            continue
        # Build full name path (parent > child)
        name = node.name
        if node.parent_id and node.parent_id in nodes_by_id:
            name = f"{nodes_by_id[node.parent_id].name} > {node.name}"

        freq = count / max(days, 1)
        if freq >= 2.0:
            priority = "high"
        elif freq >= 0.5:
            priority = "medium"
        else:
            priority = "low"

        items.append(
            GapByTaxonomyOut(
                taxonomy_node_id=nid,
                taxonomy_node_name=name,
                open_gaps=count,
                frequency_per_day=round(freq, 2),
                priority=priority,
            )
        )

    # Sort by open_gaps descending
    items.sort(key=lambda x: x.open_gaps, reverse=True)
    return GapsByTaxonomyResponse(items=items)
