"""Knowledge-side activity surface — SPEC-KNOWLEDGE-ACTIVITY-001 Appendix A.

Org-scoped conversation review for kb_manager and up: the filtered list plus
worklist, one transcript with its retrieval signals, judge verdict and
reviews, filing/withdrawing an answer review, and the queue badge.

The admin surface under ``/api/admin/widgets`` stays admin-only and untouched.
This router answers the same domain for the knowledge profile, scoped through
``perms.org_id`` on the tenant session exactly like ``app_gaps.py``.

Per-conversation aggregates (turn bands, ratings, refusals, judge verdict,
review state) come from SQL; the worklist predicate, the ``worst`` order and
the ``queue-count`` badge then run over one shared implementation and cannot
drift apart. The scan is bounded by the ``days`` window and
``_WINDOW_SCAN_CAP`` conversations per request.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_capability
from app.core.database import get_db
from app.core.permissions import ProfileRole, UserPermissions, get_caller, require_platform_unlocked
from app.core.profiles import PROFILE_RANK, Capability
from app.models.answer_reviews import AnswerReview

router = APIRouter(
    prefix="/api/app/activity",
    tags=["Knowledge Activity"],
    # Appendix A: the knowledge capability plus both platform unlocks.
    dependencies=[
        Depends(require_capability(Capability.KB_ACTIVITY)),
        Depends(require_platform_unlocked("widgets")),
        Depends(require_platform_unlocked("knowledge_activity")),
    ],
)

_WINDOW_SCAN_CAP = 2000
# ``widget_conversations`` only ever holds webchat turns; the LibreChat side of
# the review surface is a later phase, which is why ``channel`` has one value.
_WEBCHAT: Literal["webchat"] = "webchat"
# low < unknown < medium < high (Appendix A, ``worst_band``).
_BAND_BY_RANK: dict[int, str] = {0: "low", 1: "unknown", 2: "medium", 3: "high"}
_BAND_RANK: dict[str, int] = {band: rank for rank, band in _BAND_BY_RANK.items()}
_VERDICT_SEVERITY: dict[str, int] = {"wrong": 0, "incomplete": 1, "not_a_fault": 2, "correct": 3}
# What ``sort=worst`` ranks behind a thumbsDown.
_UNRESOLVED_JUDGE_OUTCOMES = frozenset({"unresolved", "partially_resolved"})

# ---------------------------------------------------------------------------
# Queries — conversation columns follow app/api/admin_widgets.py
# ---------------------------------------------------------------------------

_WIDGET_IN_ORG_SQL = """
SELECT 1
  FROM widgets
 WHERE id = CAST(:widget_id AS uuid)
   AND org_id = :org_id
"""

_WINDOW_SQL = """
SELECT c.id, c.widget_id, w.name AS widget_name, c.started_at, c.last_message_at,
       c.message_count, c.first_user_query, c.language_detected
  FROM widget_conversations c
  JOIN widgets w ON w.id = c.widget_id
 WHERE c.org_id = :org_id
   AND c.is_preview = false
   AND c.started_at >= :cutoff
   {clauses}
 ORDER BY c.started_at DESC
 LIMIT :scan_cap
"""

_TURNS_SQL = """
SELECT m.conversation_id,
       COUNT(*) FILTER (WHERE m.rating = 'thumbsUp') AS ratings_up,
       COUNT(*) FILTER (WHERE m.rating = 'thumbsDown') AS ratings_down,
       MIN(CASE m.answer_signals ->> 'band'
               WHEN 'low' THEN 0 WHEN 'unknown' THEN 1
               WHEN 'medium' THEN 2 WHEN 'high' THEN 3 END) AS worst_band_rank,
       COUNT(*) FILTER (WHERE COALESCE((m.answer_signals ->> 'refused')::boolean, false)) AS refused_turns
  FROM widget_messages m
 WHERE m.conversation_id = ANY(:ids)
   AND m.org_id = :org_id
   AND m.role = 'assistant'
 GROUP BY m.conversation_id
"""

# Nightly judge verdicts (SPEC-CHAT-QUALITY-LOOP-001 REQ-2); no row means the
# conversation has not been seen yet and `judge` stays null.
_JUDGES_SQL = """
SELECT conversation_id, outcome, failure_category, confidence
  FROM conversation_quality_judgments
 WHERE conversation_id = ANY(:ids)
"""

_REVIEWS_SQL = """
SELECT conversation_id, message_id, verdict, cause
  FROM answer_reviews
 WHERE conversation_id = ANY(:ids)
   AND org_id = :org_id
"""

_CONVERSATION_SQL = """
SELECT c.id, c.widget_id, w.name AS widget_name, c.started_at, c.language_detected,
       c.visitor_name, c.visitor_email
  FROM widget_conversations c
  JOIN widgets w ON w.id = c.widget_id
 WHERE c.id = :conversation_id
   AND c.org_id = :org_id
   AND c.is_preview = false
"""

_MESSAGES_SQL = """
SELECT id, role, content, sources, created_at, sequence, rating, answer_signals
  FROM widget_messages
 WHERE conversation_id = :conversation_id
 ORDER BY sequence ASC
"""

_MESSAGE_REVIEWS_SQL = """
SELECT r.message_id, r.verdict, r.cause, r.note, r.kb_slug,
       COALESCE(p.display_name, p.email) AS reviewer_name, r.reviewed_at
  FROM answer_reviews r
  LEFT JOIN portal_users p ON p.id = r.reviewer_user_id
 WHERE r.message_id = ANY(:message_ids)
   AND r.org_id = :org_id
"""

_JUDGE_DETAIL_SQL = """
SELECT outcome, failure_category, reasoning, confidence, suggested_action, judged_at
  FROM conversation_quality_judgments
 WHERE conversation_id = :conversation_id
"""

_JUDGE_SNAPSHOT_SQL = """
SELECT outcome, failure_category
  FROM conversation_quality_judgments
 WHERE conversation_id = :conversation_id
"""

_MESSAGE_PROBE_SQL = """
SELECT m.id, m.conversation_id, m.role, m.sequence, m.answer_signals,
       wc.org_id, wc.language_detected, wc.is_preview
  FROM widget_messages m
  JOIN widget_conversations wc ON wc.id = m.conversation_id
 WHERE m.id = :message_id
"""

_CALLER_SQL = """
SELECT id, COALESCE(display_name, email) AS display_name
  FROM portal_users
 WHERE zitadel_user_id = :user_id
   AND org_id = :org_id
"""

_DELETE_REVIEW_SQL = """
DELETE FROM answer_reviews
 WHERE message_id = :message_id
   AND org_id = :org_id
"""

# Columns a re-review overwrites; everything else on the row is immutable
# context or a snapshot taken when the review was filed.
_REVIEW_OVERWRITTEN = (
    "verdict",
    "cause",
    "note",
    "kb_slug",
    "reviewer_user_id",
    "band_at_review",
    "judge_outcome_at_review",
    "judge_failure_category_at_review",
    "language",
)


# ---------------------------------------------------------------------------
# Response shapes — field names are the Appendix A contract
# ---------------------------------------------------------------------------


class JudgeOut(BaseModel):
    outcome: str
    failure_category: str | None = None
    confidence: str | None = None


class RatingsOut(BaseModel):
    up: int
    down: int


class ReviewSummaryOut(BaseModel):
    status: Literal["unreviewed", "reviewed"]
    worst_verdict: str | None = None
    causes: list[str] = Field(default_factory=list)


class ConversationListItemOut(BaseModel):
    id: int
    widget_id: str
    widget_name: str | None
    channel: str
    started_at: datetime
    last_message_at: datetime
    message_count: int
    first_user_query: str | None
    language: str | None
    worst_band: str | None
    judge: JudgeOut | None
    ratings: RatingsOut
    review: ReviewSummaryOut
    # Phase 2 fills this; the frontend already renders the slot.
    open_gap_count: int = 0


class ConversationListResponse(BaseModel):
    items: list[ConversationListItemOut]
    next_cursor: str | None


class VisitorOut(BaseModel):
    name: str | None
    email: str | None


class QualityOut(BaseModel):
    outcome: str
    failure_category: str | None = None
    reasoning: str | None = None
    confidence: str | None = None
    suggested_action: str | None = None
    judged_at: datetime | None = None


class ReviewOut(BaseModel):
    verdict: str
    cause: str
    note: str | None = None
    kb_slug: str | None = None
    reviewer_name: str | None = None
    reviewed_at: datetime | None = None


class MessageOut(BaseModel):
    id: int
    role: Literal["user", "assistant"]
    content: str
    sequence: int
    created_at: datetime
    sources: list[dict] | None = None
    rating: str | None = None
    answer_signals: dict | None = None
    review: ReviewOut | None = None


class ConversationDetailOut(BaseModel):
    id: int
    widget_id: str
    widget_name: str | None
    channel: str
    started_at: datetime
    language: str | None
    visitor: VisitorOut | None = None
    quality: QualityOut | None = None
    messages: list[MessageOut] = Field(default_factory=list)


class ReviewRequest(BaseModel):
    verdict: Literal["correct", "incomplete", "wrong", "not_a_fault"]
    cause: Literal["knowledge_missing", "knowledge_wrong", "behaviour", "none"]
    note: str | None = None
    kb_slug: str | None = None

    @model_validator(mode="after")
    def _cause_matches_verdict(self) -> ReviewRequest:
        # Mirrors ck_answer_reviews_cause_matches_verdict so a bad combination
        # is a 422 here instead of a IntegrityError on commit.
        if self.verdict in ("correct", "not_a_fault") and self.cause != "none":
            raise ValueError("cause must be 'none' for verdict 'correct' and 'not_a_fault'")
        if self.verdict in ("incomplete", "wrong") and self.cause == "none":
            raise ValueError("verdict 'incomplete' and 'wrong' need a cause other than 'none'")
        return self


class QueueCountOut(BaseModel):
    count: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_cursor(cursor: str | None) -> datetime | None:
    if not cursor:
        return None
    try:
        return datetime.fromisoformat(cursor.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid cursor") from exc


def _may_see_visitor(perms: UserPermissions) -> bool:
    """Visitor contact details are admin-or-higher only (Appendix A)."""
    if perms.is_platform_admin:
        return True
    return PROFILE_RANK.get(perms.effective_role, -1) >= PROFILE_RANK[ProfileRole.ADMIN]


def _review_summary(review_rows: list[Any]) -> ReviewSummaryOut:
    if not review_rows:
        return ReviewSummaryOut(status="unreviewed")
    worst = min(review_rows, key=lambda r: _VERDICT_SEVERITY.get(r.verdict, len(_VERDICT_SEVERITY)))
    return ReviewSummaryOut(
        status="reviewed",
        worst_verdict=worst.verdict,
        causes=sorted({r.cause for r in review_rows if r.cause != "none"}),
    )


class _Candidate:
    """One conversation plus the one queue input that has no contract field."""

    def __init__(self, item: ConversationListItemOut, refused_turns: int) -> None:
        self.item = item
        self.refused_turns = refused_turns


def _in_queue(candidate: _Candidate) -> bool:
    """Worklist predicate (Appendix A): unreviewed AND (judge outcome is not
    resolved, or a thumbsDown, or a turn band low/unknown, or a refused turn)."""
    item = candidate.item
    if item.review.status == "reviewed":
        return False
    if item.judge is None or item.judge.outcome != "resolved":
        return True
    if item.ratings.down or candidate.refused_turns:
        return True
    return item.worst_band in ("low", "unknown")


def _matches(
    candidate: _Candidate,
    *,
    judge_outcomes: list[str],
    failure_categories: list[str],
    review_status: str | None,
    causes: list[str],
    bands: list[str],
    rating: str | None,
) -> bool:
    item = candidate.item
    if judge_outcomes and (item.judge is None or item.judge.outcome not in judge_outcomes):
        return False
    if failure_categories and (item.judge is None or item.judge.failure_category not in failure_categories):
        return False
    if review_status and item.review.status != review_status:
        return False
    if causes and not set(causes).intersection(item.review.causes):
        return False
    if bands and (item.worst_band is None or item.worst_band not in bands):
        return False
    if rating == "thumbsUp" and not item.ratings.up:
        return False
    if rating == "thumbsDown" and not item.ratings.down:
        return False
    if rating == "none" and (item.ratings.up or item.ratings.down):
        return False
    return True


def _worst_sort_key(candidate: _Candidate) -> tuple[int, int, int, float]:
    item = candidate.item
    judge_unresolved = item.judge is not None and item.judge.outcome in _UNRESOLVED_JUDGE_OUTCOMES
    return (
        0 if item.ratings.down else 1,
        0 if judge_unresolved else 1,
        _BAND_RANK.get(item.worst_band or "", len(_BAND_BY_RANK)),
        -item.started_at.timestamp(),
    )


async def _load_candidates(
    db: AsyncSession,
    *,
    org_id: int,
    cutoff: datetime,
    cursor: datetime | None,
    widget_id: str | None,
    language: str | None,
    channel: str,
) -> list[_Candidate]:
    """Every conversation in the window with its aggregates, newest first."""
    params: dict[str, object] = {"org_id": org_id, "cutoff": cutoff, "scan_cap": _WINDOW_SCAN_CAP}
    clauses = ""
    if cursor is not None:
        clauses += "   AND c.started_at < :cursor\n"
        params["cursor"] = cursor
    if widget_id is not None:
        clauses += "   AND c.widget_id = CAST(:widget_id AS uuid)\n"
        params["widget_id"] = widget_id
    if language is not None:
        clauses += "   AND c.language_detected = :language\n"
        params["language"] = language

    rows = (await db.execute(text(_WINDOW_SQL.format(clauses=clauses)), params)).all()
    if not rows:
        return []

    ids = [row.id for row in rows]
    turns = {t.conversation_id: t for t in (await db.execute(text(_TURNS_SQL), {"ids": ids, "org_id": org_id})).all()}
    judges = {j.conversation_id: j for j in (await db.execute(text(_JUDGES_SQL), {"ids": ids})).all()}
    review_rows: dict[int, list[Any]] = {}
    for review in (await db.execute(text(_REVIEWS_SQL), {"ids": ids, "org_id": org_id})).all():
        review_rows.setdefault(review.conversation_id, []).append(review)

    candidates: list[_Candidate] = []
    for row in rows:
        turn = turns.get(row.id)
        judge = judges.get(row.id)
        worst_band_rank = turn.worst_band_rank if turn is not None else None
        candidates.append(
            _Candidate(
                ConversationListItemOut(
                    id=row.id,
                    widget_id=row.widget_id,
                    widget_name=row.widget_name,
                    channel=channel,
                    started_at=row.started_at,
                    last_message_at=row.last_message_at,
                    message_count=row.message_count,
                    first_user_query=row.first_user_query,
                    language=row.language_detected,
                    worst_band=_BAND_BY_RANK.get(worst_band_rank) if worst_band_rank is not None else None,
                    judge=(
                        JudgeOut(
                            outcome=judge.outcome,
                            failure_category=judge.failure_category,
                            confidence=judge.confidence,
                        )
                        if judge is not None
                        else None
                    ),
                    ratings=RatingsOut(up=turn.ratings_up if turn else 0, down=turn.ratings_down if turn else 0),
                    review=_review_summary(review_rows.get(row.id, [])),
                ),
                refused_turns=turn.refused_turns if turn else 0,
            )
        )
    return candidates


async def _filtered_conversations(
    db: AsyncSession,
    *,
    perms: UserPermissions,
    days: int,
    cursor: datetime | None,
    widget_id: str | None,
    language: str | None,
    channel: str,
    judge_outcomes: list[str],
    failure_categories: list[str],
    review_status: str | None,
    causes: list[str],
    bands: list[str],
    rating: str | None,
    queue: bool,
) -> list[_Candidate]:
    if widget_id is not None:
        # A widget of another org yields an empty list, never a 404: existence
        # outside the caller's org is not disclosed (Appendix A).
        probe = await db.execute(text(_WIDGET_IN_ORG_SQL), {"widget_id": widget_id, "org_id": perms.org_id})
        if probe.first() is None:
            return []

    candidates = await _load_candidates(
        db,
        org_id=perms.org_id,
        cutoff=datetime.now(UTC) - timedelta(days=days),
        cursor=cursor,
        widget_id=widget_id,
        language=language,
        channel=channel,
    )
    kept = [
        candidate
        for candidate in candidates
        if (not queue or _in_queue(candidate))
        and _matches(
            candidate,
            judge_outcomes=judge_outcomes,
            failure_categories=failure_categories,
            review_status=review_status,
            causes=causes,
            bands=bands,
            rating=rating,
        )
    ]
    return kept


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    days: int = Query(default=7, ge=1, le=90),
    channel: Literal["webchat"] = Query(default=_WEBCHAT),
    widget_id: str | None = Query(default=None),
    language: str | None = Query(default=None),
    judge_outcome: list[str] = Query(default=[]),
    failure_category: list[str] = Query(default=[]),
    review_status: Literal["unreviewed", "reviewed"] | None = Query(default=None),
    cause: list[str] = Query(default=[]),
    band: list[str] = Query(default=[]),
    rating: Literal["thumbsUp", "thumbsDown", "none"] | None = Query(default=None),
    queue: bool = Query(default=False),
    sort: Literal["newest", "worst"] = Query(default="newest"),
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> ConversationListResponse:
    """Conversation list for the caller's org, newest first.

    ``cursor`` is the ISO ``started_at`` of the last row of the previous page
    (exclusive), same shape as the admin activity list. ``queue=true`` applies
    the worklist predicate; ``sort=worst`` orders thumbsDown first, then
    unresolved/partially_resolved judge verdicts, then the lowest band.
    """
    items = await _filtered_conversations(
        db,
        perms=perms,
        days=days,
        cursor=_parse_cursor(cursor),
        widget_id=widget_id,
        language=language,
        channel=channel,
        judge_outcomes=judge_outcome,
        failure_categories=failure_category,
        review_status=review_status,
        causes=cause,
        bands=band,
        rating=rating,
        queue=queue,
    )
    if sort == "worst":
        items.sort(key=_worst_sort_key)
    page = [candidate.item for candidate in items[:limit]]
    # The cursor is a `started_at <` window boundary, which only means
    # "everything older than the last row" for the newest-first order the
    # window query already returns. `sort=worst` re-sorts the whole candidate
    # set in memory, so the last row of a page is not that boundary and paging
    # from it would skip the conversations that are still unvisited. The worst
    # order is therefore served as one page only.
    next_cursor = None if sort == "worst" or not page or len(items) <= limit else _iso_z(page[-1].started_at)
    return ConversationListResponse(items=page, next_cursor=next_cursor)


@router.get("/queue-count", response_model=QueueCountOut)
async def get_queue_count(
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> QueueCountOut:
    """Badge for the kenniskant: the same set ``queue=true`` returns, over the
    default 7-day webchat window."""
    items = await _filtered_conversations(
        db,
        perms=perms,
        days=7,
        cursor=None,
        widget_id=None,
        language=None,
        channel=_WEBCHAT,
        judge_outcomes=[],
        failure_categories=[],
        review_status=None,
        causes=[],
        bands=[],
        rating=None,
        queue=True,
    )
    return QueueCountOut(count=len(items))


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: int,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Full transcript with retrieval signals, judge verdict and reviews.

    Returns a dict rather than a model instance because ``visitor`` is
    admin-or-higher only: for everyone else the key is left out entirely, and
    a response model would put it back as null.
    """
    row = (
        await db.execute(text(_CONVERSATION_SQL), {"conversation_id": conversation_id, "org_id": perms.org_id})
    ).first()
    if row is None:
        # Also covers another org's conversation and preview runs: neither is
        # ever confirmed to exist.
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="conversation not found")

    messages = (await db.execute(text(_MESSAGES_SQL), {"conversation_id": conversation_id})).all()
    message_ids = [message.id for message in messages]
    reviews: dict[int, Any] = {}
    if message_ids:
        result = await db.execute(text(_MESSAGE_REVIEWS_SQL), {"message_ids": message_ids, "org_id": perms.org_id})
        reviews = {review.message_id: review for review in result.all()}
    judged = (await db.execute(text(_JUDGE_DETAIL_SQL), {"conversation_id": conversation_id})).first()

    detail = ConversationDetailOut(
        id=row.id,
        widget_id=row.widget_id,
        widget_name=row.widget_name,
        channel=_WEBCHAT,
        started_at=row.started_at,
        language=row.language_detected,
        visitor=VisitorOut(name=row.visitor_name, email=row.visitor_email),
        quality=(
            QualityOut(
                outcome=judged.outcome,
                failure_category=judged.failure_category,
                reasoning=judged.reasoning,
                confidence=judged.confidence,
                suggested_action=judged.suggested_action,
                judged_at=judged.judged_at,
            )
            if judged is not None
            else None
        ),
        messages=[
            MessageOut(
                id=message.id,
                role=message.role,  # type: ignore[arg-type]
                content=message.content,
                sequence=message.sequence,
                created_at=message.created_at,
                sources=message.sources,
                rating=message.rating,
                answer_signals=message.answer_signals,
                review=(
                    ReviewOut(
                        verdict=reviews[message.id].verdict,
                        cause=reviews[message.id].cause,
                        note=reviews[message.id].note,
                        kb_slug=reviews[message.id].kb_slug,
                        reviewer_name=reviews[message.id].reviewer_name,
                        reviewed_at=reviews[message.id].reviewed_at,
                    )
                    if message.id in reviews
                    else None
                ),
            )
            for message in messages
        ],
    )

    payload = detail.model_dump()
    if not _may_see_visitor(perms):
        payload.pop("visitor")
    return payload


@router.put("/messages/{message_id}/review", response_model=ReviewOut)
async def put_review(
    message_id: int,
    body: ReviewRequest,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> ReviewOut:
    """File or overwrite the review of one assistant answer (last writer wins).

    The review-time snapshots come from the server, never the request: the
    band and judge state behind an answer are recomputed nightly, so the row
    has to record what it looked like at review time.
    """
    message = (await db.execute(text(_MESSAGE_PROBE_SQL), {"message_id": message_id})).first()
    if message is None or message.org_id != perms.org_id or message.is_preview:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="message not found")
    if message.role != "assistant":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="only assistant answers can be reviewed")

    judged = (await db.execute(text(_JUDGE_SNAPSHOT_SQL), {"conversation_id": message.conversation_id})).first()
    # Raw rows are dynamically shaped; the caller row exists because get_caller
    # resolved the token against it.
    caller: Any = (await db.execute(text(_CALLER_SQL), {"user_id": perms.user_id, "org_id": perms.org_id})).first()
    signals = message.answer_signals or {}

    insert_stmt = pg_insert(AnswerReview).values(
        org_id=perms.org_id,
        channel=_WEBCHAT,
        conversation_id=message.conversation_id,
        message_id=message_id,
        turn_sequence=message.sequence,
        reviewer_user_id=caller.id,
        verdict=body.verdict,
        cause=body.cause,
        note=body.note,
        kb_slug=body.kb_slug,
        band_at_review=signals.get("band") or "unknown",
        judge_outcome_at_review=judged.outcome if judged is not None else None,
        judge_failure_category_at_review=judged.failure_category if judged is not None else None,
        language=message.language_detected,
        # Phase 2 links a review to the gap it produced.
        gap_id=None,
    )
    upsert = insert_stmt.on_conflict_do_update(
        index_elements=["message_id"],
        index_where=text("message_id IS NOT NULL"),
        set_={
            **{column: getattr(insert_stmt.excluded, column) for column in _REVIEW_OVERWRITTEN},
            "reviewed_at": func.now(),
            "updated_at": func.now(),
        },
    ).returning(AnswerReview.reviewed_at)
    stored: Any = (await db.execute(upsert)).first()
    await db.commit()

    return ReviewOut(
        verdict=body.verdict,
        cause=body.cause,
        note=body.note,
        kb_slug=body.kb_slug,
        reviewer_name=caller.display_name,
        reviewed_at=stored.reviewed_at,
    )


@router.delete("/messages/{message_id}/review", status_code=status.HTTP_204_NO_CONTENT)
async def delete_review(
    message_id: int,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Withdraw the review of one answer; reviews are org-scoped by their own row."""
    await db.execute(text(_DELETE_REVIEW_SQL), {"message_id": message_id, "org_id": perms.org_id})
    await db.commit()
