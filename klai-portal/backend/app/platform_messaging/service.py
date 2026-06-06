from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.klai_feedback.models import FeedbackItem, FeedbackSubmission
from app.models.portal import PortalUser
from app.platform_messaging.models import PlatformMessage, PlatformMessageParticipant, PlatformMessageThread


class PlatformMessageThreadNotFoundError(Exception):
    pass


class PlatformMessageRecipientError(Exception):
    pass


async def get_platform_message_thread(db: AsyncSession, thread_id: int) -> PlatformMessageThread:
    thread = (
        await db.execute(select(PlatformMessageThread).where(PlatformMessageThread.id == thread_id))
    ).scalar_one_or_none()
    if thread is None:
        raise PlatformMessageThreadNotFoundError()
    return thread


async def create_platform_message_thread(
    db: AsyncSession,
    *,
    org_id: int,
    user_ids: list[str],
    subject: str,
    body: str,
    created_by: str,
    sender_display_name: str | None = None,
    feedback_submission_id: int | None = None,
    feedback_item_id: int | None = None,
) -> PlatformMessageThread:
    normalized_user_ids = list(dict.fromkeys(user_id for user_id in user_ids if user_id))
    if not normalized_user_ids:
        raise PlatformMessageRecipientError("At least one recipient is required")

    users = (
        (
            await db.execute(
                select(PortalUser).where(
                    PortalUser.org_id == org_id,
                    PortalUser.zitadel_user_id.in_(normalized_user_ids),
                    PortalUser.status != "offboarded",
                )
            )
        )
        .scalars()
        .all()
    )
    users_by_id = {user.zitadel_user_id: user for user in users}
    missing = [user_id for user_id in normalized_user_ids if user_id not in users_by_id]
    if missing:
        raise PlatformMessageRecipientError(f"Unknown active recipients for org {org_id}: {missing}")

    origin_type = "direct"
    if feedback_submission_id is not None:
        submission_org_id = await db.scalar(
            select(FeedbackSubmission.org_id).where(FeedbackSubmission.id == feedback_submission_id)
        )
        if submission_org_id != org_id:
            raise PlatformMessageRecipientError("Feedback submission does not belong to recipient org")
        origin_type = "feedback_submission"
    if feedback_item_id is not None:
        item_exists = await db.scalar(select(FeedbackItem.id).where(FeedbackItem.id == feedback_item_id))
        if item_exists is None:
            raise PlatformMessageRecipientError("Feedback item not found")
        origin_type = "feedback_item"

    now = datetime.now(UTC)
    thread = PlatformMessageThread(
        org_id=org_id,
        subject=subject,
        created_by=created_by,
        origin_type=origin_type,
        feedback_submission_id=feedback_submission_id,
        feedback_item_id=feedback_item_id,
        last_message_at=now,
    )
    db.add(thread)
    await db.flush()

    for user_id in normalized_user_ids:
        user = users_by_id[user_id]
        db.add(
            PlatformMessageParticipant(
                thread_id=thread.id,
                org_id=org_id,
                user_id=user.zitadel_user_id,
                recipient_email=user.email,
                recipient_display_name=user.display_name,
            )
        )
    db.add(
        PlatformMessage(
            thread_id=thread.id,
            org_id=org_id,
            sender_type="platform_admin",
            sender_user_id=created_by,
            sender_display_name=sender_display_name,
            body=body,
            created_at=now,
        )
    )
    await db.flush()
    return thread


async def add_platform_message_reply(
    db: AsyncSession,
    *,
    thread_id: int,
    org_id: int,
    sender_type: str,
    sender_user_id: str,
    body: str,
    sender_display_name: str | None = None,
) -> PlatformMessage:
    thread = await get_platform_message_thread(db, thread_id)
    if thread.org_id != org_id:
        raise PlatformMessageThreadNotFoundError()
    now = datetime.now(UTC)
    message = PlatformMessage(
        thread_id=thread.id,
        org_id=org_id,
        sender_type=sender_type,
        sender_user_id=sender_user_id,
        sender_display_name=sender_display_name,
        body=body,
        created_at=now,
    )
    thread.status = "open"
    thread.last_message_at = now
    db.add(message)
    await db.flush()
    return message


async def mark_platform_message_thread_read(
    db: AsyncSession,
    *,
    thread_id: int,
    org_id: int,
    user_id: str,
) -> datetime:
    participant = (
        await db.execute(
            select(PlatformMessageParticipant).where(
                PlatformMessageParticipant.thread_id == thread_id,
                PlatformMessageParticipant.org_id == org_id,
                PlatformMessageParticipant.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if participant is None:
        raise PlatformMessageThreadNotFoundError()
    read_at = datetime.now(UTC)
    participant.last_read_at = read_at
    await db.flush()
    return read_at


async def mark_platform_thread_admin_read(db: AsyncSession, *, thread_id: int) -> datetime:
    """Mark a thread as read by the platform admin (cross-org context)."""
    thread = await get_platform_message_thread(db, thread_id)
    read_at = datetime.now(UTC)
    thread.admin_read_at = read_at
    await db.flush()
    return read_at


async def user_can_access_thread(db: AsyncSession, *, thread_id: int, org_id: int, user_id: str) -> bool:
    participant = await db.scalar(
        select(PlatformMessageParticipant.thread_id).where(
            PlatformMessageParticipant.thread_id == thread_id,
            PlatformMessageParticipant.org_id == org_id,
            PlatformMessageParticipant.user_id == user_id,
        )
    )
    return participant is not None


async def latest_platform_admin_message_at(db: AsyncSession, *, thread_id: int) -> datetime | None:
    return await db.scalar(
        select(func.max(PlatformMessage.created_at)).where(
            PlatformMessage.thread_id == thread_id,
            PlatformMessage.sender_type.in_(("platform_admin", "system")),
        )
    )
