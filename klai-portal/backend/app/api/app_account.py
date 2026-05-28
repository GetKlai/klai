"""App-facing account preferences API.

GET  /api/app/account/kb-preference  — read current KB scope preference
PATCH /api/app/account/kb-preference — update KB scope preference

The PATCH endpoint validates that all submitted kb_slugs belong to the caller's org,
increments kb_pref_version, and immediately invalidates the LiteLLM Redis cache key
so the next LLM call picks up the new settings without delay.
"""

import asyncio
import logging
from datetime import datetime

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.permissions import UserPermissions, get_caller
from app.klai_feedback.models import FeedbackItem, FeedbackItemLink, FeedbackSubmission
from app.models.knowledge_bases import PortalKnowledgeBase
from app.models.portal import PortalUser
from app.models.templates import PortalTemplate
from app.services.litellm_cache import invalidate_templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/app/account", tags=["app-account"])


async def _load_caller_user(perms: UserPermissions, db: AsyncSession) -> PortalUser:
    """Load the caller's PortalUser row for read+mutate paths.

    ``perms`` is built from this same row by ``get_caller``, so a miss is a
    server-side invariant violation -> 500.
    """
    result = await db.execute(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == perms.user_id,
            PortalUser.org_id == perms.org_id,
        )
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Caller user not found",
        )
    return user


async def _invalidate_litellm_kb_cache(org_id: int, librechat_user_id: str) -> None:
    """Delete the LiteLLM version pointer key so the next LLM call fetches fresh KB prefs.

    Fire-and-forget — failures are logged but never bubble up to the caller.
    Key format mirrors klai_knowledge.py: kb_ver:{org_id}:{user_id}.
    """
    try:
        r = aioredis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password or None,
            socket_connect_timeout=1.0,
        )
        async with r:
            await r.delete(f"kb_ver:{org_id}:{librechat_user_id}")
    except Exception as exc:
        logger.warning(
            "KB pref: Redis cache invalidation failed (%s) — hook picks up within 30s",
            exc,
            exc_info=True,
        )


# -- Pydantic schemas ---------------------------------------------------------


class KBPreferenceOut(BaseModel):
    kb_retrieval_enabled: bool
    kb_personal_enabled: bool
    kb_slugs_filter: list[str] | None
    kb_narrow: bool
    kb_pref_version: int
    # SPEC-CHAT-TEMPLATES-001: active prompt-template IDs. NULL = none active.
    active_template_ids: list[int] | None = None


class KBPreferencePatch(BaseModel):
    kb_retrieval_enabled: bool | None = None
    kb_personal_enabled: bool | None = None
    kb_slugs_filter: list[str] | None = None
    kb_narrow: bool | None = None
    active_template_ids: list[int] | None = None


class AccountFeedbackUpdateOut(BaseModel):
    submission_id: int
    source: str
    raw_text: str
    submission_status: str
    created_at: datetime
    updated_at: datetime
    page_url: str | None = None
    route_id: str | None = None
    item_id: int | None = None
    item_kind: str | None = None
    item_title: str | None = None
    item_summary: str | None = None
    item_status: str | None = None
    item_updated_at: datetime | None = None
    latest_update_at: datetime
    unread: bool = False


class AccountFeedbackUpdatesResponse(BaseModel):
    items: list[AccountFeedbackUpdateOut]
    unread_count: int = 0


async def _validate_and_normalize_template_ids(
    tpl_ids: list[int] | None,
    org_id: int,
    db: AsyncSession,
) -> list[int] | None:
    """Dedupe (preserving order) and validate every template ID against caller's org.

    Normalizes an empty list to None — "no active templates" is expressed as NULL
    in the DB, never as `[]`. Raises 400 if any ID belongs to another org or
    does not exist.
    """
    if tpl_ids is None or len(tpl_ids) == 0:
        return None

    seen: set[int] = set()
    deduped: list[int] = []
    for tid in tpl_ids:
        if tid not in seen:
            seen.add(tid)
            deduped.append(tid)

    result = await db.execute(
        select(PortalTemplate.id).where(
            PortalTemplate.org_id == org_id,
            PortalTemplate.id.in_(deduped),
        )
    )
    valid_ids = {row[0] for row in result}
    invalid = set(deduped) - valid_ids
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown template IDs for this org: {sorted(invalid)}",
        )

    return deduped


# -- Endpoints ----------------------------------------------------------------


@router.get("/kb-preference", response_model=KBPreferenceOut)
async def get_kb_preference(
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> KBPreferenceOut:
    """Return the caller's current KB scope preference."""
    user = await _load_caller_user(perms, db)
    return KBPreferenceOut(
        kb_retrieval_enabled=user.kb_retrieval_enabled,
        kb_personal_enabled=user.kb_personal_enabled,
        kb_slugs_filter=user.kb_slugs_filter,
        kb_narrow=user.kb_narrow,
        kb_pref_version=user.kb_pref_version,
        active_template_ids=user.active_template_ids,
    )


@router.get("/feedback-updates", response_model=AccountFeedbackUpdatesResponse)
async def get_feedback_updates(
    limit: int = 50,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> AccountFeedbackUpdatesResponse:
    """Return the caller's own feedback/problem reports for the account page."""
    safe_limit = max(1, min(limit, 100))
    result = await db.execute(
        select(
            FeedbackSubmission.id.label("submission_id"),
            FeedbackSubmission.source.label("source"),
            FeedbackSubmission.raw_text.label("raw_text"),
            FeedbackSubmission.status.label("submission_status"),
            FeedbackSubmission.created_at.label("created_at"),
            FeedbackSubmission.updated_at.label("updated_at"),
            FeedbackSubmission.page_url.label("page_url"),
            FeedbackSubmission.route_id.label("route_id"),
            FeedbackItem.id.label("item_id"),
            FeedbackItem.kind.label("item_kind"),
            FeedbackItem.title.label("item_title"),
            FeedbackItem.summary.label("item_summary"),
            FeedbackItem.status.label("item_status"),
            FeedbackItem.updated_at.label("item_updated_at"),
        )
        .select_from(FeedbackSubmission)
        .outerjoin(FeedbackItemLink, FeedbackItemLink.submission_id == FeedbackSubmission.id)
        .outerjoin(FeedbackItem, FeedbackItem.id == FeedbackItemLink.item_id)
        .where(
            FeedbackSubmission.org_id == perms.org_id,
            FeedbackSubmission.user_id == perms.user_id,
            FeedbackSubmission.source.in_(["assistant_problem", "assistant_feedback"]),
        )
        .order_by(FeedbackSubmission.created_at.desc())
        .limit(safe_limit)
    )

    items: list[AccountFeedbackUpdateOut] = []
    for row in result.all():
        item_updated_at = row.item_updated_at
        latest_update_at = item_updated_at or row.updated_at
        items.append(
            AccountFeedbackUpdateOut(
                submission_id=row.submission_id,
                source=row.source,
                raw_text=row.raw_text,
                submission_status=row.submission_status,
                created_at=row.created_at,
                updated_at=row.updated_at,
                page_url=row.page_url,
                route_id=row.route_id,
                item_id=row.item_id,
                item_kind=row.item_kind,
                item_title=row.item_title,
                item_summary=row.item_summary,
                item_status=row.item_status,
                item_updated_at=item_updated_at,
                latest_update_at=latest_update_at,
                unread=False,
            )
        )

    return AccountFeedbackUpdatesResponse(items=items, unread_count=0)


@router.patch("/kb-preference", response_model=KBPreferenceOut)
async def patch_kb_preference(
    body: KBPreferencePatch,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> KBPreferenceOut:
    """Update the caller's KB scope preference.

    Validates that any submitted kb_slugs belong to the caller's own org.
    Empty list is normalized to null (null means all org KBs).
    Increments kb_pref_version on every successful save.
    """
    user = await _load_caller_user(perms, db)

    if body.kb_retrieval_enabled is not None:
        user.kb_retrieval_enabled = body.kb_retrieval_enabled

    if body.kb_personal_enabled is not None:
        user.kb_personal_enabled = body.kb_personal_enabled

    if body.kb_narrow is not None:
        user.kb_narrow = body.kb_narrow

    if "kb_slugs_filter" in body.model_fields_set:
        slugs = body.kb_slugs_filter

        # Tri-state contract:
        #   None  = "all org KBs" (default; client did not narrow)
        #   []    = "no org KBs"  (user explicitly turned all off)
        #   [..]  = explicit subset
        #
        # The earlier collapse `[] -> None` here was a silent destruction of
        # user intent: when the user turned off the LAST org KB the client
        # sent `[]`, the server stored it as `None`, the GET round-trip
        # returned `None`, and the next render flipped every collection back
        # to "on". The frontend's toggleSlug comment explicitly warns
        # "DO NOT collapse empty to null" — this commit makes the server
        # honour that contract.
        if slugs is not None and len(slugs) > 0:
            # Validate all slugs belong to the caller's org (REQ-N3)
            result = await db.execute(
                select(PortalKnowledgeBase.slug).where(
                    PortalKnowledgeBase.org_id == perms.org_id,
                    PortalKnowledgeBase.slug.in_(slugs),
                )
            )
            valid_slugs = {row[0] for row in result}
            invalid = set(slugs) - valid_slugs
            if invalid:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Unknown KB slugs for this org: {sorted(invalid)}",
                )

        user.kb_slugs_filter = slugs

    # SPEC-CHAT-TEMPLATES-001 REQ-TEMPLATES-CRUD-E5
    active_templates_changed = False
    if "active_template_ids" in body.model_fields_set:
        active_templates_changed = True
        user.active_template_ids = await _validate_and_normalize_template_ids(
            body.active_template_ids, org_id=perms.org_id, db=db
        )

    user.kb_pref_version += 1
    await db.commit()

    if user.librechat_user_id:
        asyncio.get_running_loop().create_task(_invalidate_litellm_kb_cache(perms.org_id, user.librechat_user_id))
        if active_templates_changed:
            asyncio.get_running_loop().create_task(invalidate_templates(perms.org_id, user.librechat_user_id))

    return KBPreferenceOut(
        kb_retrieval_enabled=user.kb_retrieval_enabled,
        kb_personal_enabled=user.kb_personal_enabled,
        kb_slugs_filter=user.kb_slugs_filter,
        kb_narrow=user.kb_narrow,
        kb_pref_version=user.kb_pref_version,
        active_template_ids=user.active_template_ids,
    )
