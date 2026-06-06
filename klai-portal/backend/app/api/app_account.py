"""App-facing account preferences API.

GET  /api/app/account/kb-preference  — read current KB scope preference
PATCH /api/app/account/kb-preference — update KB scope preference

The PATCH endpoint validates that all submitted kb_slugs belong to the caller's org,
increments kb_pref_version, and immediately invalidates the LiteLLM Redis cache key
so the next LLM call picks up the new settings without delay.
"""

import asyncio
import logging
from datetime import UTC, datetime

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import cross_org_session, get_db
from app.core.permissions import UserPermissions, get_caller
from app.klai_feedback.models import FeedbackItem, FeedbackItemLink, FeedbackNotification, FeedbackSubmission
from app.models.knowledge_bases import PortalKnowledgeBase
from app.models.portal import PortalUser
from app.models.templates import PortalTemplate
from app.platform_messaging.models import PlatformMessage, PlatformMessageParticipant, PlatformMessageThread
from app.platform_messaging.service import (
    PlatformMessageThreadNotFoundError,
    add_platform_message_reply,
    mark_platform_message_thread_read,
    user_can_access_thread,
)
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
    notification_id: int | None = None
    notification_body: str | None = None
    notification_read_at: datetime | None = None
    message_thread_id: int | None = None
    latest_update_at: datetime
    unread: bool = False


class AccountFeedbackUpdatesResponse(BaseModel):
    items: list[AccountFeedbackUpdateOut]
    unread_count: int = 0


class AccountFeedbackReadResponse(BaseModel):
    ok: bool = True
    notification_id: int
    read_at: datetime


class AccountFeedbackReadAllResponse(BaseModel):
    ok: bool = True
    read_count: int
    read_at: datetime


class AccountPlatformMessageThreadOut(BaseModel):
    id: int
    subject: str
    status: str
    origin_type: str
    feedback_submission_id: int | None = None
    feedback_item_id: int | None = None
    latest_message_body: str
    latest_message_sender_type: str
    latest_message_at: datetime
    last_read_at: datetime | None = None
    unread: bool = False
    created_at: datetime


class AccountPlatformMessageOut(BaseModel):
    id: int
    sender_type: str
    sender_user_id: str | None = None
    body: str
    created_at: datetime


class AccountPlatformMessageThreadDetailOut(BaseModel):
    thread: AccountPlatformMessageThreadOut
    messages: list[AccountPlatformMessageOut]


class AccountPlatformMessagesResponse(BaseModel):
    items: list[AccountPlatformMessageThreadOut]
    unread_count: int = 0


class AccountPlatformMessageReplyIn(BaseModel):
    body: str


class AccountPlatformMessageReplyOut(BaseModel):
    ok: bool = True
    message: AccountPlatformMessageOut


class AccountPlatformMessageReadResponse(BaseModel):
    ok: bool = True
    thread_id: int
    read_at: datetime


class AccountPlatformMessageReadAllResponse(BaseModel):
    ok: bool = True
    read_count: int
    read_at: datetime


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
            (
                select(PlatformMessageThread.id)
                .where(
                    PlatformMessageThread.org_id == FeedbackSubmission.org_id,
                    PlatformMessageThread.feedback_submission_id == FeedbackSubmission.id,
                )
                .order_by(PlatformMessageThread.last_message_at.desc(), PlatformMessageThread.id.desc())
                .limit(1)
                .correlate(FeedbackSubmission)
                .scalar_subquery()
            ).label("message_thread_id"),
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

    rows = result.all()
    item_ids = [row.item_id for row in rows if row.item_id is not None]
    notifications_by_item: dict[int, FeedbackNotification] = {}
    if item_ids:
        notification_rows = (
            await db.execute(
                select(FeedbackNotification)
                .where(
                    FeedbackNotification.org_id == perms.org_id,
                    FeedbackNotification.user_id == perms.user_id,
                    FeedbackNotification.channel == "in_app",
                    FeedbackNotification.item_id.in_(item_ids),
                )
                .order_by(FeedbackNotification.created_at.desc())
            )
        ).scalars()
        for notification in notification_rows:
            notifications_by_item.setdefault(notification.item_id, notification)

    items: list[AccountFeedbackUpdateOut] = []
    for row in rows:
        item_updated_at = row.item_updated_at
        notification = notifications_by_item.get(row.item_id) if row.item_id is not None else None
        latest_update_at = notification.created_at if notification is not None else item_updated_at or row.updated_at
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
                notification_id=notification.id if notification is not None else None,
                notification_body=notification.body if notification is not None else None,
                notification_read_at=notification.read_at if notification is not None else None,
                message_thread_id=row.message_thread_id,
                latest_update_at=latest_update_at,
                unread=notification is not None and notification.read_at is None,
            )
        )

    unread_count = sum(1 for item in items if item.unread)
    return AccountFeedbackUpdatesResponse(items=items, unread_count=unread_count)


@router.post("/feedback-updates/{notification_id}/read", response_model=AccountFeedbackReadResponse)
async def mark_feedback_update_read(
    notification_id: int,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> AccountFeedbackReadResponse:
    result = await db.execute(
        select(FeedbackNotification).where(
            FeedbackNotification.id == notification_id,
            FeedbackNotification.org_id == perms.org_id,
            FeedbackNotification.user_id == perms.user_id,
            FeedbackNotification.channel == "in_app",
        )
    )
    notification = result.scalar_one_or_none()
    if notification is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Feedback update not found")
    if notification.read_at is None:
        notification.read_at = datetime.now(UTC)
        await db.commit()
    read_at = notification.read_at or datetime.now(UTC)
    return AccountFeedbackReadResponse(notification_id=notification.id, read_at=read_at)


@router.post("/feedback-updates/read-all", response_model=AccountFeedbackReadAllResponse)
async def mark_all_feedback_updates_read(
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> AccountFeedbackReadAllResponse:
    result = await db.execute(
        select(FeedbackNotification).where(
            FeedbackNotification.org_id == perms.org_id,
            FeedbackNotification.user_id == perms.user_id,
            FeedbackNotification.channel == "in_app",
            FeedbackNotification.read_at.is_(None),
        )
    )
    notifications = list(result.scalars())
    read_at = datetime.now(UTC)
    for notification in notifications:
        notification.read_at = read_at
    if notifications:
        await db.commit()
    return AccountFeedbackReadAllResponse(read_count=len(notifications), read_at=read_at)


@router.post("/feedback-updates/{submission_id}/reply", response_model=AccountPlatformMessageThreadDetailOut)
async def reply_to_feedback_update(
    submission_id: int,
    body: AccountPlatformMessageReplyIn,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> AccountPlatformMessageThreadDetailOut:
    text = body.body.strip()
    if len(text) < 1 or len(text) > 4000:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Message body must be 1-4000 characters")

    caller = await _load_caller_user(perms, db)
    feedback_row = (
        await db.execute(
            select(
                FeedbackSubmission.id.label("submission_id"),
                FeedbackSubmission.raw_text.label("raw_text"),
                FeedbackItem.id.label("item_id"),
                FeedbackItem.title.label("item_title"),
            )
            .select_from(FeedbackSubmission)
            .outerjoin(FeedbackItemLink, FeedbackItemLink.submission_id == FeedbackSubmission.id)
            .outerjoin(FeedbackItem, FeedbackItem.id == FeedbackItemLink.item_id)
            .where(
                FeedbackSubmission.id == submission_id,
                FeedbackSubmission.org_id == perms.org_id,
                FeedbackSubmission.user_id == perms.user_id,
                FeedbackSubmission.source.in_(["assistant_problem", "assistant_feedback"]),
            )
            .order_by(FeedbackItem.updated_at.desc().nullslast())
            .limit(1)
        )
    ).first()
    if feedback_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Feedback update not found")

    fallback_subject_lines = feedback_row.raw_text.strip().splitlines()
    subject_text = feedback_row.item_title or (fallback_subject_lines[0] if fallback_subject_lines else "")
    subject = subject_text[:256] or "Feedback follow-up"
    async with cross_org_session() as message_db:
        existing_thread = (
            (
                await message_db.execute(
                    select(PlatformMessageThread)
                    .join(
                        PlatformMessageParticipant,
                        PlatformMessageParticipant.thread_id == PlatformMessageThread.id,
                    )
                    .where(
                        PlatformMessageThread.org_id == perms.org_id,
                        PlatformMessageThread.feedback_submission_id == submission_id,
                        PlatformMessageParticipant.org_id == perms.org_id,
                        PlatformMessageParticipant.user_id == perms.user_id,
                    )
                    .order_by(PlatformMessageThread.last_message_at.desc(), PlatformMessageThread.id.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if existing_thread is None:
            now = datetime.now(UTC)
            existing_thread = PlatformMessageThread(
                org_id=perms.org_id,
                subject=subject,
                created_by=perms.user_id,
                origin_type="feedback_submission",
                feedback_submission_id=submission_id,
                feedback_item_id=feedback_row.item_id,
                last_message_at=now,
            )
            message_db.add(existing_thread)
            await message_db.flush()
            message_db.add(
                PlatformMessageParticipant(
                    thread_id=existing_thread.id,
                    org_id=perms.org_id,
                    user_id=perms.user_id,
                    recipient_email=caller.email,
                    recipient_display_name=caller.display_name,
                )
            )
            message_db.add(
                PlatformMessage(
                    thread_id=existing_thread.id,
                    org_id=perms.org_id,
                    sender_type="user",
                    sender_user_id=perms.user_id,
                    body=text,
                    created_at=now,
                )
            )
            await message_db.flush()
        else:
            await add_platform_message_reply(
                message_db,
                thread_id=existing_thread.id,
                org_id=perms.org_id,
                sender_type="user",
                sender_user_id=perms.user_id,
                body=text,
            )
        detail = await _load_account_message_thread_detail(
            message_db,
            thread_id=existing_thread.id,
            org_id=perms.org_id,
            user_id=perms.user_id,
        )
        await message_db.commit()
        return detail


def _thread_out(row, latest_admin_at: datetime | None = None) -> AccountPlatformMessageThreadOut:
    last_read_at = row.last_read_at
    unread = latest_admin_at is not None and (last_read_at is None or latest_admin_at > last_read_at)
    return AccountPlatformMessageThreadOut(
        id=row.id,
        subject=row.subject,
        status=row.status,
        origin_type=row.origin_type,
        feedback_submission_id=row.feedback_submission_id,
        feedback_item_id=row.feedback_item_id,
        latest_message_body=row.latest_message_body,
        latest_message_sender_type=row.latest_message_sender_type,
        latest_message_at=row.latest_message_at,
        last_read_at=last_read_at,
        unread=unread,
        created_at=row.created_at,
    )


async def _load_account_message_thread_detail(
    db: AsyncSession,
    *,
    thread_id: int,
    org_id: int,
    user_id: str,
) -> AccountPlatformMessageThreadDetailOut:
    row = (
        await db.execute(
            _account_thread_select().where(
                PlatformMessageParticipant.org_id == org_id,
                PlatformMessageParticipant.user_id == user_id,
                PlatformMessageThread.id == thread_id,
            )
        )
    ).first()
    if row is None:
        raise PlatformMessageThreadNotFoundError()
    thread = _thread_out(row, row.latest_admin_at)
    messages = (
        (
            await db.execute(
                select(PlatformMessage)
                .where(PlatformMessage.thread_id == thread_id, PlatformMessage.org_id == org_id)
                .order_by(PlatformMessage.created_at.asc(), PlatformMessage.id.asc())
            )
        )
        .scalars()
        .all()
    )
    return AccountPlatformMessageThreadDetailOut(
        thread=thread,
        messages=[
            AccountPlatformMessageOut(
                id=message.id,
                sender_type=message.sender_type,
                sender_user_id=message.sender_user_id,
                body=message.body,
                created_at=message.created_at,
            )
            for message in messages
        ],
    )


def _account_thread_select():
    latest_body = (
        select(PlatformMessage.body)
        .where(PlatformMessage.thread_id == PlatformMessageThread.id)
        .order_by(PlatformMessage.created_at.desc(), PlatformMessage.id.desc())
        .limit(1)
        .correlate(PlatformMessageThread)
        .scalar_subquery()
    )
    latest_sender_type = (
        select(PlatformMessage.sender_type)
        .where(PlatformMessage.thread_id == PlatformMessageThread.id)
        .order_by(PlatformMessage.created_at.desc(), PlatformMessage.id.desc())
        .limit(1)
        .correlate(PlatformMessageThread)
        .scalar_subquery()
    )
    latest_admin_at = (
        select(func.max(PlatformMessage.created_at))
        .where(
            PlatformMessage.thread_id == PlatformMessageThread.id,
            PlatformMessage.sender_type.in_(("platform_admin", "system")),
        )
        .correlate(PlatformMessageThread)
        .scalar_subquery()
    )
    return (
        select(
            PlatformMessageThread.id.label("id"),
            PlatformMessageThread.subject.label("subject"),
            PlatformMessageThread.status.label("status"),
            PlatformMessageThread.origin_type.label("origin_type"),
            PlatformMessageThread.feedback_submission_id.label("feedback_submission_id"),
            PlatformMessageThread.feedback_item_id.label("feedback_item_id"),
            PlatformMessageThread.last_message_at.label("latest_message_at"),
            PlatformMessageThread.created_at.label("created_at"),
            PlatformMessageParticipant.last_read_at.label("last_read_at"),
            latest_body.label("latest_message_body"),
            latest_sender_type.label("latest_message_sender_type"),
            latest_admin_at.label("latest_admin_at"),
        )
        .select_from(PlatformMessageParticipant)
        .join(PlatformMessageThread, PlatformMessageThread.id == PlatformMessageParticipant.thread_id)
    )


@router.get("/messages", response_model=AccountPlatformMessagesResponse)
async def get_platform_messages(
    limit: int = 50,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> AccountPlatformMessagesResponse:
    safe_limit = max(1, min(limit, 100))
    rows = (
        await db.execute(
            _account_thread_select()
            .where(
                PlatformMessageParticipant.org_id == perms.org_id,
                PlatformMessageParticipant.user_id == perms.user_id,
            )
            .order_by(PlatformMessageThread.last_message_at.desc())
            .limit(safe_limit)
        )
    ).all()
    items = [_thread_out(row, row.latest_admin_at) for row in rows]
    return AccountPlatformMessagesResponse(items=items, unread_count=sum(1 for item in items if item.unread))


@router.get("/messages/{thread_id}", response_model=AccountPlatformMessageThreadDetailOut)
async def get_platform_message_thread(
    thread_id: int,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> AccountPlatformMessageThreadDetailOut:
    if not await user_can_access_thread(db, thread_id=thread_id, org_id=perms.org_id, user_id=perms.user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message thread not found")
    try:
        return await _load_account_message_thread_detail(
            db,
            thread_id=thread_id,
            org_id=perms.org_id,
            user_id=perms.user_id,
        )
    except PlatformMessageThreadNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message thread not found") from exc


@router.post("/messages/{thread_id}/reply", response_model=AccountPlatformMessageReplyOut)
async def reply_to_platform_message_thread(
    thread_id: int,
    body: AccountPlatformMessageReplyIn,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> AccountPlatformMessageReplyOut:
    text = body.body.strip()
    if len(text) < 1 or len(text) > 4000:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Message body must be 1-4000 characters")
    if not await user_can_access_thread(db, thread_id=thread_id, org_id=perms.org_id, user_id=perms.user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message thread not found")
    try:
        message = await add_platform_message_reply(
            db,
            thread_id=thread_id,
            org_id=perms.org_id,
            sender_type="user",
            sender_user_id=perms.user_id,
            body=text,
        )
    except PlatformMessageThreadNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message thread not found") from exc
    result = AccountPlatformMessageReplyOut(
        message=AccountPlatformMessageOut(
            id=message.id,
            sender_type=message.sender_type,
            sender_user_id=message.sender_user_id,
            body=message.body,
            created_at=message.created_at,
        )
    )
    await db.commit()
    return result


@router.post("/messages/{thread_id}/read", response_model=AccountPlatformMessageReadResponse)
async def mark_platform_message_read(
    thread_id: int,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> AccountPlatformMessageReadResponse:
    try:
        read_at = await mark_platform_message_thread_read(
            db,
            thread_id=thread_id,
            org_id=perms.org_id,
            user_id=perms.user_id,
        )
    except PlatformMessageThreadNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message thread not found") from exc
    await db.commit()
    return AccountPlatformMessageReadResponse(thread_id=thread_id, read_at=read_at)


@router.post("/messages/read-all", response_model=AccountPlatformMessageReadAllResponse)
async def mark_all_platform_messages_read(
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> AccountPlatformMessageReadAllResponse:
    latest_admin_at = (
        select(func.max(PlatformMessage.created_at))
        .where(
            PlatformMessage.thread_id == PlatformMessageParticipant.thread_id,
            PlatformMessage.sender_type.in_(("platform_admin", "system")),
        )
        .correlate(PlatformMessageParticipant)
        .scalar_subquery()
    )
    rows = (
        await db.execute(
            select(PlatformMessageParticipant, latest_admin_at.label("latest_admin_at")).where(
                PlatformMessageParticipant.org_id == perms.org_id,
                PlatformMessageParticipant.user_id == perms.user_id,
            )
        )
    ).all()
    read_at = datetime.now(UTC)
    read_count = 0
    for participant, latest_at in rows:
        if latest_at is not None and (participant.last_read_at is None or latest_at > participant.last_read_at):
            participant.last_read_at = read_at
            read_count += 1
    if read_count:
        await db.commit()
    return AccountPlatformMessageReadAllResponse(read_count=read_count, read_at=read_at)


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
