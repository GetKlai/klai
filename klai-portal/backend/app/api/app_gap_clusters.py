"""Knowledge-gap clusters: the weekly list from stored chat signals, with a proposal and a final decision per cluster.

Phase 1 of docs/architecture/knowledge-gap-to-article.md. The list itself
makes no model call (app/services/knowledge_gap_report.py); a proposal is one
klai-judge call, made only when the owner asks for it.
"""

from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_capability
from app.core.database import get_db
from app.core.permissions import UserPermissions, get_caller, require_platform_unlocked
from app.core.profiles import Capability
from app.models.gap_proposals import PortalGapProposal
from app.models.portal import PortalOrg, PortalUser
from app.services.gap_proposals import MAX_EXAMPLE_CHARS, PROPOSAL_MODEL, ProposalDraft, draft_proposal
from app.services.knowledge_gap_report import knowledge_gap_clusters

router = APIRouter(
    prefix="/api/app",
    tags=["gaps"],
    # Same gate as the gap inbox (app_gaps.py): the kb.gaps capability and the
    # per-tenant knowledge_gaps unlock.
    dependencies=[
        Depends(require_capability(Capability.KB_GAPS)),
        Depends(require_platform_unlocked("knowledge_gaps")),
    ],
)

# The longest period the list shows, and the period a proposal or a decision
# looks its cluster up in: one window, so a cluster the list shows is always
# found by the actions on it.
CLUSTER_WINDOW_DAYS = 30
# Model calls per org per rolling 7 days, failed ones included. Per org, not per
# knowledge base: the stored chat signals name the articles a turn found, not
# their knowledge base.
PROPOSAL_CAP = 20
# Model calls per cluster: the first and one retry after a failure.
MAX_ATTEMPTS = 2
# A reservation older than this is a call that died with its process; it may be retried.
_STALE_GENERATING = timedelta(minutes=10)
_ALREADY_DECIDED = "This gap cluster is already decided."
_GENERATING = "A proposal for this gap cluster is already being generated."
_NOT_DECIDABLE = (
    "This cluster collects turns without a topic (or internal turns without text), so it cannot be decided "
    "or get a proposal."
)


class GapArticleOut(BaseModel):
    title: str | None
    url: str | None
    score: float | None


class GapClusterOut(BaseModel):
    cluster_key: str
    product: str | None
    topic: str
    count: int
    widget_count: int
    internal_count: int
    kind: Literal["no_article", "article_misses_part", "variant_unclear", "contact_wish", "weak_match"]
    kinds: dict[str, int]
    question: str | None
    questions: list[str]
    article: GapArticleOut | None
    articles: list[GapArticleOut]
    open_parts: list[str]
    language: str | None
    last_seen: datetime
    decidable: bool
    decision: Literal["none", "rejected", "out_of_scope"]
    decided_by: str | None
    decided_at: datetime | None
    has_proposal: bool


class GapClustersResponse(BaseModel):
    days: int
    clusters: list[GapClusterOut]


class GapProposalOut(BaseModel):
    id: int
    cluster_key: str
    missing: str
    where_kind: Literal["existing", "new"]
    where_title: str
    where_url: str | None
    where_section: str | None
    example_text: str
    open_facts: list[str]
    language: str | None
    model: str
    generated_at: datetime
    decision: Literal["none", "rejected", "out_of_scope"]
    decided_by: str | None
    decided_at: datetime | None


class GapDecisionRequest(BaseModel):
    decision: Literal["rejected", "out_of_scope"]


class GapDecisionOut(BaseModel):
    cluster_key: str
    decision: Literal["rejected", "out_of_scope"]
    decided_by: str | None
    decided_at: datetime


@router.get("/gaps/clusters", response_model=GapClustersResponse)
async def list_gap_clusters(
    days: int = Query(default=7, ge=1, le=CLUSTER_WINDOW_DAYS),
    include_decided: bool = Query(default=False),
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> GapClustersResponse:
    """The org's knowledge-gap clusters over ``days``; decided clusters only with ``include_decided``."""
    clusters = await knowledge_gap_clusters(db, org_id=perms.org_id, days=days, include_decided=include_decided)
    return GapClustersResponse(days=days, clusters=[GapClusterOut.model_validate(c) for c in clusters])


async def _decidable_cluster_or_error(db: AsyncSession, org_id: int, cluster_key: str) -> dict:
    """The cluster in the caller's org, decided or not: 404 for a key the org does not have (another org's
    included), 409 for a catch-all cluster that may not be decided or proposed on."""
    clusters = await knowledge_gap_clusters(db, org_id=org_id, days=CLUSTER_WINDOW_DAYS, include_decided=True)
    cluster = next((c for c in clusters if c["cluster_key"] == cluster_key), None)
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gap cluster not found")
    if not cluster["decidable"]:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_NOT_DECIDABLE)
    return cluster


async def _proposal_row(db: AsyncSession, org_id: int, cluster_key: str) -> tuple[PortalGapProposal | None, str | None]:
    row = (
        await db.execute(
            select(PortalGapProposal, func.coalesce(PortalUser.display_name, PortalUser.email))
            .outerjoin(PortalUser, (PortalUser.id == PortalGapProposal.decided_by) & (PortalUser.org_id == org_id))
            .where(PortalGapProposal.org_id == org_id, PortalGapProposal.cluster_key == cluster_key)
        )
    ).one_or_none()
    return (row[0], row[1]) if row is not None else (None, None)


def _proposal_out(proposal: PortalGapProposal, decided_by: str | None) -> GapProposalOut:
    return GapProposalOut.model_validate(
        {
            "id": proposal.id,
            "cluster_key": proposal.cluster_key,
            "missing": proposal.missing,
            "where_kind": proposal.where_kind,
            "where_title": proposal.where_title,
            "where_url": proposal.where_url,
            "where_section": proposal.where_section,
            "example_text": proposal.example_text,
            "open_facts": proposal.open_facts,
            "language": proposal.language,
            "model": proposal.model,
            "generated_at": proposal.generated_at,
            "decision": proposal.decision,
            "decided_by": decided_by,
            "decided_at": proposal.decided_at,
        }
    )


@router.get("/gaps/clusters/{cluster_key:path}/proposal", response_model=GapProposalOut)
async def get_gap_proposal(
    cluster_key: str,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> GapProposalOut:
    """The stored proposal of one cluster; 404 when none is ready (a decision alone is no proposal)."""
    proposal, decided_by = await _proposal_row(db, perms.org_id, cluster_key)
    if proposal is None or proposal.status != "ready":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No proposal for this gap cluster")
    return _proposal_out(proposal, decided_by)


def _rejection(draft: ProposalDraft | None, articles: list[dict]) -> tuple[str | None, str | None]:
    """Why the model's output cannot be stored (or None), and the URL of the article it named."""
    if draft is None:
        return "The proposal could not be generated.", None
    if len(draft.example_text) > MAX_EXAMPLE_CHARS:
        return f"The generated example text is longer than {MAX_EXAMPLE_CHARS} characters.", None
    if draft.where_kind == "new":
        return None, None

    def norm(value: object) -> str:
        return " ".join(str(value or "").split()).casefold()

    # The link comes from the articles the cluster found, never from the model.
    named = next((a for a in articles if norm(a["title"]) == norm(draft.where_title)), None)
    if named is None:
        return "The proposal named an article this cluster did not find.", None
    return None, named["url"]


async def _reserve(db: AsyncSession, org_id: int, cluster_key: str, proposal: PortalGapProposal | None) -> int:
    """Claim the one model call for this cluster before it is made; returns the row id, or raises 409.

    A first call inserts the row as 'generating' (a concurrent first call hits
    the unique (org_id, cluster_key) and gets 409); a retry flips a failed or
    stale row back, conditionally, so of two concurrent retries one wins.
    Committed before the model call, so the cap and the other request see it.
    """
    now = datetime.now(tz=UTC)
    if proposal is None:
        row = PortalGapProposal(
            org_id=org_id, cluster_key=cluster_key, status="generating", attempts=1, attempted_at=now, decision="none"
        )
        db.add(row)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_GENERATING) from None
        await db.commit()
        return row.id
    result = await db.execute(
        update(PortalGapProposal)
        .where(
            PortalGapProposal.id == proposal.id,
            PortalGapProposal.decision == "none",
            PortalGapProposal.attempts < MAX_ATTEMPTS,
            or_(
                PortalGapProposal.status == "failed",
                (PortalGapProposal.status == "generating") & (PortalGapProposal.attempted_at < now - _STALE_GENERATING),
            ),
        )
        .values(status="generating", attempts=PortalGapProposal.attempts + 1, attempted_at=now, failure_reason=None)
    )
    if not result.rowcount:  # type: ignore[attr-defined]
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_GENERATING)
    await db.commit()
    return proposal.id


@router.post("/gaps/clusters/{cluster_key:path}/proposal", response_model=GapProposalOut)
async def create_gap_proposal(
    cluster_key: str,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> GapProposalOut:
    """Generate the cluster's proposal with one klai-judge call, or return the stored one.

    Refused (409) below telemetry ``full``, where the customers' words may not
    be used literally, for a catch-all cluster, while another call runs, and
    after MAX_ATTEMPTS failed calls. Capped at PROPOSAL_CAP model calls per org
    per rolling 7 days, failed calls included (429): the call is reserved and
    counted before it is made.
    """
    proposal, decided_by = await _proposal_row(db, perms.org_id, cluster_key)
    if proposal is not None and proposal.status == "ready":
        return _proposal_out(proposal, decided_by)
    if proposal is not None and proposal.decision != "none":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_ALREADY_DECIDED)
    if proposal is not None and proposal.attempts >= MAX_ATTEMPTS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Generating a proposal for this gap cluster failed {MAX_ATTEMPTS} times.",
        )
    org = (
        await db.execute(
            select(PortalOrg.telemetry_level, PortalOrg.zitadel_org_id).where(PortalOrg.id == perms.org_id)
        )
    ).one()
    if org.telemetry_level != "full":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Proposals need telemetry level 'full': below it the customers' questions may not be used.",
        )
    cluster = await _decidable_cluster_or_error(db, perms.org_id, cluster_key)
    if not cluster["questions"] and not cluster["open_parts"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="This gap cluster has no questions to write a proposal from."
        )
    calls = (
        await db.execute(
            select(func.coalesce(func.sum(PortalGapProposal.attempts), 0)).where(
                PortalGapProposal.org_id == perms.org_id,
                PortalGapProposal.attempted_at >= datetime.now(tz=UTC) - timedelta(days=7),
            )
        )
    ).scalar_one()
    if calls >= PROPOSAL_CAP:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"The limit of {PROPOSAL_CAP} proposals per 7 days is reached ({calls} in the last 7 days).",
        )
    row_id = await _reserve(db, perms.org_id, cluster_key, proposal)

    draft = await draft_proposal(
        questions=cluster["questions"],
        open_parts=cluster["open_parts"],
        articles=cluster["articles"],
        language=cluster["language"],
        delegated_org_id=org.zitadel_org_id,
    )
    reason, where_url = _rejection(draft, cluster["articles"])
    if reason is not None or draft is None:
        await db.execute(
            update(PortalGapProposal)
            .where(PortalGapProposal.id == row_id)
            .values(status="failed", failure_reason=reason)
        )
        await db.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"{reason} Try again once.")

    generated_at = datetime.now(tz=UTC)
    await db.execute(
        update(PortalGapProposal)
        .where(PortalGapProposal.id == row_id)
        .values(
            status="ready",
            missing=draft.missing,
            where_kind=draft.where_kind,
            where_title=draft.where_title,
            where_url=where_url,
            where_section=draft.where_section or None,
            example_text=draft.example_text,
            open_facts=draft.open_facts,
            language=cluster["language"],
            model=PROPOSAL_MODEL,
            generated_at=generated_at,
        )
    )
    await db.commit()
    stored, decided_by = await _proposal_row(db, perms.org_id, cluster_key)
    if stored is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gap cluster not found")
    return _proposal_out(stored, decided_by)


@router.post("/gaps/clusters/{cluster_key:path}/decision", response_model=GapDecisionOut)
async def decide_gap_cluster(
    cluster_key: str,
    body: GapDecisionRequest,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> GapDecisionOut:
    """Reject a cluster or mark it out of scope. Final: a second decision is 409, and the cluster leaves the list.

    A catch-all cluster (no topic words, or internal turns) is 409: a final
    decision on it would hide every future turn of that kind.
    """
    await _decidable_cluster_or_error(db, perms.org_id, cluster_key)
    caller = (
        await db.execute(
            select(PortalUser.id, func.coalesce(PortalUser.display_name, PortalUser.email)).where(
                PortalUser.zitadel_user_id == perms.user_id, PortalUser.org_id == perms.org_id
            )
        )
    ).one_or_none()
    decided_at = datetime.now(tz=UTC)
    values = {"decision": body.decision, "decided_by": caller[0] if caller else None, "decided_at": decided_at}
    proposal, _ = await _proposal_row(db, perms.org_id, cluster_key)
    if proposal is None:
        db.add(PortalGapProposal(org_id=perms.org_id, cluster_key=cluster_key, **values))
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_ALREADY_DECIDED) from None
    else:
        # Conditional, so of two concurrent decisions exactly one lands.
        result = await db.execute(
            update(PortalGapProposal)
            .where(PortalGapProposal.id == proposal.id, PortalGapProposal.decision == "none")
            .values(**values)
        )
        if not result.rowcount:  # type: ignore[attr-defined]
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_ALREADY_DECIDED)
    await db.commit()
    return GapDecisionOut(
        cluster_key=cluster_key,
        decision=body.decision,
        decided_by=caller[1] if caller else None,
        decided_at=decided_at,
    )
