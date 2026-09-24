"""App-level gap dashboard API."""

from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import case, distinct, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_kb_with_access, require_capability
from app.core.database import get_db
from app.core.permissions import UserPermissions, get_caller, require_platform_unlocked
from app.core.profiles import Capability
from app.models.knowledge_bases import PortalKnowledgeBase
from app.models.portal import PortalOrg, PortalUser
from app.models.retrieval_gaps import PortalRetrievalGap
from app.models.support_cases import PortalSupportCase
from app.models.taxonomy import PortalTaxonomyNode
from app.models.widgets import WidgetConversation
from app.services.access import is_personal_kb
from app.services.gap_events import JUDGE_CALLER_CLIENT_ID, REVIEW_CALLER_CLIENT_ID, shows_unmet_need
from app.services.support_case_reviews import REFERENCE_KEY, compute_analysis_revision, reviews_for_current_revision
from app.services.support_cases import _question_key

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


class GapTopic(BaseModel):
    id: int
    name: str


class GapOut(BaseModel):
    query_text: str
    gap_type: str
    language: str | None = None
    # "support" = a case-backed content finding; "review" = a human already saw
    # this question (answer-review flow); "judge" = the conversation judge found
    # the knowledge to blame; "automatic" = telemetry only.
    source: str
    # Conversation the newest row of the group came from, NULL when the gap
    # has no conversation or that conversation no longer exists.
    conversation_id: int | None = None
    top_score: float | None
    nearest_kb_slug: str | None
    occurrence_count: int
    last_occurred: datetime
    resolved_at: datetime | None = None
    # From the newest resolved row of the group; null for an open group.
    resolved_by: str | None = None
    resolved_by_name: str | None = None
    # SPEC-RAG-SUPPORT-GAP: defaulted for legacy telemetry groups, populated for
    # case-backed support groups. ``support_case_ids`` is the set of distinct
    # cases behind the group; its length is the unique-case frequency.
    diagnosis: str | None = None
    audience: str | None = None
    support_case_ids: list[int] = []
    support_sources: list[str] = []
    # The group's identity key: the persisted ``question_key`` when the row(s)
    # have one (every producer writes one now — SPEC-RAG-GAP-GROUPING), else
    # the row's own literal ``query_text`` for a pre-migration row. The
    # reliable handle to close the group with ``/gaps/resolve``, since folded
    # findings share one key while their wording differs.
    group_key: str | None = None
    topic: GapTopic | None = None


class GapsResponse(BaseModel):
    gaps: list[GapOut]
    total: int


class GapResolveRequest(BaseModel):
    """Identifies one gap group; ``language=None`` means the group whose rows
    carry no language, not "any language".

    For a support (case-backed) group the UI also submits ``diagnosis`` (and
    optionally ``audience`` / ``nearest_kb_slug``): a manual close must target
    exactly that diagnosis+KB group and cannot close an unrelated one. When
    ``diagnosis`` is absent the close only ever touches legacy telemetry rows
    (``support_case_id IS NULL``), so it never silently closes a support row.

    ``group_key`` is the authoritative way to close a support group: findings
    folded together share one persisted key even though their wording differs, so
    recomputing the key from a displayed question would miss them. When present it
    matches that key directly.
    """

    query_text: str
    gap_type: Literal["hard", "soft", "content"]
    language: str | None = None
    diagnosis: str | None = None
    audience: str | None = None
    nearest_kb_slug: str | None = None
    group_key: str | None = None


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
    """List gap events for the caller's org, grouped by customer need + language
    (SPEC-RAG-GAP-GROUPING: the persisted ``question_key`` when a row has one,
    falling back to its literal query text for a pre-migration row).

    Optional taxonomy_node_id filter: only return gaps classified to that node.
    Optional language filter (SPEC-KNOWLEDGE-ACTIVITY-001 §4.5): language is
    part of the grouping key, because "missing in nl" and "missing in en" are
    two different things to write.
    """
    cutoff = datetime.now(tz=UTC) - timedelta(days=days)

    # SPEC-RAG-GAP-GROUPING: group on the persisted ``question_key`` — the
    # normalized-text need identifier every producer now writes — falling back
    # to the row's own literal ``query_text`` when it has none (a row written
    # before this shipped). The fallback exactly reproduces the old
    # exact-tuple grouping for those rows, so nothing regroups on deploy; a new
    # row's key can later be folded onto an earlier paraphrase's key by the
    # grouping judge (SPEC-RAG-GAP-GROUPING), at which point they share one
    # group here too. gap_type stays a separate axis on purpose (a hard vs
    # soft retrieval miss is a different signal, even about the same need).
    group_key_expr = func.coalesce(PortalRetrievalGap.question_key, PortalRetrievalGap.query_text)

    stmt = (
        select(
            group_key_expr.label("group_key"),
            func.max(PortalRetrievalGap.query_text).label("query_text"),
            PortalRetrievalGap.gap_type,
            PortalRetrievalGap.language,
            func.max(PortalRetrievalGap.top_score).label("top_score"),
            func.max(PortalRetrievalGap.nearest_kb_slug).label("nearest_kb_slug"),
            func.count().label("occurrence_count"),
            func.max(PortalRetrievalGap.occurred_at).label("last_occurred"),
            # Closed only when every occurrence is closed: a reopened question
            # must keep its close action in the mixed list.
            case(
                (func.bool_and(PortalRetrievalGap.resolved_at.isnot(None)), func.max(PortalRetrievalGap.resolved_at)),
                else_=None,
            ).label("resolved_at"),
            func.bool_or(PortalRetrievalGap.caller_client_id == REVIEW_CALLER_CLIENT_ID).label("has_review"),
            func.bool_or(PortalRetrievalGap.caller_client_id == JUDGE_CALLER_CLIENT_ID).label("has_judge"),
            # Part of the group key, so one value per group.
            func.max(PortalRetrievalGap.audience).label("audience"),
        )
        .where(
            PortalRetrievalGap.org_id == perms.org_id,
            PortalRetrievalGap.occurred_at >= cutoff,
            # Case-backed findings are grouped separately below (by question_key
            # with unique-case frequency); keep them out of the legacy
            # query_text grouping so the two never mix.
            PortalRetrievalGap.support_case_id.is_(None),
            # Only rows that show the visitor went unhelped are inbox items. On
            # the first tenant measured, low-score and redacted rows made up
            # most open rows, including the largest groups. They stay stored;
            # they only leave this list.
            shows_unmet_need(),
        )
        .group_by(group_key_expr, PortalRetrievalGap.gap_type, PortalRetrievalGap.language)
        .order_by(
            func.count().desc(),
            func.max(PortalRetrievalGap.occurred_at).desc(),
        )
        .limit(limit)
    )
    if gap_type:
        stmt = stmt.where(PortalRetrievalGap.gap_type == gap_type)
    if language:
        stmt = stmt.where(PortalRetrievalGap.language == language)
    if not include_resolved:
        stmt = stmt.where(PortalRetrievalGap.resolved_at.is_(None))
    if taxonomy_node_id is not None:
        stmt = stmt.where(PortalRetrievalGap.taxonomy_node_ids.contains([taxonomy_node_id]))

    result = await db.execute(stmt)
    rows = result.all()
    if not rows:
        # No legacy telemetry groups, but the org may still have case-backed
        # support findings — fetch those before returning empty.
        support_only = await _list_support_gaps(
            perms=perms,
            db=db,
            cutoff=cutoff,
            gap_type=gap_type,
            language=language,
            include_resolved=include_resolved,
            limit=limit,
            taxonomy_node_id=taxonomy_node_id,
        )
        return GapsResponse(gaps=support_only, total=len(support_only))

    # §4.5: link each group to the conversation its newest row came from. A
    # per-row value cannot ride along in the grouped query, so pick the newest
    # one here. The JOIN doubles as the existence check — a conversation the
    # retention job purged (or a row written before the FK landed) must not be
    # linked. Filters are deliberately not mirrored: rows only ever land on
    # their own group key, and closing a gap must not hide its provenance.
    conv_result = await db.execute(
        select(
            group_key_expr.label("group_key"),
            PortalRetrievalGap.gap_type,
            PortalRetrievalGap.language,
            PortalRetrievalGap.conversation_id,
        )
        .join(WidgetConversation, WidgetConversation.id == PortalRetrievalGap.conversation_id)
        .where(
            PortalRetrievalGap.org_id == perms.org_id,
            PortalRetrievalGap.occurred_at >= cutoff,
            PortalRetrievalGap.conversation_id.isnot(None),
            group_key_expr.in_({r.group_key for r in rows}),
        )
        # DISTINCT ON keeps one row per group in PostgreSQL instead of streaming
        # every occurrence of a frequent question to pick the newest here.
        .distinct(group_key_expr, PortalRetrievalGap.gap_type, PortalRetrievalGap.language)
        .order_by(
            group_key_expr,
            PortalRetrievalGap.gap_type,
            PortalRetrievalGap.language,
            PortalRetrievalGap.occurred_at.desc(),
            PortalRetrievalGap.id.desc(),
        )
    )
    conversation_by_group: dict[tuple[str, str, str | None], int] = {}
    for row in conv_result.all():
        # Rows arrive newest-first, so the first hit per group is the one.
        conversation_by_group.setdefault((row.group_key, row.gap_type, row.language), row.conversation_id)

    # Who closed it: only asked for when closed rows are actually in view —
    # an open-only list never has a resolved row to attribute. Same
    # newest-row-per-group pick as the conversation link above, but ordered
    # by resolved_at (the group's occurred_at winner need not be the row that
    # closed it).
    resolved_by_group: dict[tuple[str, str, str | None], tuple[str | None, str | None]] = {}
    if include_resolved:
        resolved_result = await db.execute(
            select(
                group_key_expr.label("group_key"),
                PortalRetrievalGap.gap_type,
                PortalRetrievalGap.language,
                PortalRetrievalGap.resolved_by,
                func.coalesce(PortalUser.display_name, PortalUser.email).label("resolved_by_name"),
            )
            .outerjoin(
                PortalUser,
                (PortalUser.id == PortalRetrievalGap.resolved_by_user_id) & (PortalUser.org_id == perms.org_id),
            )
            .where(
                PortalRetrievalGap.org_id == perms.org_id,
                PortalRetrievalGap.occurred_at >= cutoff,
                PortalRetrievalGap.resolved_at.isnot(None),
                group_key_expr.in_({r.group_key for r in rows}),
            )
            .distinct(group_key_expr, PortalRetrievalGap.gap_type, PortalRetrievalGap.language)
            .order_by(
                group_key_expr,
                PortalRetrievalGap.gap_type,
                PortalRetrievalGap.language,
                PortalRetrievalGap.resolved_at.desc(),
                PortalRetrievalGap.id.desc(),
            )
        )
        for row in resolved_result.all():
            resolved_by_group.setdefault(
                (row.group_key, row.gap_type, row.language), (row.resolved_by, row.resolved_by_name)
            )

    gaps = [
        GapOut(
            query_text=r.query_text,
            gap_type=r.gap_type,
            language=r.language,
            source="review" if r.has_review else "judge" if r.has_judge else "automatic",
            audience=r.audience,
            conversation_id=conversation_by_group.get((r.group_key, r.gap_type, r.language)),
            top_score=r.top_score,
            nearest_kb_slug=r.nearest_kb_slug,
            occurrence_count=r.occurrence_count,
            last_occurred=r.last_occurred,
            resolved_at=r.resolved_at,
            group_key=r.group_key,
            # A reopened group is open: its old closer must not travel along.
            resolved_by=(
                resolved_by_group.get((r.group_key, r.gap_type, r.language), (None, None))[0]
                if r.resolved_at is not None
                else None
            ),
            resolved_by_name=(
                resolved_by_group.get((r.group_key, r.gap_type, r.language), (None, None))[1]
                if r.resolved_at is not None
                else None
            ),
        )
        for r in rows
    ]

    gaps.extend(
        await _list_support_gaps(
            perms=perms,
            db=db,
            cutoff=cutoff,
            gap_type=gap_type,
            language=language,
            include_resolved=include_resolved,
            limit=limit,
            taxonomy_node_id=taxonomy_node_id,
        )
    )
    gaps.sort(
        key=lambda g: (
            -g.occurrence_count,
            -g.last_occurred.timestamp(),
            g.group_key or "",
        )
    )
    return GapsResponse(gaps=gaps, total=len(gaps))


async def _list_support_gaps(
    *,
    perms: UserPermissions,
    db: AsyncSession,
    cutoff: datetime,
    gap_type: str | None,
    language: str | None,
    include_resolved: bool,
    limit: int,
    taxonomy_node_id: int | None = None,
) -> list[GapOut]:
    """Case-backed findings, grouped by ``question_key`` with UNIQUE-CASE
    frequency (SPEC-RAG-SUPPORT-GAP).

    ``question_key`` already encodes normalized question + diagnosis + language
    + KB + audience, so equivalent questions across cases collapse into one
    group while different diagnoses/KBs/audiences stay distinct. Frequency is
    ``COUNT(DISTINCT support_case_id)`` — one case counts once no matter how
    many times it was imported. Rows are only visible to a ``full``-telemetry
    org, so a downgraded tenant sees no support evidence here.
    """
    telemetry_level = (
        await db.execute(select(PortalOrg.telemetry_level).where(PortalOrg.id == perms.org_id))
    ).scalar_one_or_none()
    if telemetry_level != "full":
        return []

    stmt = (
        select(
            PortalRetrievalGap.question_key,
            func.max(PortalRetrievalGap.query_text).label("query_text"),
            func.max(PortalRetrievalGap.gap_type).label("gap_type"),
            func.max(PortalRetrievalGap.language).label("language"),
            func.max(PortalRetrievalGap.diagnosis).label("diagnosis"),
            func.max(PortalRetrievalGap.audience).label("audience"),
            func.max(PortalRetrievalGap.nearest_kb_slug).label("nearest_kb_slug"),
            func.max(PortalRetrievalGap.top_score).label("top_score"),
            func.count(distinct(PortalRetrievalGap.support_case_id)).label("occurrence_count"),
            func.array_agg(distinct(PortalRetrievalGap.support_case_id)).label("support_case_ids"),
            func.array_agg(distinct(PortalSupportCase.source)).label("support_sources"),
            func.max(PortalRetrievalGap.occurred_at).label("last_occurred"),
            case(
                (func.bool_and(PortalRetrievalGap.resolved_at.isnot(None)), func.max(PortalRetrievalGap.resolved_at)),
                else_=None,
            ).label("resolved_at"),
        )
        .join(
            PortalSupportCase,
            (PortalSupportCase.id == PortalRetrievalGap.support_case_id) & (PortalSupportCase.org_id == perms.org_id),
        )
        .where(
            PortalRetrievalGap.org_id == perms.org_id,
            PortalRetrievalGap.occurred_at >= cutoff,
            PortalRetrievalGap.support_case_id.isnot(None),
        )
        .group_by(PortalRetrievalGap.question_key)
        .order_by(
            func.count(distinct(PortalRetrievalGap.support_case_id)).desc(),
            func.max(PortalRetrievalGap.occurred_at).desc(),
            PortalRetrievalGap.question_key,
        )
        .limit(limit)
    )
    if gap_type:
        stmt = stmt.where(PortalRetrievalGap.gap_type == gap_type)
    if language:
        stmt = stmt.where(PortalRetrievalGap.language == language)
    if not include_resolved:
        stmt = stmt.where(PortalRetrievalGap.resolved_at.is_(None))
    # SPEC-KB-022 R7: the taxonomy filter now covers support findings too, since
    # they carry classified node ids. Without this a filtered view silently
    # dropped every case-backed group regardless of its topic.
    if taxonomy_node_id is not None:
        stmt = stmt.where(PortalRetrievalGap.taxonomy_node_ids.contains([taxonomy_node_id]))

    result = await db.execute(stmt)
    gaps = [
        GapOut(
            query_text=r.query_text,
            gap_type=r.gap_type,
            language=r.language,
            source="support",
            conversation_id=None,
            top_score=r.top_score,
            nearest_kb_slug=r.nearest_kb_slug,
            occurrence_count=r.occurrence_count,
            last_occurred=r.last_occurred,
            resolved_at=r.resolved_at,
            diagnosis=r.diagnosis,
            audience=r.audience,
            support_case_ids=sorted(cid for cid in (r.support_case_ids or []) if cid is not None),
            support_sources=sorted(r.support_sources or []),
            group_key=r.question_key,
        )
        for r in result.all()
    ]

    keys = [g.group_key for g in gaps if g.group_key is not None]
    if keys:
        topic_rows = (
            await db.execute(
                select(
                    PortalRetrievalGap.question_key,
                    PortalTaxonomyNode.id,
                    PortalTaxonomyNode.name,
                )
                .join(
                    PortalKnowledgeBase,
                    (PortalKnowledgeBase.org_id == perms.org_id)
                    & (PortalKnowledgeBase.slug == PortalRetrievalGap.nearest_kb_slug),
                )
                .join(
                    PortalTaxonomyNode,
                    (PortalTaxonomyNode.kb_id == PortalKnowledgeBase.id)
                    & (PortalTaxonomyNode.id == func.any(PortalRetrievalGap.taxonomy_node_ids)),
                )
                .where(
                    PortalRetrievalGap.org_id == perms.org_id,
                    PortalRetrievalGap.support_case_id.isnot(None),
                    PortalRetrievalGap.question_key.in_(keys),
                )
                .distinct(PortalRetrievalGap.question_key)
                .order_by(
                    PortalRetrievalGap.question_key,
                    func.array_position(PortalRetrievalGap.taxonomy_node_ids, PortalTaxonomyNode.id),
                    PortalTaxonomyNode.id,
                )
            )
        ).all()
        topic_by_key = {tr.question_key: GapTopic(id=tr.id, name=tr.name) for tr in topic_rows}
        for g in gaps:
            if g.group_key is not None:
                g.topic = topic_by_key.get(g.group_key)

    return gaps


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
    caller_id = (
        await db.execute(
            select(PortalUser.id).where(
                PortalUser.zitadel_user_id == perms.user_id,
                PortalUser.org_id == perms.org_id,
            )
        )
    ).scalar_one_or_none()

    stmt = (
        update(PortalRetrievalGap)
        .where(
            PortalRetrievalGap.org_id == perms.org_id,
            PortalRetrievalGap.gap_type == body.gap_type,
            PortalRetrievalGap.resolved_at.is_(None),
        )
        .values(resolved_at=datetime.now(tz=UTC), resolved_by="manual", resolved_by_user_id=caller_id)
    )

    # SPEC-RAG-GAP-GROUPING: the persisted group key closes a group
    # authoritatively — regardless of producer, folded findings share one key
    # while their wording differs, so it matches them all where recomputing
    # from a displayed question would not. COALESCE mirrors ``list_gaps``'s
    # grouping expression so a pre-migration row (no question_key yet, its
    # group_key is its own literal query_text) still closes correctly.
    if body.group_key is not None:
        stmt = stmt.where(
            func.coalesce(PortalRetrievalGap.question_key, PortalRetrievalGap.query_text) == body.group_key,
        )
    # A diagnosis marks a support (case-backed) close. Without a key or a
    # diagnosis, the close only ever touches legacy telemetry rows (exact query
    # text), so it can never silently close a case-backed row.
    elif body.diagnosis is None:
        stmt = stmt.where(
            PortalRetrievalGap.support_case_id.is_(None),
            PortalRetrievalGap.query_text == body.query_text,
        )
        # None means "the group without a language", not "any language".
        if body.language is None:
            stmt = stmt.where(PortalRetrievalGap.language.is_(None))
        else:
            stmt = stmt.where(PortalRetrievalGap.language == body.language)
    else:
        # A support group is identified by its persisted question_key (the same
        # normalized question + language + KB + audience the inbox grouped on;
        # diagnosis no longer separates it), so every spelling variant AND every
        # diagnosis in the group closes together. The KB selector is required —
        # omitting it must not close every KB's group — and the audience is
        # matched deliberately (NULL matches NULL via the empty-string slot in
        # the key).
        if not body.nearest_kb_slug:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="nearest_kb_slug is required to close a support finding group",
            )
        key = _question_key(
            question=body.query_text,
            language=body.language,
            kb_slug=body.nearest_kb_slug,
            audience=body.audience,
        )
        stmt = stmt.where(
            PortalRetrievalGap.support_case_id.isnot(None),
            PortalRetrievalGap.question_key == key,
        )

    result = await db.execute(stmt)
    resolved = result.rowcount or 0  # type: ignore[attr-defined]
    if resolved == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No open gap group matches this query text, gap type and language",
        )
    await db.commit()
    return GapResolveResponse(resolved=resolved)


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
