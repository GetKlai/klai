"""Support-case detail and gap summaries (the kb.gaps capability gate)."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_kb_with_access, require_capability
from app.core.database import get_db
from app.core.permissions import UserPermissions, get_caller, require_platform_unlocked
from app.core.profiles import Capability
from app.models.portal import PortalOrg
from app.models.retrieval_gaps import PortalRetrievalGap
from app.models.support_cases import PortalSupportCase
from app.models.taxonomy import PortalTaxonomyNode
from app.services.access import is_personal_kb
from app.services.support_case_reviews import REFERENCE_KEY, compute_analysis_revision, reviews_for_current_revision

router = APIRouter(
    prefix="/api/app",
    tags=["gaps"],
    # R-X2 / AC-3: all gap endpoints require the kb.gaps capability, and the
    # per-tenant knowledge_gaps unlock while the screen is unfinished.
    dependencies=[
        Depends(require_capability(Capability.KB_GAPS)),
        Depends(require_platform_unlocked("knowledge_gaps")),
    ],
)


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


class SupportCaseDetailOut(BaseModel):
    id: int
    kb_slug: str
    payload: dict
    status: str
    # Each finding is the raw analysis dict with a ``review`` key added: the
    # current-revision human verdict (carrying its ``corrected_diagnosis``), or
    # None. NULL analysis stays NULL.
    analysis: list | None = None
    analysis_version: str | None = None
    # Identifies the exact analysis shown, so a review PATCH can be rejected when
    # it was made against a superseded analysis. Present for every case (a null
    # analysis hashes as an empty list) so a failed/pending case stays retryable.
    analysis_revision: str | None = None
    # The evidence version, so a reference PUT can be rejected when it was written
    # against superseded evidence.
    content_hash: str
    # The human case-level reference, surfaced only while it still matches the
    # current evidence (a later change makes it stale and it drops to None).
    reference: dict | None = None
    imported_at: datetime


@router.get("/gaps/support-cases/{case_id}", response_model=SupportCaseDetailOut)
async def get_support_case(
    case_id: int,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> SupportCaseDetailOut:
    """Return one imported case's evidence and analysis (SPEC-RAG-SUPPORT-GAP).

    Access-checked: bounded to the caller's org (explicit predicate plus Cat-D
    RLS). Telemetry policy is re-checked on read — a tenant that downgraded away
    from ``full`` cannot view literal support evidence even for a case imported
    while it still could, and the purge removes those rows shortly after.
    """
    org = await db.get(PortalOrg, perms.org_id)
    if org is None or org.telemetry_level != "full":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "telemetry_level_forbids_support_evidence"},
        )
    result = await db.execute(
        select(PortalSupportCase).where(
            PortalSupportCase.id == case_id,
            PortalSupportCase.org_id == perms.org_id,
        )
    )
    case = result.scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Support case not found")
    # Shared contract: evidence is gated by tenant/KB access, not org+policy
    # alone. Resolve the case's comparison-scope KB through the same firewall
    # every KB route uses (existence + personal-firewall), and require it to be
    # org-owned, as import does. Raises 404 for a missing / personal / cross-org
    # KB. No new ACL — existing KB_GAPS capability + KB access apply.
    kb = await get_kb_with_access(case.kb_slug, perms, db)
    if is_personal_kb(kb):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Support case not found")

    # A revision is computed for EVERY case, even one with a null analysis (it
    # hashes as an empty list): a failed or pending case is retryable and role-
    # correctable, so the reviewer needs a revision to act on. The finding review
    # itself still requires analyzed findings, gated separately. Lay the human
    # review over each finding without persisting the enriched shape back — the
    # revision hashes the raw analysis, and only the current-revision review shows.
    revision = compute_analysis_revision(
        content_hash=case.content_hash, analysis_version=case.analysis_version, analysis=case.analysis
    )
    analysis_out: list | None = None
    if case.analysis is not None:
        reviews = reviews_for_current_revision(case.reviews, revision, len(case.analysis))
        analysis_out = [{**finding, "review": reviews[i]} for i, finding in enumerate(case.analysis)]

    # Surface the case-level human reference only while it still matches the
    # current evidence; a reference written against superseded evidence is not gold
    # for what is shown now.
    stored_reference = (case.reviews or {}).get(REFERENCE_KEY)
    reference = (
        stored_reference
        if isinstance(stored_reference, dict) and stored_reference.get("content_hash") == case.content_hash
        else None
    )

    return SupportCaseDetailOut(
        id=case.id,
        kb_slug=case.kb_slug,
        payload=case.payload,
        status=case.status,
        analysis=analysis_out,
        analysis_version=case.analysis_version,
        analysis_revision=revision,
        content_hash=case.content_hash,
        reference=reference,
        imported_at=case.imported_at,
    )


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
