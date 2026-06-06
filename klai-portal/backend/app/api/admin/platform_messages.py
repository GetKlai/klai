"""Platform-admin in-app messaging endpoints."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import bindparam, func, or_, select

from app.core.database import cross_org_session
from app.core.permissions import UserPermissions, require_platform_admin
from app.models.portal import PortalOrg, PortalUser
from app.platform_messaging.models import PlatformMessage, PlatformMessageParticipant, PlatformMessageThread
from app.platform_messaging.service import (
    PlatformMessageRecipientError,
    PlatformMessageThreadNotFoundError,
    add_platform_message_reply,
    create_platform_message_thread,
    get_platform_message_thread,
    mark_platform_thread_admin_read,
)
from app.services.audit import log_event

router = APIRouter(prefix="/platform/messages", tags=["platform-admin-messages"])


class PlatformMessageRecipientOut(BaseModel):
    user_id: str
    email: str | None
    display_name: str | None
    last_read_at: datetime | None


class PlatformMessageOut(BaseModel):
    id: int
    sender_type: str
    sender_user_id: str | None
    sender_display_name: str | None = None
    body: str
    created_at: datetime


class PlatformMessageThreadOut(BaseModel):
    id: int
    org_id: int
    org_name: str | None
    org_slug: str | None
    subject: str
    origin_type: str
    feedback_submission_id: int | None = None
    feedback_item_id: int | None = None
    recipient_count: int
    latest_message_body: str
    latest_message_sender_type: str
    latest_message_at: datetime
    latest_user_message_at: datetime | None = None
    latest_admin_message_at: datetime | None = None
    unread_for_admin: bool = False
    created_by: str
    created_at: datetime


class PlatformMessageThreadDetailOut(BaseModel):
    thread: PlatformMessageThreadOut
    recipients: list[PlatformMessageRecipientOut]
    messages: list[PlatformMessageOut]


class PlatformMessageThreadCreateIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    org_id: int
    user_ids: list[str] = Field(..., min_length=1, max_length=50)
    subject: str = Field(..., min_length=1, max_length=256)
    body: str = Field(..., min_length=1, max_length=4000)
    feedback_submission_id: int | None = None
    feedback_item_id: int | None = None


class PlatformMessageReplyIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    body: str = Field(..., min_length=1, max_length=4000)


async def _audit(perms: UserPermissions, action: str, resource_id: str | None = None) -> None:
    audit_resource_id = resource_id or "collection"
    await log_event(
        org_id=perms.org_id,
        actor=perms.user_id,
        action=f"platform_admin.messages.{action}",
        resource_type="platform_message_thread",
        resource_id=audit_resource_id,
        details=None,
    )


def _latest_body_subquery():
    return (
        select(PlatformMessage.body)
        .where(PlatformMessage.thread_id == PlatformMessageThread.id)
        .order_by(PlatformMessage.created_at.desc(), PlatformMessage.id.desc())
        .limit(1)
        .correlate(PlatformMessageThread)
        .scalar_subquery()
    )


def _latest_sender_type_subquery():
    return (
        select(PlatformMessage.sender_type)
        .where(PlatformMessage.thread_id == PlatformMessageThread.id)
        .order_by(PlatformMessage.created_at.desc(), PlatformMessage.id.desc())
        .limit(1)
        .correlate(PlatformMessageThread)
        .scalar_subquery()
    )


def _latest_user_message_at_subquery():
    return (
        select(func.max(PlatformMessage.created_at))
        .where(
            PlatformMessage.thread_id == PlatformMessageThread.id,
            PlatformMessage.sender_type == "user",
        )
        .correlate(PlatformMessageThread)
        .scalar_subquery()
    )


def _latest_admin_message_at_subquery():
    return (
        select(func.max(PlatformMessage.created_at))
        .where(
            PlatformMessage.thread_id == PlatformMessageThread.id,
            PlatformMessage.sender_type.in_(("platform_admin", "system")),
        )
        .correlate(PlatformMessageThread)
        .scalar_subquery()
    )


def _recipient_count_subquery():
    return (
        select(func.count(PlatformMessageParticipant.user_id))
        .where(PlatformMessageParticipant.thread_id == PlatformMessageThread.id)
        .correlate(PlatformMessageThread)
        .scalar_subquery()
    )


def _thread_select():
    return (
        select(
            PlatformMessageThread.id.label("id"),
            PlatformMessageThread.org_id.label("org_id"),
            PortalOrg.name.label("org_name"),
            PortalOrg.slug.label("org_slug"),
            PlatformMessageThread.subject.label("subject"),
            PlatformMessageThread.origin_type.label("origin_type"),
            PlatformMessageThread.feedback_submission_id.label("feedback_submission_id"),
            PlatformMessageThread.feedback_item_id.label("feedback_item_id"),
            PlatformMessageThread.created_by.label("created_by"),
            PlatformMessageThread.created_at.label("created_at"),
            PlatformMessageThread.last_message_at.label("latest_message_at"),
            PlatformMessageThread.admin_read_at.label("admin_read_at"),
            _latest_body_subquery().label("latest_message_body"),
            _latest_sender_type_subquery().label("latest_message_sender_type"),
            _latest_user_message_at_subquery().label("latest_user_message_at"),
            _latest_admin_message_at_subquery().label("latest_admin_message_at"),
            _recipient_count_subquery().label("recipient_count"),
        )
        .select_from(PlatformMessageThread)
        .outerjoin(PortalOrg, PortalOrg.id == PlatformMessageThread.org_id)
    )


def _thread_out(row) -> PlatformMessageThreadOut:
    # The admin has "seen" everything up to the later of their last reply and
    # the last time they opened the thread. A newer user message = unread.
    admin_read_at = getattr(row, "admin_read_at", None)
    seen_candidates = [t for t in (row.latest_admin_message_at, admin_read_at) if t is not None]
    seen_at = max(seen_candidates) if seen_candidates else None
    unread_for_admin = row.latest_user_message_at is not None and (
        seen_at is None or row.latest_user_message_at > seen_at
    )
    return PlatformMessageThreadOut(
        id=row.id,
        org_id=row.org_id,
        org_name=row.org_name,
        org_slug=row.org_slug,
        subject=row.subject,
        origin_type=row.origin_type,
        feedback_submission_id=row.feedback_submission_id,
        feedback_item_id=row.feedback_item_id,
        recipient_count=row.recipient_count,
        latest_message_body=row.latest_message_body,
        latest_message_sender_type=row.latest_message_sender_type,
        latest_message_at=row.latest_message_at,
        latest_user_message_at=row.latest_user_message_at,
        latest_admin_message_at=row.latest_admin_message_at,
        unread_for_admin=unread_for_admin,
        created_by=row.created_by,
        created_at=row.created_at,
    )


async def _load_thread_detail(db, thread_id: int) -> PlatformMessageThreadDetailOut:
    row = (await db.execute(_thread_select().where(PlatformMessageThread.id == thread_id))).first()
    if row is None:
        raise PlatformMessageThreadNotFoundError()
    recipients = (
        (
            await db.execute(
                select(PlatformMessageParticipant)
                .where(PlatformMessageParticipant.thread_id == thread_id)
                .order_by(PlatformMessageParticipant.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    messages = (
        (
            await db.execute(
                select(PlatformMessage)
                .where(PlatformMessage.thread_id == thread_id)
                .order_by(PlatformMessage.created_at.asc(), PlatformMessage.id.asc())
            )
        )
        .scalars()
        .all()
    )
    return PlatformMessageThreadDetailOut(
        thread=_thread_out(row),
        recipients=[
            PlatformMessageRecipientOut(
                user_id=recipient.user_id,
                email=recipient.recipient_email,
                display_name=recipient.recipient_display_name,
                last_read_at=recipient.last_read_at,
            )
            for recipient in recipients
        ],
        messages=[
            PlatformMessageOut(
                id=message.id,
                sender_type=message.sender_type,
                sender_user_id=message.sender_user_id,
                sender_display_name=message.sender_display_name,
                body=message.body,
                created_at=message.created_at,
            )
            for message in messages
        ],
    )


@router.get("/threads", response_model=list[PlatformMessageThreadOut])
async def platform_message_threads(
    search: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
    perms: UserPermissions = Depends(require_platform_admin()),
) -> list[PlatformMessageThreadOut]:
    await _audit(perms, "list")
    params: dict[str, object] = {"limit": limit}
    query = _thread_select().order_by(PlatformMessageThread.last_message_at.desc()).limit(bindparam("limit"))
    if search:
        params["q"] = f"%{search}%"
        q = bindparam("q")
        query = query.outerjoin(
            PlatformMessageParticipant,
            PlatformMessageParticipant.thread_id == PlatformMessageThread.id,
        ).where(
            or_(
                PlatformMessageThread.subject.ilike(q),
                PortalOrg.name.ilike(q),
                PortalOrg.slug.ilike(q),
                PlatformMessageParticipant.recipient_email.ilike(q),
                PlatformMessageParticipant.recipient_display_name.ilike(q),
            )
        )
    async with cross_org_session() as db:
        rows = (await db.execute(query, params)).all()
    return [_thread_out(row) for row in rows]


@router.get("/threads/{thread_id}", response_model=PlatformMessageThreadDetailOut)
async def platform_message_thread_detail(
    thread_id: int,
    perms: UserPermissions = Depends(require_platform_admin()),
) -> PlatformMessageThreadDetailOut:
    await _audit(perms, "detail", str(thread_id))
    async with cross_org_session() as db:
        try:
            return await _load_thread_detail(db, thread_id)
        except PlatformMessageThreadNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Message thread not found") from exc


async def _resolve_admin_display_name(db, user_id: str) -> str | None:
    """Snapshot the sending admin's display name (cross-org; Klai staff org)."""
    return await db.scalar(select(PortalUser.display_name).where(PortalUser.zitadel_user_id == user_id))


@router.post("/threads", response_model=PlatformMessageThreadDetailOut, status_code=status.HTTP_201_CREATED)
async def platform_message_thread_create(
    body: PlatformMessageThreadCreateIn,
    perms: UserPermissions = Depends(require_platform_admin()),
) -> PlatformMessageThreadDetailOut:
    await _audit(perms, "create")
    async with cross_org_session() as db:
        try:
            thread = await create_platform_message_thread(
                db,
                org_id=body.org_id,
                user_ids=body.user_ids,
                subject=body.subject,
                body=body.body,
                created_by=perms.user_id,
                sender_display_name=await _resolve_admin_display_name(db, perms.user_id),
                feedback_submission_id=body.feedback_submission_id,
                feedback_item_id=body.feedback_item_id,
            )
            detail = await _load_thread_detail(db, thread.id)
            await db.commit()
            return detail
        except PlatformMessageRecipientError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/threads/{thread_id}/reply", response_model=PlatformMessageThreadDetailOut)
async def platform_message_thread_reply(
    thread_id: int,
    body: PlatformMessageReplyIn,
    perms: UserPermissions = Depends(require_platform_admin()),
) -> PlatformMessageThreadDetailOut:
    await _audit(perms, "reply", str(thread_id))
    async with cross_org_session() as db:
        try:
            thread = await get_platform_message_thread(db, thread_id)
            await add_platform_message_reply(
                db,
                thread_id=thread_id,
                org_id=thread.org_id,
                sender_type="platform_admin",
                sender_user_id=perms.user_id,
                body=body.body,
                sender_display_name=await _resolve_admin_display_name(db, perms.user_id),
            )
            detail = await _load_thread_detail(db, thread_id)
            await db.commit()
            return detail
        except PlatformMessageThreadNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Message thread not found") from exc


@router.post("/threads/{thread_id}/read", response_model=PlatformMessageThreadDetailOut)
async def platform_message_thread_mark_read(
    thread_id: int,
    perms: UserPermissions = Depends(require_platform_admin()),
) -> PlatformMessageThreadDetailOut:
    await _audit(perms, "read", str(thread_id))
    async with cross_org_session() as db:
        try:
            await mark_platform_thread_admin_read(db, thread_id=thread_id)
            detail = await _load_thread_detail(db, thread_id)
            await db.commit()
            return detail
        except PlatformMessageThreadNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Message thread not found") from exc


@router.patch("/threads/{thread_id}/messages/{message_id}", response_model=PlatformMessageThreadDetailOut)
async def platform_message_edit(
    thread_id: int,
    message_id: int,
    body: PlatformMessageReplyIn,
    perms: UserPermissions = Depends(require_platform_admin()),
) -> PlatformMessageThreadDetailOut:
    """Edit a Klai-team message the calling admin sent themselves."""
    await _audit(perms, "edit", str(thread_id))
    async with cross_org_session() as db:
        message = (
            await db.execute(
                select(PlatformMessage).where(
                    PlatformMessage.id == message_id,
                    PlatformMessage.thread_id == thread_id,
                    PlatformMessage.sender_user_id == perms.user_id,
                    PlatformMessage.sender_type.in_(("platform_admin", "system")),
                )
            )
        ).scalar_one_or_none()
        if message is None:
            raise HTTPException(status_code=404, detail="Message not found")
        message.body = body.body
        detail = await _load_thread_detail(db, thread_id)
        await db.commit()
        return detail
