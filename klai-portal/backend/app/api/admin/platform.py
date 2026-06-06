"""Platform-admin console endpoints — SPEC-PLATFORM-ADMIN-001.

Cross-tenant read-only overview of users, organisations, bots
(widgets/agents) and subscriptions for Klai staff. Every endpoint:

- [HARD] is gated on ``require_platform_admin()`` — caller must be an
  ADMIN inside the platform org (``settings.platform_org_slug``).
- [HARD] reads via ``cross_org_session()`` which bypasses RLS, so it
  MUST never be reachable without passing the platform-admin gate.
- [HARD] writes an audit event (``platform_admin.viewed``) on every
  read so cross-tenant access is never silent.

# @MX:ANCHOR fan_in=5 — every endpoint here is a cross-tenant RLS bypass
# @MX:REASON: Security boundary; the platform-admin gate is the only thing
#             standing between a tenant-admin and every other tenant's data.
# @MX:SPEC: SPEC-PLATFORM-ADMIN-001
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Literal

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, bindparam, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import cross_org_session
from app.core.permissions import UserPermissions, require_platform_admin
from app.klai_feedback.models import FeedbackItem, FeedbackItemLink, FeedbackSubmission, FeedbackTriageSuggestion
from app.klai_feedback.service import (
    FeedbackItemNotFoundError,
    FeedbackSubmissionNotFoundError,
    create_feedback_item_from_submission,
    delete_feedback_item,
    delete_feedback_submission,
    dismiss_feedback_submission,
    get_feedback_item,
    link_feedback_submission_to_item,
    mark_feedback_submission_support,
    resolve_feedback_item,
    search_feedback_items,
    update_feedback_item,
    update_feedback_submission,
)
from app.models.portal import PortalOrg as PortalOrgModel
from app.models.portal import PortalUser as PortalUserModel
from app.services.audit import log_event
from app.services.platform_subdomains import KLAI_SUBDOMAINS
from app.services.zitadel import zitadel

logger = structlog.get_logger()

router = APIRouter(prefix="/platform", tags=["platform-admin"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class PlatformStats(BaseModel):
    total_users: int
    new_users_this_month: int
    total_orgs: int
    active_subscriptions: int
    total_bots: int
    new_bots_today: int
    total_kbs: int
    total_templates: int
    total_feedback_count: int
    new_feedback_count: int
    chat_error_count: int
    mrr_cents: int
    arr_cents: int


class PlatformUser(BaseModel):
    zitadel_user_id: str
    email: str | None
    display_name: str | None
    role: str
    is_admin: bool
    status: str
    deletion_status: str | None = None
    deletion_failure_reason: dict[str, Any] | None = None
    deletion_last_attempted_step: str | None = None
    org_id: int
    org_name: str
    org_slug: str
    org_plan: str
    org_onboarded: bool
    created_at: datetime


class PlatformOrg(BaseModel):
    id: int
    name: str
    slug: str
    plan: str
    platform_unlocked_features: list[str] = Field(default_factory=list)
    billing_status: str
    billing_cycle: str
    seats: int
    provisioning_status: str
    user_count: int
    bot_count: int
    kb_count: int
    created_at: datetime


class PlatformBot(BaseModel):
    id: str
    name: str
    widget_id: str
    org_id: int
    org_name: str
    org_slug: str
    kb_count: int
    created_at: datetime


class PlatformChatError(BaseModel):
    id: int
    org_id: int
    org_name: str | None
    event_type: str
    detail: str | None
    created_at: datetime


class PlatformFeedbackDuplicateCandidate(BaseModel):
    item_id: int
    confidence: float | None = None
    reason: str | None = None
    title: str | None = None
    kind: str | None = None
    status: str | None = None
    area: str | None = None


class PlatformFeedbackTriageSuggestion(BaseModel):
    classification: str | None
    summary: str | None
    suggested_area: str | None
    suggested_severity: str | None
    suggested_action: str | None
    duplicate_candidates: list[PlatformFeedbackDuplicateCandidate]
    model: str | None
    created_at: datetime | None


class PlatformFeedbackSubmission(BaseModel):
    id: int
    org_id: int | None
    org_name: str | None
    org_slug: str | None
    user_id: str | None
    user_email: str | None
    user_display_name: str | None
    event_type: str
    status: str
    raw_text: str | None
    feedback_type: str | None
    severity: str | None
    page_url: str | None
    route_id: str | None
    locale: str | None
    viewport: str | None
    created_at: datetime
    triage_suggestion: PlatformFeedbackTriageSuggestion | None = None
    linked_item_id: int | None = None
    linked_item_title: str | None = None
    linked_item_status: str | None = None


class PlatformFeedbackReporterOrg(BaseModel):
    org_id: int | None
    org_name: str | None
    org_slug: str | None
    user_count: int


class PlatformFeedbackItem(BaseModel):
    id: int
    kind: str
    title: str
    summary: str | None
    status: str
    area: str | None
    priority_score: int
    org_count: int
    user_count: int
    shipped_at: datetime | None
    resolution_summary: str | None = None
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    notification_state: str | None = None
    reporter_orgs: list[PlatformFeedbackReporterOrg] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class PlatformFeedbackLinkedSubmission(PlatformFeedbackSubmission):
    link_type: str
    linked_at: datetime


class PlatformFeedbackItemDetail(BaseModel):
    item: PlatformFeedbackItem
    submissions: list[PlatformFeedbackLinkedSubmission]


class PlatformFeedbackActionResult(BaseModel):
    ok: bool = True
    submission_id: int
    status: str
    item_id: int | None = None


class PlatformFeedbackCreateItemIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    kind: Literal["feature", "bug", "ux_confusion", "docs", "support_pattern"]
    title: str = Field(..., min_length=3, max_length=256)
    summary: str | None = Field(default=None, max_length=4000)
    area: str | None = Field(default=None, max_length=128)
    link_type: Literal["upvote", "evidence", "bug_repro", "support_signal"] = "evidence"


class PlatformFeedbackLinkItemIn(BaseModel):
    item_id: int
    link_type: Literal["upvote", "evidence", "bug_repro", "support_signal"] = "evidence"


class PlatformFeedbackSubmissionPatchIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    raw_text: str | None = Field(default=None, min_length=1, max_length=4000)
    status: Literal["new", "open", "resolved", "dismissed", "support"] | None = None


class PlatformFeedbackItemPatchIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    kind: Literal["feature", "bug", "ux_confusion", "docs", "support_pattern"] | None = None
    title: str | None = Field(default=None, min_length=3, max_length=256)
    summary: str | None = Field(default=None, max_length=4000)
    status: Literal["open", "resolved", "dismissed"] | None = None
    area: str | None = Field(default=None, max_length=128)


class PlatformFeedbackResolveIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    resolution_summary: str = Field(..., min_length=3, max_length=4000)
    channels: list[Literal["in_app", "email"]] = Field(default_factory=lambda: ["in_app"])
    subject: str | None = Field(default=None, max_length=256)


class PlatformFeedbackNotificationOut(BaseModel):
    id: int
    item_id: int
    submission_id: int | None
    org_id: int | None
    user_id: str | None
    recipient_email: str | None
    channel: str
    status: str
    subject: str | None
    body: str
    sent_at: datetime | None
    read_at: datetime | None
    created_at: datetime


class PlatformFeedbackResolveOut(BaseModel):
    item: PlatformFeedbackItem
    notifications: list[PlatformFeedbackNotificationOut]
    recipient_count: int


class PlatformKB(BaseModel):
    id: int
    name: str
    slug: str
    org_id: int
    org_name: str
    org_slug: str
    owner_type: str
    visibility: str
    created_at: datetime


class PlatformTemplate(BaseModel):
    id: int
    name: str
    slug: str
    org_id: int
    org_name: str
    org_slug: str
    scope: str  # "org" | "personal"
    created_by: str
    created_by_name: str | None  # resolved display name / email of creator
    is_active: bool
    created_at: datetime


class PlatformOrgDetail(BaseModel):
    org: PlatformOrg
    users: list[PlatformUser]
    bots: list[PlatformBot]
    knowledge_bases: list[PlatformKB]
    templates: list[PlatformTemplate]


# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------


async def _audit(perms: UserPermissions, tab: str, search: str | None) -> None:
    """Record a cross-tenant platform read. Never raises."""
    await log_event(
        org_id=perms.org_id,
        actor=perms.user_id,
        action="platform_admin.viewed",
        resource_type="platform_console",
        resource_id=tab,
        details={"search": search} if search else None,
    )


def _feedback_event_type(source: str) -> str:
    if source == "assistant_problem":
        return "klai_assistant.problem_report"
    if source == "assistant_question":
        return "klai_assistant.question"
    return "klai_assistant.feedback"


def _candidate_item_id(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return None


def _platform_feedback_triage_suggestion(
    suggestion: FeedbackTriageSuggestion,
    items_by_id: dict[int, FeedbackItem],
) -> PlatformFeedbackTriageSuggestion:
    raw_candidates = suggestion.duplicate_candidates_json.get("candidates", {})
    if not isinstance(raw_candidates, list):
        raw_candidates = []

    candidates: list[PlatformFeedbackDuplicateCandidate] = []
    for candidate in raw_candidates:
        if not isinstance(candidate, dict):
            continue
        item_id = _candidate_item_id(candidate.get("item_id"))
        if item_id is None:
            continue
        item = items_by_id.get(item_id)
        candidates.append(
            PlatformFeedbackDuplicateCandidate(
                item_id=item_id,
                confidence=float(candidate["confidence"])
                if isinstance(candidate.get("confidence"), int | float)
                else None,
                reason=candidate.get("reason") if isinstance(candidate.get("reason"), str) else None,
                title=item.title if item else None,
                kind=item.kind if item else None,
                status=item.status if item else None,
                area=item.area if item else None,
            )
        )

    return PlatformFeedbackTriageSuggestion(
        classification=suggestion.classification,
        summary=suggestion.summary,
        suggested_area=suggestion.suggested_area,
        suggested_severity=suggestion.suggested_severity,
        suggested_action=suggestion.suggested_action,
        duplicate_candidates=candidates,
        model=suggestion.model,
        created_at=suggestion.created_at,
    )


async def _platform_feedback_triage_suggestions(
    db: AsyncSession,
    submission_ids: list[int],
) -> dict[int, PlatformFeedbackTriageSuggestion]:
    if not submission_ids:
        return {}

    suggestions = list(
        (
            await db.execute(
                select(FeedbackTriageSuggestion)
                .where(FeedbackTriageSuggestion.submission_id.in_(submission_ids))
                .order_by(FeedbackTriageSuggestion.submission_id.asc(), FeedbackTriageSuggestion.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    latest_by_submission: dict[int, FeedbackTriageSuggestion] = {}
    candidate_item_ids: set[int] = set()
    for suggestion in suggestions:
        if suggestion.submission_id in latest_by_submission:
            continue
        latest_by_submission[suggestion.submission_id] = suggestion
        raw_candidates = suggestion.duplicate_candidates_json.get("candidates", {})
        if isinstance(raw_candidates, list):
            for candidate in raw_candidates:
                if not isinstance(candidate, dict):
                    continue
                item_id = _candidate_item_id(candidate.get("item_id"))
                if item_id is not None:
                    candidate_item_ids.add(item_id)

    items_by_id: dict[int, FeedbackItem] = {}
    if candidate_item_ids:
        items = (await db.execute(select(FeedbackItem).where(FeedbackItem.id.in_(candidate_item_ids)))).scalars().all()
        items_by_id = {item.id: item for item in items}

    return {
        submission_id: _platform_feedback_triage_suggestion(suggestion, items_by_id)
        for submission_id, suggestion in latest_by_submission.items()
    }


def _platform_feedback_submission(
    row: Any,
    triage_suggestions: dict[int, PlatformFeedbackTriageSuggestion],
    linked: Any | None = None,
) -> PlatformFeedbackSubmission:
    return PlatformFeedbackSubmission(
        id=row.id,
        org_id=row.org_id,
        org_name=row.org_name,
        org_slug=row.org_slug,
        user_id=row.user_id,
        user_email=row.user_email,
        user_display_name=row.user_display_name,
        event_type=_feedback_event_type(row.source),
        status=row.status,
        raw_text=row.raw_text,
        feedback_type=row.feedback_type,
        severity=row.severity,
        page_url=row.page_url,
        route_id=row.route_id,
        locale=row.locale,
        viewport=row.viewport,
        created_at=row.created_at,
        triage_suggestion=triage_suggestions.get(row.id),
        linked_item_id=linked.id if linked is not None else None,
        linked_item_title=linked.title if linked is not None else None,
        linked_item_status=linked.status if linked is not None else None,
    )


async def _zitadel_identity_map() -> dict[str, tuple[str | None, str | None]]:
    """Map ``zitadel_user_id -> (display_name, email)`` for every human in the
    single portal org. ``portal_users`` is mapping-only (no live identity), so
    the console must resolve names from Zitadel — same pattern as
    ``admin/users.py``. Best-effort: on Zitadel failure returns ``{}`` so the
    console still renders (callers fall back to the id)."""
    from app.core.config import settings

    try:
        zusers = await zitadel.list_org_users(settings.zitadel_portal_org_id)
    except Exception:
        logger.warning("platform_identity_lookup_failed", exc_info=True)
        return {}

    out: dict[str, tuple[str | None, str | None]] = {}
    for z in zusers:
        uid = z.get("id", "")
        if not uid:
            continue
        human = z.get("human", {})
        profile = human.get("profile", {})
        name = (
            profile.get("displayName")
            or " ".join(p for p in (profile.get("firstName"), profile.get("lastName")) if p).strip()
            or None
        )
        email = human.get("email", {}).get("email") or None
        out[uid] = (name, email)
    return out


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/stats", response_model=PlatformStats)
async def platform_stats(
    perms: UserPermissions = Depends(require_platform_admin()),
) -> PlatformStats:
    await _audit(perms, "stats", None)
    async with cross_org_session() as db:
        row = (
            await db.execute(
                text(
                    """
                    SELECT
                      (SELECT COUNT(*) FROM portal_users
                         WHERE status <> 'offboarded') AS total_users,
                      (SELECT COUNT(*) FROM portal_users
                         WHERE status <> 'offboarded'
                           AND created_at >= date_trunc('month', NOW())) AS new_users_month,
                      (SELECT COUNT(*) FROM portal_orgs
                         WHERE deleted_at IS NULL) AS total_orgs,
                      (SELECT COUNT(*) FROM portal_orgs
                         WHERE deleted_at IS NULL
                           AND billing_status IN ('active','trialing')) AS active_subs,
                      (SELECT COUNT(*) FROM widgets) AS total_bots,
                      (SELECT COUNT(*) FROM widgets
                         WHERE created_at >= date_trunc('day', NOW())) AS new_bots_today,
                      (SELECT COUNT(*) FROM portal_knowledge_bases) AS total_kbs,
                      (SELECT COUNT(*) FROM portal_templates
                         WHERE is_active) AS total_templates,
                      (SELECT COUNT(*) FROM feedback_submissions) AS total_feedback_count,
                      (SELECT COUNT(*) FROM feedback_submissions
                         WHERE status = 'new') AS new_feedback_count,
                      (SELECT COUNT(*) FROM product_events
                         WHERE event_type LIKE '%error%'
                           AND created_at >= NOW() - INTERVAL '24 hours') AS chat_error_count
                    """
                )
            )
        ).one()

    # MRR is stubbed at 0 until a plan→price table exists (SPEC §7).
    return PlatformStats(
        total_users=row.total_users,
        new_users_this_month=row.new_users_month,
        total_orgs=row.total_orgs,
        active_subscriptions=row.active_subs,
        total_bots=row.total_bots,
        new_bots_today=row.new_bots_today,
        total_kbs=row.total_kbs,
        total_templates=row.total_templates,
        total_feedback_count=row.total_feedback_count,
        new_feedback_count=row.new_feedback_count,
        chat_error_count=row.chat_error_count,
        mrr_cents=0,
        arr_cents=0,
    )


@router.get("/users", response_model=list[PlatformUser])
async def platform_users(
    search: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    perms: UserPermissions = Depends(require_platform_admin()),
) -> list[PlatformUser]:
    await _audit(perms, "users", search)
    params: dict[str, object] = {"limit": limit, "offset": offset}
    if search:
        params["q"] = f"%{search}%"

    async with cross_org_session() as db:
        if search:
            result = await db.execute(
                text(
                    "SELECT u.zitadel_user_id, u.email, u.display_name, u.role, "
                    "u.status, u.deletion_status, u.failure_reason, u.last_attempted_step, "
                    "u.created_at, o.id AS org_id, o.name AS org_name, "
                    "o.slug AS org_slug, o.plan AS org_plan, "
                    "o.provisioning_status AS prov "
                    "FROM portal_users u "
                    "JOIN portal_orgs o ON o.id = u.org_id "
                    "WHERE u.status <> 'offboarded' "
                    "AND (u.email ILIKE :q OR u.display_name ILIKE :q OR o.name ILIKE :q) "
                    "ORDER BY u.created_at DESC "
                    "LIMIT :limit OFFSET :offset"
                ),
                params,
            )
        else:
            result = await db.execute(
                text(
                    "SELECT u.zitadel_user_id, u.email, u.display_name, u.role, "
                    "u.status, u.deletion_status, u.failure_reason, u.last_attempted_step, "
                    "u.created_at, o.id AS org_id, o.name AS org_name, "
                    "o.slug AS org_slug, o.plan AS org_plan, "
                    "o.provisioning_status AS prov "
                    "FROM portal_users u "
                    "JOIN portal_orgs o ON o.id = u.org_id "
                    "WHERE u.status <> 'offboarded' "
                    "ORDER BY u.created_at DESC "
                    "LIMIT :limit OFFSET :offset"
                ),
                params,
            )
        rows = result.all()

    identity = await _zitadel_identity_map()

    return [
        PlatformUser(
            zitadel_user_id=r.zitadel_user_id,
            email=identity.get(r.zitadel_user_id, (None, None))[1] or r.email,
            display_name=identity.get(r.zitadel_user_id, (None, None))[0] or r.display_name,
            role=r.role,
            is_admin=r.role == "admin",
            status=r.status,
            deletion_status=r.deletion_status,
            deletion_failure_reason=r.failure_reason,
            deletion_last_attempted_step=r.last_attempted_step,
            org_id=r.org_id,
            org_name=r.org_name,
            org_slug=r.org_slug,
            org_plan=r.org_plan,
            org_onboarded=r.prov == "ready",
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.get("/organizations", response_model=list[PlatformOrg])
async def platform_organizations(
    search: str | None = Query(default=None),
    perms: UserPermissions = Depends(require_platform_admin()),
) -> list[PlatformOrg]:
    await _audit(perms, "organizations", search)
    params: dict[str, object] = {}
    if search:
        params["q"] = f"%{search}%"

    async with cross_org_session() as db:
        if search:
            result = await db.execute(
                text(
                    "SELECT o.id, o.name, o.slug, o.plan, o.billing_status, "
                    "o.platform_unlocked_features, o.billing_cycle, o.seats, o.provisioning_status, o.created_at, "
                    "(SELECT COUNT(*) FROM portal_users u "
                    "  WHERE u.org_id = o.id AND u.status <> 'offboarded') AS user_count, "
                    "(SELECT COUNT(*) FROM widgets w WHERE w.org_id = o.id) AS bot_count, "
                    "(SELECT COUNT(*) FROM portal_knowledge_bases kb "
                    "  WHERE kb.org_id = o.id) AS kb_count "
                    "FROM portal_orgs o "
                    "WHERE o.deleted_at IS NULL "
                    "AND (o.name ILIKE :q OR o.slug ILIKE :q) "
                    "ORDER BY o.created_at DESC"
                ),
                params,
            )
        else:
            result = await db.execute(
                text(
                    "SELECT o.id, o.name, o.slug, o.plan, o.billing_status, "
                    "o.platform_unlocked_features, o.billing_cycle, o.seats, o.provisioning_status, o.created_at, "
                    "(SELECT COUNT(*) FROM portal_users u "
                    "  WHERE u.org_id = o.id AND u.status <> 'offboarded') AS user_count, "
                    "(SELECT COUNT(*) FROM widgets w WHERE w.org_id = o.id) AS bot_count, "
                    "(SELECT COUNT(*) FROM portal_knowledge_bases kb "
                    "  WHERE kb.org_id = o.id) AS kb_count "
                    "FROM portal_orgs o "
                    "WHERE o.deleted_at IS NULL "
                    "ORDER BY o.created_at DESC"
                ),
                params,
            )
        rows = result.all()

    return [
        PlatformOrg(
            id=r.id,
            name=r.name,
            slug=r.slug,
            plan=r.plan,
            platform_unlocked_features=list(r.platform_unlocked_features or []),
            billing_status=r.billing_status,
            billing_cycle=r.billing_cycle,
            seats=r.seats,
            provisioning_status=r.provisioning_status,
            user_count=r.user_count,
            bot_count=r.bot_count,
            kb_count=r.kb_count,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.get("/organizations/{org_id}", response_model=PlatformOrgDetail)
async def platform_org_detail(
    org_id: int,
    perms: UserPermissions = Depends(require_platform_admin()),
) -> PlatformOrgDetail:
    """One org with its users + bots — drill-down from the console."""
    await _audit(perms, f"organization:{org_id}", None)
    async with cross_org_session() as db:
        org_row = (
            await db.execute(
                text(
                    "SELECT o.id, o.name, o.slug, o.plan, o.billing_status, "
                    "o.platform_unlocked_features, o.billing_cycle, o.seats, o.provisioning_status, o.created_at, "
                    "(SELECT COUNT(*) FROM portal_users u "
                    "  WHERE u.org_id = o.id AND u.status <> 'offboarded') AS user_count, "
                    "(SELECT COUNT(*) FROM widgets w WHERE w.org_id = o.id) AS bot_count, "
                    "(SELECT COUNT(*) FROM portal_knowledge_bases kb "
                    "  WHERE kb.org_id = o.id) AS kb_count "
                    "FROM portal_orgs o WHERE o.id = :org_id AND o.deleted_at IS NULL"
                ),
                {"org_id": org_id},
            )
        ).first()
        if org_row is None:
            raise HTTPException(status_code=404, detail="Organisatie niet gevonden")

        user_rows = (
            await db.execute(
                text(
                    "SELECT u.zitadel_user_id, u.email, u.display_name, u.role, "
                    "u.status, u.deletion_status, u.failure_reason, u.last_attempted_step, "
                    "u.created_at FROM portal_users u "
                    "WHERE u.org_id = :org_id AND u.status <> 'offboarded' "
                    "ORDER BY u.created_at DESC"
                ),
                {"org_id": org_id},
            )
        ).all()

        bot_rows = (
            await db.execute(
                text(
                    "SELECT w.id, w.name, w.widget_id, w.created_at, "
                    "(SELECT COUNT(*) FROM widget_kb_access k "
                    "  WHERE k.widget_id = w.id) AS kb_count "
                    "FROM widgets w WHERE w.org_id = :org_id "
                    "ORDER BY w.created_at DESC"
                ),
                {"org_id": org_id},
            )
        ).all()

        kb_rows = (
            await db.execute(
                text(
                    "SELECT kb.id, kb.name, kb.slug, kb.owner_type, "
                    "kb.visibility, kb.created_at "
                    "FROM portal_knowledge_bases kb WHERE kb.org_id = :org_id "
                    "ORDER BY kb.created_at DESC"
                ),
                {"org_id": org_id},
            )
        ).all()

        template_rows = (
            await db.execute(
                text(
                    "SELECT t.id, t.name, t.slug, t.scope, t.created_by, "
                    "t.is_active, t.created_at, "
                    "COALESCE(u.display_name, u.email) AS created_by_name "
                    "FROM portal_templates t "
                    "LEFT JOIN portal_users u "
                    "  ON u.zitadel_user_id = t.created_by AND u.org_id = t.org_id "
                    "WHERE t.org_id = :org_id "
                    "ORDER BY t.created_at DESC"
                ),
                {"org_id": org_id},
            )
        ).all()

    org = PlatformOrg(
        id=org_row.id,
        name=org_row.name,
        slug=org_row.slug,
        plan=org_row.plan,
        platform_unlocked_features=list(org_row.platform_unlocked_features or []),
        billing_status=org_row.billing_status,
        billing_cycle=org_row.billing_cycle,
        seats=org_row.seats,
        provisioning_status=org_row.provisioning_status,
        user_count=org_row.user_count,
        bot_count=org_row.bot_count,
        kb_count=org_row.kb_count,
        created_at=org_row.created_at,
    )
    onboarded = org_row.provisioning_status == "ready"
    identity = await _zitadel_identity_map()
    users = [
        PlatformUser(
            zitadel_user_id=u.zitadel_user_id,
            email=identity.get(u.zitadel_user_id, (None, None))[1] or u.email,
            display_name=identity.get(u.zitadel_user_id, (None, None))[0] or u.display_name,
            role=u.role,
            is_admin=u.role == "admin",
            status=u.status,
            deletion_status=u.deletion_status,
            deletion_failure_reason=u.failure_reason,
            deletion_last_attempted_step=u.last_attempted_step,
            org_id=org.id,
            org_name=org.name,
            org_slug=org.slug,
            org_plan=org.plan,
            org_onboarded=onboarded,
            created_at=u.created_at,
        )
        for u in user_rows
    ]
    bots = [
        PlatformBot(
            id=str(b.id),
            name=b.name,
            widget_id=b.widget_id,
            org_id=org.id,
            org_name=org.name,
            org_slug=org.slug,
            kb_count=b.kb_count,
            created_at=b.created_at,
        )
        for b in bot_rows
    ]
    kbs = [
        PlatformKB(
            id=k.id,
            name=k.name,
            slug=k.slug,
            org_id=org.id,
            org_name=org.name,
            org_slug=org.slug,
            owner_type=k.owner_type,
            visibility=k.visibility,
            created_at=k.created_at,
        )
        for k in kb_rows
    ]
    templates = [
        PlatformTemplate(
            id=t.id,
            name=t.name,
            slug=t.slug,
            org_id=org.id,
            org_name=org.name,
            org_slug=org.slug,
            scope=t.scope,
            created_by=t.created_by,
            created_by_name=t.created_by_name,
            is_active=t.is_active,
            created_at=t.created_at,
        )
        for t in template_rows
    ]
    return PlatformOrgDetail(
        org=org,
        users=users,
        bots=bots,
        knowledge_bases=kbs,
        templates=templates,
    )


@router.get("/bots", response_model=list[PlatformBot])
async def platform_bots(
    search: str | None = Query(default=None),
    perms: UserPermissions = Depends(require_platform_admin()),
) -> list[PlatformBot]:
    await _audit(perms, "bots", search)
    params: dict[str, object] = {}
    if search:
        params["q"] = f"%{search}%"

    async with cross_org_session() as db:
        if search:
            result = await db.execute(
                text(
                    "SELECT w.id, w.name, w.widget_id, w.created_at, "
                    "o.id AS org_id, o.name AS org_name, o.slug AS org_slug, "
                    "(SELECT COUNT(*) FROM widget_kb_access k "
                    "  WHERE k.widget_id = w.id) AS kb_count "
                    "FROM widgets w "
                    "JOIN portal_orgs o ON o.id = w.org_id "
                    "WHERE (w.name ILIKE :q OR o.name ILIKE :q) "
                    "ORDER BY w.created_at DESC"
                ),
                params,
            )
        else:
            result = await db.execute(
                text(
                    "SELECT w.id, w.name, w.widget_id, w.created_at, "
                    "o.id AS org_id, o.name AS org_name, o.slug AS org_slug, "
                    "(SELECT COUNT(*) FROM widget_kb_access k "
                    "  WHERE k.widget_id = w.id) AS kb_count "
                    "FROM widgets w "
                    "JOIN portal_orgs o ON o.id = w.org_id "
                    "ORDER BY w.created_at DESC"
                ),
                params,
            )
        rows = result.all()

    return [
        PlatformBot(
            id=str(r.id),
            name=r.name,
            widget_id=r.widget_id,
            org_id=r.org_id,
            org_name=r.org_name,
            org_slug=r.org_slug,
            kb_count=r.kb_count,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.get("/knowledge-bases", response_model=list[PlatformKB])
async def platform_knowledge_bases(
    search: str | None = Query(default=None),
    perms: UserPermissions = Depends(require_platform_admin()),
) -> list[PlatformKB]:
    """Cross-tenant list of all knowledge bases."""
    await _audit(perms, "knowledge-bases", search)
    params: dict[str, object] = {}
    if search:
        params["q"] = f"%{search}%"

    async with cross_org_session() as db:
        if search:
            result = await db.execute(
                text(
                    "SELECT kb.id, kb.name, kb.slug, kb.owner_type, "
                    "kb.visibility, kb.created_at, "
                    "o.id AS org_id, o.name AS org_name, o.slug AS org_slug "
                    "FROM portal_knowledge_bases kb "
                    "JOIN portal_orgs o ON o.id = kb.org_id "
                    "WHERE (kb.name ILIKE :q OR o.name ILIKE :q) "
                    "ORDER BY kb.created_at DESC"
                ),
                params,
            )
        else:
            result = await db.execute(
                text(
                    "SELECT kb.id, kb.name, kb.slug, kb.owner_type, "
                    "kb.visibility, kb.created_at, "
                    "o.id AS org_id, o.name AS org_name, o.slug AS org_slug "
                    "FROM portal_knowledge_bases kb "
                    "JOIN portal_orgs o ON o.id = kb.org_id "
                    "ORDER BY kb.created_at DESC"
                ),
                params,
            )
        rows = result.all()

    return [
        PlatformKB(
            id=r.id,
            name=r.name,
            slug=r.slug,
            org_id=r.org_id,
            org_name=r.org_name,
            org_slug=r.org_slug,
            owner_type=r.owner_type,
            visibility=r.visibility,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.get("/templates", response_model=list[PlatformTemplate])
async def platform_templates(
    search: str | None = Query(default=None),
    perms: UserPermissions = Depends(require_platform_admin()),
) -> list[PlatformTemplate]:
    """Cross-tenant list of all chat templates (org + personal)."""
    await _audit(perms, "templates", search)
    params: dict[str, object] = {}
    if search:
        params["q"] = f"%{search}%"

    async with cross_org_session() as db:
        if search:
            result = await db.execute(
                text(
                    "SELECT t.id, t.name, t.slug, t.scope, t.created_by, "
                    "t.is_active, t.created_at, "
                    "o.id AS org_id, o.name AS org_name, o.slug AS org_slug, "
                    "COALESCE(u.display_name, u.email) AS created_by_name "
                    "FROM portal_templates t "
                    "JOIN portal_orgs o ON o.id = t.org_id "
                    "LEFT JOIN portal_users u "
                    "  ON u.zitadel_user_id = t.created_by AND u.org_id = t.org_id "
                    "WHERE (t.name ILIKE :q OR o.name ILIKE :q) "
                    "ORDER BY t.created_at DESC"
                ),
                params,
            )
        else:
            result = await db.execute(
                text(
                    "SELECT t.id, t.name, t.slug, t.scope, t.created_by, "
                    "t.is_active, t.created_at, "
                    "o.id AS org_id, o.name AS org_name, o.slug AS org_slug, "
                    "COALESCE(u.display_name, u.email) AS created_by_name "
                    "FROM portal_templates t "
                    "JOIN portal_orgs o ON o.id = t.org_id "
                    "LEFT JOIN portal_users u "
                    "  ON u.zitadel_user_id = t.created_by AND u.org_id = t.org_id "
                    "ORDER BY t.created_at DESC"
                ),
                params,
            )
        rows = result.all()

    return [
        PlatformTemplate(
            id=r.id,
            name=r.name,
            slug=r.slug,
            org_id=r.org_id,
            org_name=r.org_name,
            org_slug=r.org_slug,
            scope=r.scope,
            created_by=r.created_by,
            created_by_name=r.created_by_name,
            is_active=r.is_active,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.get("/chat-errors", response_model=list[PlatformChatError])
async def platform_chat_errors(
    limit: int = Query(default=50, ge=1, le=200),
    perms: UserPermissions = Depends(require_platform_admin()),
) -> list[PlatformChatError]:
    """Recent chat-error product-events across all tenants.

    v1 best-effort: reads ``product_events`` for error-flavoured event
    types. If the table or events don't exist yet, returns []. Full
    error-stream integration is a follow-up (SPEC §7).
    """
    await _audit(perms, "chat-errors", None)
    async with cross_org_session() as db:
        try:
            rows = (
                await db.execute(
                    text(
                        "SELECT e.id, e.org_id, e.event_type, "
                        "e.properties::text AS detail, e.created_at, "
                        "o.name AS org_name "
                        "FROM product_events e "
                        "LEFT JOIN portal_orgs o ON o.id = e.org_id "
                        "WHERE e.event_type LIKE '%error%' "
                        "ORDER BY e.created_at DESC "
                        "LIMIT :limit"
                    ),
                    {"limit": limit},
                )
            ).all()
        except Exception:
            logger.warning("platform_chat_errors_query_failed", exc_info=True)
            return []

    return [
        PlatformChatError(
            id=r.id,
            org_id=r.org_id,
            org_name=r.org_name,
            event_type=r.event_type,
            detail=r.detail,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.get("/feedback/submissions", response_model=list[PlatformFeedbackSubmission])
@router.get("/feedback-submissions", response_model=list[PlatformFeedbackSubmission])
async def platform_feedback_submissions(
    search: str | None = Query(default=None),
    status_filter: Literal["new", "open", "resolved", "dismissed", "support"] | None = Query(
        default=None,
        alias="status",
    ),
    kind: Literal["feedback", "problem", "question"] | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
    perms: UserPermissions = Depends(require_platform_admin()),
) -> list[PlatformFeedbackSubmission]:
    """Recent first-party assistant submissions across tenants.

    SPEC-KLAI-FEEDBACK-001 now uses feedback_submissions as the durable source
    of truth. product_events still receives secondary audit/analytics events,
    but Platform must not depend on that event stream for triage.
    """
    await _audit(perms, "feedback", search)
    params: dict[str, object] = {"limit": limit}

    feedback_type = FeedbackSubmission.metadata_json["feedback_type"].astext
    severity = FeedbackSubmission.metadata_json["severity"].astext

    query = (
        select(
            FeedbackSubmission.id.label("id"),
            FeedbackSubmission.org_id.label("org_id"),
            PortalOrgModel.name.label("org_name"),
            PortalOrgModel.slug.label("org_slug"),
            FeedbackSubmission.user_id.label("user_id"),
            PortalUserModel.email.label("user_email"),
            PortalUserModel.display_name.label("user_display_name"),
            FeedbackSubmission.source.label("source"),
            FeedbackSubmission.status.label("status"),
            FeedbackSubmission.raw_text.label("raw_text"),
            feedback_type.label("feedback_type"),
            severity.label("severity"),
            FeedbackSubmission.page_url.label("page_url"),
            FeedbackSubmission.route_id.label("route_id"),
            FeedbackSubmission.locale.label("locale"),
            FeedbackSubmission.viewport.label("viewport"),
            FeedbackSubmission.created_at.label("created_at"),
        )
        .select_from(FeedbackSubmission)
        .outerjoin(PortalOrgModel, PortalOrgModel.id == FeedbackSubmission.org_id)
        .outerjoin(
            PortalUserModel,
            and_(
                PortalUserModel.zitadel_user_id == FeedbackSubmission.user_id,
                PortalUserModel.org_id == FeedbackSubmission.org_id,
            ),
        )
        .where(FeedbackSubmission.source.in_(("assistant_feedback", "assistant_problem", "assistant_question")))
        .order_by(FeedbackSubmission.created_at.desc())
        .limit(bindparam("limit"))
    )

    if search:
        params["q"] = f"%{search}%"
        q = bindparam("q")
        query = query.where(
            or_(
                PortalOrgModel.name.ilike(q),
                PortalOrgModel.slug.ilike(q),
                FeedbackSubmission.user_id.ilike(q),
                FeedbackSubmission.source.ilike(q),
                FeedbackSubmission.raw_text.ilike(q),
                FeedbackSubmission.page_url.ilike(q),
                FeedbackSubmission.route_id.ilike(q),
            )
        )
    if status_filter:
        params["status"] = status_filter
        query = query.where(FeedbackSubmission.status == bindparam("status"))
    if kind:
        source_by_kind = {
            "feedback": "assistant_feedback",
            "problem": "assistant_problem",
            "question": "assistant_question",
        }
        params["source"] = source_by_kind[kind]
        query = query.where(FeedbackSubmission.source == bindparam("source"))

    async with cross_org_session() as db:
        try:
            rows = (await db.execute(query, params)).all()
        except Exception:
            logger.warning("platform_feedback_submissions_query_failed", exc_info=True)
            return []
        try:
            triage_suggestions = await _platform_feedback_triage_suggestions(db, [r.id for r in rows])
        except Exception:
            logger.warning("platform_feedback_triage_suggestions_query_failed", exc_info=True)
            triage_suggestions = {}

    return [_platform_feedback_submission(r, triage_suggestions) for r in rows]


@router.get("/feedback/submissions/{submission_id}", response_model=PlatformFeedbackSubmission)
async def platform_feedback_submission_detail(
    submission_id: int,
    perms: UserPermissions = Depends(require_platform_admin()),
) -> PlatformFeedbackSubmission:
    """Return one feedback submission for the platform detail page."""
    await _audit(perms, "feedback:submission_detail", str(submission_id))
    feedback_type = FeedbackSubmission.metadata_json["feedback_type"].astext
    severity = FeedbackSubmission.metadata_json["severity"].astext
    query = (
        select(
            FeedbackSubmission.id.label("id"),
            FeedbackSubmission.org_id.label("org_id"),
            PortalOrgModel.name.label("org_name"),
            PortalOrgModel.slug.label("org_slug"),
            FeedbackSubmission.user_id.label("user_id"),
            PortalUserModel.email.label("user_email"),
            PortalUserModel.display_name.label("user_display_name"),
            FeedbackSubmission.source.label("source"),
            FeedbackSubmission.status.label("status"),
            FeedbackSubmission.raw_text.label("raw_text"),
            feedback_type.label("feedback_type"),
            severity.label("severity"),
            FeedbackSubmission.page_url.label("page_url"),
            FeedbackSubmission.route_id.label("route_id"),
            FeedbackSubmission.locale.label("locale"),
            FeedbackSubmission.viewport.label("viewport"),
            FeedbackSubmission.created_at.label("created_at"),
        )
        .select_from(FeedbackSubmission)
        .outerjoin(PortalOrgModel, PortalOrgModel.id == FeedbackSubmission.org_id)
        .outerjoin(
            PortalUserModel,
            and_(
                PortalUserModel.zitadel_user_id == FeedbackSubmission.user_id,
                PortalUserModel.org_id == FeedbackSubmission.org_id,
            ),
        )
        .where(FeedbackSubmission.id == bindparam("submission_id"))
        .where(FeedbackSubmission.source.in_(("assistant_feedback", "assistant_problem", "assistant_question")))
        .limit(1)
    )

    async with cross_org_session() as db:
        rows = (await db.execute(query, {"submission_id": submission_id})).all()
        if not rows:
            raise HTTPException(status_code=404, detail="Feedback submission not found")
        row = rows[0]
        triage_suggestions = await _platform_feedback_triage_suggestions(db, [row.id])
        link_row = (
            await db.execute(
                select(
                    FeedbackItem.id.label("id"),
                    FeedbackItem.title.label("title"),
                    FeedbackItem.status.label("status"),
                )
                .select_from(FeedbackItemLink)
                .join(FeedbackItem, FeedbackItem.id == FeedbackItemLink.item_id)
                .where(FeedbackItemLink.submission_id == submission_id)
                .order_by(FeedbackItemLink.created_at.desc())
                .limit(1)
            )
        ).first()

    return _platform_feedback_submission(row, triage_suggestions, linked=link_row)


@router.get("/feedback/items", response_model=list[PlatformFeedbackItem])
async def platform_feedback_items(
    search: str | None = Query(default=None),
    status: Literal["all", "active", "triage", "closed", "open", "resolved", "dismissed"] = Query(default="active"),
    kind: Literal["all", "feature", "bug", "ux_confusion", "docs", "support_pattern"] = Query(default="all"),
    limit: int = Query(default=25, ge=1, le=100),
    perms: UserPermissions = Depends(require_platform_admin()),
) -> list[PlatformFeedbackItem]:
    """Search canonical feedback items for duplicate/link triage."""
    await _audit(perms, "feedback:items", search)
    async with cross_org_session() as db:
        try:
            items = await search_feedback_items(
                db,
                search=search,
                status=status,
                kind=kind,
                limit=limit,
            )
            reporter_orgs = await _platform_feedback_item_reporter_orgs(db, [item.id for item in items])
        except Exception:
            logger.warning("platform_feedback_items_query_failed", exc_info=True)
            return []
        return [_platform_feedback_item(item, reporter_orgs.get(item.id, [])) for item in items]


@router.get("/feedback/items/{item_id}", response_model=PlatformFeedbackItemDetail)
async def platform_feedback_item_detail(
    item_id: int,
    perms: UserPermissions = Depends(require_platform_admin()),
) -> PlatformFeedbackItemDetail:
    """Return a canonical feedback item plus all linked customer evidence."""
    await _audit(perms, "feedback:item_detail", str(item_id))
    feedback_type = FeedbackSubmission.metadata_json["feedback_type"].astext
    severity = FeedbackSubmission.metadata_json["severity"].astext
    async with cross_org_session() as db:
        try:
            item = await get_feedback_item(db, item_id)
            rows = (
                await db.execute(
                    select(
                        FeedbackSubmission.id.label("id"),
                        FeedbackSubmission.org_id.label("org_id"),
                        PortalOrgModel.name.label("org_name"),
                        PortalOrgModel.slug.label("org_slug"),
                        FeedbackSubmission.user_id.label("user_id"),
                        PortalUserModel.email.label("user_email"),
                        PortalUserModel.display_name.label("user_display_name"),
                        FeedbackSubmission.source.label("source"),
                        FeedbackSubmission.status.label("status"),
                        FeedbackSubmission.raw_text.label("raw_text"),
                        feedback_type.label("feedback_type"),
                        severity.label("severity"),
                        FeedbackSubmission.page_url.label("page_url"),
                        FeedbackSubmission.route_id.label("route_id"),
                        FeedbackSubmission.locale.label("locale"),
                        FeedbackSubmission.viewport.label("viewport"),
                        FeedbackSubmission.created_at.label("created_at"),
                        FeedbackItemLink.link_type.label("link_type"),
                        FeedbackItemLink.created_at.label("linked_at"),
                    )
                    .select_from(FeedbackItemLink)
                    .join(FeedbackSubmission, FeedbackSubmission.id == FeedbackItemLink.submission_id)
                    .outerjoin(PortalOrgModel, PortalOrgModel.id == FeedbackSubmission.org_id)
                    .outerjoin(
                        PortalUserModel,
                        and_(
                            PortalUserModel.zitadel_user_id == FeedbackSubmission.user_id,
                            PortalUserModel.org_id == FeedbackSubmission.org_id,
                        ),
                    )
                    .where(FeedbackItemLink.item_id == item_id)
                    .order_by(FeedbackItemLink.created_at.desc())
                )
            ).all()
            reporter_orgs = await _platform_feedback_item_reporter_orgs(db, [item_id])
        except FeedbackItemNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Feedback item not found") from exc

        return PlatformFeedbackItemDetail(
            item=_platform_feedback_item(item, reporter_orgs.get(item_id, [])),
            submissions=[
                PlatformFeedbackLinkedSubmission(
                    id=r.id,
                    org_id=r.org_id,
                    org_name=r.org_name,
                    org_slug=r.org_slug,
                    user_id=r.user_id,
                    user_email=r.user_email,
                    user_display_name=r.user_display_name,
                    event_type=_feedback_event_type(r.source),
                    status=r.status,
                    raw_text=r.raw_text,
                    feedback_type=r.feedback_type,
                    severity=r.severity,
                    page_url=r.page_url,
                    route_id=r.route_id,
                    locale=r.locale,
                    viewport=r.viewport,
                    created_at=r.created_at,
                    link_type=r.link_type,
                    linked_at=r.linked_at,
                )
                for r in rows
            ],
        )


@router.patch("/feedback/items/{item_id}", response_model=PlatformFeedbackItem)
async def platform_feedback_update_item(
    item_id: int,
    body: PlatformFeedbackItemPatchIn,
    perms: UserPermissions = Depends(require_platform_admin()),
) -> PlatformFeedbackItem:
    await _audit(perms, "feedback:update_item", str(item_id))
    values = {key: _blank_to_none(value) for key, value in body.model_dump(exclude_unset=True).items()}
    async with cross_org_session() as db:
        try:
            item = await update_feedback_item(db, item_id, values)
        except FeedbackItemNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Feedback item not found") from exc
        return _platform_feedback_item(item)


@router.delete("/feedback/items/{item_id}", status_code=204)
async def platform_feedback_delete_item(
    item_id: int,
    perms: UserPermissions = Depends(require_platform_admin()),
) -> None:
    await _audit(perms, "feedback:delete_item", str(item_id))
    async with cross_org_session() as db:
        try:
            await delete_feedback_item(db, item_id)
        except FeedbackItemNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Feedback item not found") from exc


@router.post("/feedback/items/{item_id}/resolve", response_model=PlatformFeedbackResolveOut)
async def platform_feedback_resolve_item(
    item_id: int,
    body: PlatformFeedbackResolveIn,
    perms: UserPermissions = Depends(require_platform_admin()),
) -> PlatformFeedbackResolveOut:
    await _audit(perms, "feedback:resolve_item", str(item_id))
    async with cross_org_session() as db:
        try:
            item, notifications = await resolve_feedback_item(
                db,
                item_id,
                resolution_summary=body.resolution_summary,
                resolved_by=perms.user_id,
                channels=list(body.channels),
                subject=body.subject,
            )
        except FeedbackItemNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Feedback item not found") from exc
        recipient_count = len(
            {
                (notification.org_id, notification.user_id)
                for notification in notifications
                if notification.org_id is not None and notification.user_id is not None
            }
        )
        return PlatformFeedbackResolveOut(
            item=_platform_feedback_item(item),
            notifications=[
                PlatformFeedbackNotificationOut(
                    id=notification.id,
                    item_id=notification.item_id,
                    submission_id=notification.submission_id,
                    org_id=notification.org_id,
                    user_id=notification.user_id,
                    recipient_email=notification.recipient_email,
                    channel=notification.channel,
                    status=notification.status,
                    subject=notification.subject,
                    body=notification.body,
                    sent_at=notification.sent_at,
                    read_at=notification.read_at,
                    created_at=notification.created_at,
                )
                for notification in notifications
            ],
            recipient_count=recipient_count,
        )


@router.patch(
    "/feedback/submissions/{submission_id}",
    response_model=PlatformFeedbackActionResult,
)
async def platform_feedback_update_submission(
    submission_id: int,
    body: PlatformFeedbackSubmissionPatchIn,
    perms: UserPermissions = Depends(require_platform_admin()),
) -> PlatformFeedbackActionResult:
    await _audit(perms, "feedback:update_submission", str(submission_id))
    values = body.model_dump(exclude_unset=True, exclude_none=True)
    async with cross_org_session() as db:
        try:
            submission = await update_feedback_submission(db, submission_id, values)
        except FeedbackSubmissionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Feedback submission not found") from exc
        return PlatformFeedbackActionResult(submission_id=submission.id, status=submission.status)


@router.delete("/feedback/submissions/{submission_id}", status_code=204)
async def platform_feedback_delete_submission(
    submission_id: int,
    perms: UserPermissions = Depends(require_platform_admin()),
) -> None:
    await _audit(perms, "feedback:delete_submission", str(submission_id))
    async with cross_org_session() as db:
        try:
            await delete_feedback_submission(db, submission_id)
        except FeedbackSubmissionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Feedback submission not found") from exc


@router.post(
    "/feedback/submissions/{submission_id}/dismiss",
    response_model=PlatformFeedbackActionResult,
)
async def platform_feedback_dismiss_submission(
    submission_id: int,
    perms: UserPermissions = Depends(require_platform_admin()),
) -> PlatformFeedbackActionResult:
    await _audit(perms, "feedback:dismiss", str(submission_id))
    async with cross_org_session() as db:
        try:
            submission = await dismiss_feedback_submission(db, submission_id)
        except FeedbackSubmissionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Feedback submission not found") from exc
        return PlatformFeedbackActionResult(submission_id=submission.id, status=submission.status)


@router.post(
    "/feedback/submissions/{submission_id}/support",
    response_model=PlatformFeedbackActionResult,
)
async def platform_feedback_mark_support(
    submission_id: int,
    perms: UserPermissions = Depends(require_platform_admin()),
) -> PlatformFeedbackActionResult:
    await _audit(perms, "feedback:support", str(submission_id))
    async with cross_org_session() as db:
        try:
            submission = await mark_feedback_submission_support(db, submission_id)
        except FeedbackSubmissionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Feedback submission not found") from exc
        return PlatformFeedbackActionResult(submission_id=submission.id, status=submission.status)


@router.post(
    "/feedback/submissions/{submission_id}/items",
    response_model=PlatformFeedbackActionResult,
)
async def platform_feedback_create_item(
    submission_id: int,
    body: PlatformFeedbackCreateItemIn,
    perms: UserPermissions = Depends(require_platform_admin()),
) -> PlatformFeedbackActionResult:
    await _audit(perms, "feedback:create_item", str(submission_id))
    async with cross_org_session() as db:
        try:
            submission, item = await create_feedback_item_from_submission(
                db,
                submission_id=submission_id,
                kind=body.kind,
                title=body.title,
                summary=body.summary,
                area=body.area,
                link_type=body.link_type,
            )
        except FeedbackSubmissionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Feedback submission not found") from exc
        return PlatformFeedbackActionResult(submission_id=submission.id, status=submission.status, item_id=item.id)


@router.post(
    "/feedback/submissions/{submission_id}/links",
    response_model=PlatformFeedbackActionResult,
)
async def platform_feedback_link_item(
    submission_id: int,
    body: PlatformFeedbackLinkItemIn,
    perms: UserPermissions = Depends(require_platform_admin()),
) -> PlatformFeedbackActionResult:
    await _audit(perms, "feedback:link_item", str(submission_id))
    async with cross_org_session() as db:
        try:
            submission, item = await link_feedback_submission_to_item(
                db,
                submission_id=submission_id,
                item_id=body.item_id,
                link_type=body.link_type,
            )
        except FeedbackSubmissionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Feedback submission not found") from exc
        except FeedbackItemNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Feedback item not found") from exc
        return PlatformFeedbackActionResult(submission_id=submission.id, status=submission.status, item_id=item.id)


async def _platform_feedback_item_reporter_orgs(
    db: AsyncSession,
    item_ids: list[int],
) -> dict[int, list[PlatformFeedbackReporterOrg]]:
    if not item_ids:
        return {}
    rows = (
        await db.execute(
            select(
                FeedbackItemLink.item_id.label("item_id"),
                FeedbackSubmission.org_id.label("org_id"),
                PortalOrgModel.name.label("org_name"),
                PortalOrgModel.slug.label("org_slug"),
                func.count(func.distinct(FeedbackSubmission.user_id)).label("user_count"),
            )
            .select_from(FeedbackItemLink)
            .join(FeedbackSubmission, FeedbackSubmission.id == FeedbackItemLink.submission_id)
            .outerjoin(PortalOrgModel, PortalOrgModel.id == FeedbackSubmission.org_id)
            .where(FeedbackItemLink.item_id.in_(item_ids))
            .group_by(
                FeedbackItemLink.item_id,
                FeedbackSubmission.org_id,
                PortalOrgModel.name,
                PortalOrgModel.slug,
            )
            .order_by(FeedbackItemLink.item_id.asc(), PortalOrgModel.name.asc())
        )
    ).all()
    grouped: dict[int, list[PlatformFeedbackReporterOrg]] = {}
    for row in rows:
        grouped.setdefault(row.item_id, []).append(
            PlatformFeedbackReporterOrg(
                org_id=row.org_id,
                org_name=row.org_name,
                org_slug=row.org_slug,
                user_count=int(row.user_count or 0),
            )
        )
    return grouped


def _platform_feedback_item(
    item: FeedbackItem,
    reporter_orgs: list[PlatformFeedbackReporterOrg] | None = None,
) -> PlatformFeedbackItem:
    return PlatformFeedbackItem(
        id=item.id,
        kind=item.kind,
        title=item.title,
        summary=item.summary,
        status=item.status,
        area=item.area,
        priority_score=item.priority_score,
        org_count=item.org_count,
        user_count=item.user_count,
        shipped_at=item.shipped_at,
        resolution_summary=item.resolution_summary,
        resolved_at=item.resolved_at,
        resolved_by=item.resolved_by,
        notification_state=item.notification_state,
        reporter_orgs=reporter_orgs or [],
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _blank_to_none(value: object) -> object:
    if isinstance(value, str) and value == "":
        return None
    return value


# -----------------------------------------------------------------------------
# Subdomains overview (SPEC-PLATFORM-SUBDOMAINS-001)
# -----------------------------------------------------------------------------


class PlatformSubdomainItem(BaseModel):
    """Single subdomain entry, including its live-check status."""

    model_config = ConfigDict(from_attributes=True)

    subdomain: str
    url: str
    label: str
    description: str
    category: str
    host: str
    owner: str
    status: str
    """One of: 'up' (2xx/3xx), 'auth_required' (401/403), 'client_error' (4xx),
    'server_error' (5xx), 'unreachable' (network failure / timeout),
    'not_probed' (external/DNS-only entry that has no HTTP surface)."""
    status_code: int | None
    """HTTP response code, or null when unreachable / not probed."""


# Per-probe hard cap. The first ship used 3s/2s but mail.getklai.com (MX-only,
# no A record) and other DNS-only entries can hang DNS resolution longer than
# httpx's connect timeout in practice — we now skip those entries entirely
# (see `_should_probe`) and keep this conservative so probed entries return
# fast even on a real connect-stall.
_PROBE_PER_REQUEST_TIMEOUT_S = 2.0
_PROBE_CONNECT_TIMEOUT_S = 1.5

# Outer cap on the whole gather. asyncio.wait_for guarantees the endpoint
# responds within this wall-clock budget regardless of any pathological
# upstream — partial results win over a spinning frontend.
_PROBE_TOTAL_TIMEOUT_S = 6.0


def _should_probe(item: dict) -> bool:
    """Skip liveness probes for entries that have no HTTP surface.

    The catalogue tracks DNS-only entries (MX records, dead aliases) for
    inventory completeness, but probing them with HTTP either hangs on
    DNS resolution (no A record) or returns a meaningless 404 from the
    default nginx that resolves the alias. Either way the result is
    noise, not signal.
    """
    if item["category"] == "external":
        # All external entries are DNS-only in the current catalogue
        # (mail., cdn.). If a real external HTTP service joins, change
        # its category or add an explicit "probe" flag here.
        return False
    return True


async def _check_subdomain_status(client: httpx.AsyncClient, url: str) -> tuple[str, int | None]:
    """One liveness probe per subdomain. Never raises — failures map to
    ``('unreachable', None)`` so a single bad target does not break the
    whole overview.

    GET (not HEAD) because several Klai services 405 on HEAD (Vaultwarden,
    Grafana, Caddy admin endpoints). The conservative per-request timeout
    plus the outer wait_for cap in ``list_subdomains`` ensures the endpoint
    always returns within ~6s wall-clock even with multiple hanging upstreams.
    """
    try:
        # follow_redirects=False so a 301 → captive portal doesn't count as up.
        # Most Klai apex/auth endpoints respond 200/3xx directly.
        response = await client.get(url, follow_redirects=False)
    except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError):
        return ("unreachable", None)
    except Exception:
        return ("unreachable", None)
    code = response.status_code
    if 200 <= code < 400:
        return ("up", code)
    if code in (401, 403):
        # Vaultwarden, Grafana, mcp etc. legitimately 401/403 on root —
        # the service is up, it just requires auth.
        return ("auth_required", code)
    if 400 <= code < 500:
        return ("client_error", code)
    return ("server_error", code)


@router.get("/subdomains", response_model=list[PlatformSubdomainItem])
async def list_subdomains(
    perms: UserPermissions = Depends(require_platform_admin),
) -> list[PlatformSubdomainItem]:
    """Cross-tenant overview of every Klai-controlled subdomain.

    Combines the curated static list (Klai services, tooling, marketing)
    from ``app.services.platform_subdomains`` with dynamic tenant entries
    pulled from ``portal_orgs`` (one per active tenant), then probes each
    URL in parallel with a 3s GET to surface live status.

    Tenant entries are added per-tenant for ``<slug>.getklai.com`` only
    (the user-visible portal view). The chat- and docs- subdomains are
    container-instance specific and would explode the list — those live
    in the tenant detail page instead.
    """
    # Dynamic tenant entries — one per active tenant.
    tenant_items: list[dict] = []
    async with cross_org_session() as db:
        result = await db.execute(
            text("SELECT slug, name FROM portal_orgs WHERE deleted_at IS NULL AND slug IS NOT NULL ORDER BY slug")
        )
        for row in result.all():
            slug = row[0]
            name = row[1] or slug
            tenant_items.append(
                {
                    "subdomain": slug,
                    "url": f"https://{slug}.getklai.com",
                    "label": name,
                    "description": f"Tenant portal voor {name}.",
                    "category": "tenant",
                    "host": "core-01",
                    "owner": "tenant-admin",
                }
            )

    # Curated items first, then tenants — UI renders in this order.
    curated_items = [
        {
            "subdomain": s.subdomain,
            "url": s.url,
            "label": s.label,
            "description": s.description,
            "category": s.category,
            "host": s.host,
            "owner": s.owner,
        }
        for s in KLAI_SUBDOMAINS
    ]
    all_items = curated_items + tenant_items

    # Parallel liveness probes for entries that have an HTTP surface.
    # DNS-only / external entries (mail., cdn.) are skipped — see
    # _should_probe — so we don't burn the timeout budget on resolution
    # hangs that have no answer for the user.
    probe_indices = [i for i, item in enumerate(all_items) if _should_probe(item)]
    statuses: list[tuple[str, int | None]] = [("not_probed", None)] * len(all_items)

    if probe_indices:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_PROBE_PER_REQUEST_TIMEOUT_S, connect=_PROBE_CONNECT_TIMEOUT_S),
            verify=True,
            follow_redirects=False,
            headers={"User-Agent": "klai-platform-subdomain-check/1.0"},
        ) as client:
            try:
                probed = await asyncio.wait_for(
                    asyncio.gather(
                        *(_check_subdomain_status(client, all_items[i]["url"]) for i in probe_indices),
                        return_exceptions=False,
                    ),
                    timeout=_PROBE_TOTAL_TIMEOUT_S,
                )
            except TimeoutError:
                logger.warning(
                    "platform_subdomains_probe_total_timeout",
                    timeout_s=_PROBE_TOTAL_TIMEOUT_S,
                    probe_count=len(probe_indices),
                )
                probed = [("unreachable", None)] * len(probe_indices)

        for probe_idx, result in zip(probe_indices, probed, strict=True):
            statuses[probe_idx] = result

    logger.info(
        "platform_subdomains_listed",
        caller_user_id=perms.user_id,
        item_count=len(all_items),
        unreachable_count=sum(1 for s, _ in statuses if s == "unreachable"),
    )

    return [
        PlatformSubdomainItem(
            subdomain=item["subdomain"],
            url=item["url"],
            label=item["label"],
            description=item["description"],
            category=item["category"],
            host=item["host"],
            owner=item["owner"],
            status=status,
            status_code=code,
        )
        for item, (status, code) in zip(all_items, statuses, strict=True)
    ]


__all__ = ["router"]
