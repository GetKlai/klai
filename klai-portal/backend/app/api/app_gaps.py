"""App-level gap dashboard API."""

from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_capability
from app.core.database import get_db
from app.core.permissions import UserPermissions, get_caller
from app.core.profiles import Capability
from app.models.retrieval_gaps import PortalRetrievalGap
from app.models.taxonomy import PortalTaxonomyNode
from app.models.widgets import WidgetConversation

router = APIRouter(
    prefix="/api/app",
    tags=["gaps"],
    # R-X2 / AC-3: all gap endpoints require the kb.gaps capability.
    dependencies=[Depends(require_capability(Capability.KB_GAPS))],
)

# caller_client_id of the answer-review producer; a group containing one of its
# rows is review-sourced rather than telemetry-only.
_REVIEW_CALLER_CLIENT_ID = "human-review"


class GapOut(BaseModel):
    query_text: str
    gap_type: str
    language: str | None = None
    # "review" = a human already saw this question (answer-review flow);
    # "automatic" = telemetry only.
    source: str
    # Conversation the newest row of the group came from, NULL when the gap
    # has no conversation or that conversation no longer exists.
    conversation_id: int | None = None
    top_score: float | None
    nearest_kb_slug: str | None
    occurrence_count: int
    last_occurred: datetime
    resolved_at: datetime | None = None


class GapsResponse(BaseModel):
    gaps: list[GapOut]
    total: int


class GapResolveRequest(BaseModel):
    """Identifies one gap group; ``language=None`` means the group whose rows
    carry no language, not "any language"."""

    query_text: str
    gap_type: Literal["hard", "soft"]
    language: str | None = None


class GapResolveResponse(BaseModel):
    resolved: int


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
    language: str | None = Query(default=None),
    taxonomy_node_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    include_resolved: bool = Query(default=False),
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> GapsResponse:
    """List gap events for the caller's org, grouped by query text + language.

    Optional taxonomy_node_id filter: only return gaps classified to that node.
    Optional language filter (SPEC-KNOWLEDGE-ACTIVITY-001 §4.5): language is
    part of the grouping key, because "missing in nl" and "missing in en" are
    two different things to write.
    """
    cutoff = datetime.now(tz=UTC) - timedelta(days=days)

    stmt = (
        select(
            PortalRetrievalGap.query_text,
            PortalRetrievalGap.gap_type,
            PortalRetrievalGap.language,
            func.max(PortalRetrievalGap.top_score).label("top_score"),
            func.max(PortalRetrievalGap.nearest_kb_slug).label("nearest_kb_slug"),
            func.count().label("occurrence_count"),
            func.max(PortalRetrievalGap.occurred_at).label("last_occurred"),
            func.max(PortalRetrievalGap.resolved_at).label("resolved_at"),
            func.bool_or(PortalRetrievalGap.caller_client_id == _REVIEW_CALLER_CLIENT_ID).label("has_review"),
        )
        .where(
            PortalRetrievalGap.org_id == perms.org_id,
            PortalRetrievalGap.occurred_at >= cutoff,
        )
        .group_by(PortalRetrievalGap.query_text, PortalRetrievalGap.gap_type, PortalRetrievalGap.language)
        .order_by(func.count().desc())
        .limit(limit)
    )
    if gap_type:
        stmt = stmt.where(PortalRetrievalGap.gap_type == gap_type)
    if language:
        stmt = stmt.where(PortalRetrievalGap.language == language)
    if not include_resolved:
        stmt = stmt.where(PortalRetrievalGap.resolved_at.is_(None))
    # SPEC-KB-022 R7: filter by taxonomy node
    if taxonomy_node_id is not None:
        stmt = stmt.where(PortalRetrievalGap.taxonomy_node_ids.contains([taxonomy_node_id]))

    result = await db.execute(stmt)
    rows = result.all()
    if not rows:
        return GapsResponse(gaps=[], total=0)

    # §4.5: link each group to the conversation its newest row came from. A
    # per-row value cannot ride along in the grouped query, so pick the newest
    # one here. The JOIN doubles as the existence check — a conversation the
    # retention job purged (or a row written before the FK landed) must not be
    # linked. Filters are deliberately not mirrored: rows only ever land on
    # their own group key, and closing a gap must not hide its provenance.
    conv_result = await db.execute(
        select(
            PortalRetrievalGap.query_text,
            PortalRetrievalGap.gap_type,
            PortalRetrievalGap.language,
            PortalRetrievalGap.conversation_id,
        )
        .join(WidgetConversation, WidgetConversation.id == PortalRetrievalGap.conversation_id)
        .where(
            PortalRetrievalGap.org_id == perms.org_id,
            PortalRetrievalGap.occurred_at >= cutoff,
            PortalRetrievalGap.conversation_id.isnot(None),
            PortalRetrievalGap.query_text.in_({r.query_text for r in rows}),
        )
        # DISTINCT ON keeps one row per group in PostgreSQL instead of streaming
        # every occurrence of a frequent question to pick the newest here.
        .distinct(PortalRetrievalGap.query_text, PortalRetrievalGap.gap_type, PortalRetrievalGap.language)
        .order_by(
            PortalRetrievalGap.query_text,
            PortalRetrievalGap.gap_type,
            PortalRetrievalGap.language,
            PortalRetrievalGap.occurred_at.desc(),
            PortalRetrievalGap.id.desc(),
        )
    )
    conversation_by_group: dict[tuple[str, str, str | None], int] = {}
    for row in conv_result.all():
        # Rows arrive newest-first, so the first hit per group is the one.
        conversation_by_group.setdefault((row.query_text, row.gap_type, row.language), row.conversation_id)

    gaps = [
        GapOut(
            query_text=r.query_text,
            gap_type=r.gap_type,
            language=r.language,
            source="review" if r.has_review else "automatic",
            conversation_id=conversation_by_group.get((r.query_text, r.gap_type, r.language)),
            top_score=r.top_score,
            nearest_kb_slug=r.nearest_kb_slug,
            occurrence_count=r.occurrence_count,
            last_occurred=r.last_occurred,
            resolved_at=r.resolved_at,
        )
        for r in rows
    ]
    return GapsResponse(gaps=gaps, total=len(gaps))


@router.post("/gaps/resolve", response_model=GapResolveResponse)
async def resolve_gap(
    body: GapResolveRequest,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> GapResolveResponse:
    """Close one gap group by hand (SPEC-KNOWLEDGE-ACTIVITY-001 §4.9).

    The rescorer is not the only closer: whoever wrote the missing page (or
    decided the question is out of scope) needs to take it off the list now.
    Stamps ``resolved_at`` on every open row of the group in the caller's org,
    so it leaves the default open list. 404 when nothing open matches — which
    includes another org's group, since the org predicate excludes it (and RLS
    Category-D bounds the statement to that org anyway).
    """
    stmt = (
        update(PortalRetrievalGap)
        .where(
            PortalRetrievalGap.org_id == perms.org_id,
            PortalRetrievalGap.query_text == body.query_text,
            PortalRetrievalGap.gap_type == body.gap_type,
            PortalRetrievalGap.resolved_at.is_(None),
        )
        .values(resolved_at=datetime.now(tz=UTC))
    )
    # None means "the group without a language", not "any language" — same key
    # the GET grouping used to show it.
    if body.language is None:
        stmt = stmt.where(PortalRetrievalGap.language.is_(None))
    else:
        stmt = stmt.where(PortalRetrievalGap.language == body.language)

    result = await db.execute(stmt)
    resolved = result.rowcount or 0  # type: ignore[attr-defined]
    if resolved == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No open gap group matches this query text, gap type and language",
        )
    await db.commit()
    return GapResolveResponse(resolved=resolved)


@router.get("/gaps/summary", response_model=GapSummaryResponse)
async def get_gap_summary(
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> GapSummaryResponse:
    """Return gap summary stats for the caller's org (last 7 days)."""
    cutoff = datetime.now(tz=UTC) - timedelta(days=7)

    count_result = await db.execute(
        select(
            PortalRetrievalGap.gap_type,
            func.count().label("cnt"),
        )
        .where(
            PortalRetrievalGap.org_id == perms.org_id,
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
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> GapsByTaxonomyResponse:
    """Aggregate open gaps per taxonomy node.

    Returns per-node: open_gaps count, frequency_per_day, priority level.
    Priority: >= 2.0/day = high, >= 0.5/day = medium, < 0.5/day = low.
    Sorted by open_gaps descending.
    """
    cutoff = datetime.now(tz=UTC) - timedelta(days=days)

    # Get all open gaps with taxonomy_node_ids in the time window
    gaps_result = await db.execute(
        select(PortalRetrievalGap).where(
            PortalRetrievalGap.org_id == perms.org_id,
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
