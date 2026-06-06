from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.klai_feedback.models import (
    FeedbackItem,
    FeedbackItemLink,
    FeedbackNotification,
    FeedbackSubmission,
    FeedbackTriageSuggestion,
)
from app.models.portal import PortalUser


class FeedbackSubmissionNotFoundError(Exception):
    pass


class FeedbackItemNotFoundError(Exception):
    pass


async def create_feedback_submission(
    db: AsyncSession,
    *,
    source: str,
    raw_text: str,
    org_id: int,
    user_id: str | None,
    page_url: str | None,
    route_id: str | None,
    locale: str | None,
    viewport: str | None,
    user_agent: str | None,
    referrer: str | None,
    metadata_json: dict | None = None,
) -> FeedbackSubmission:
    """Persist a raw first-party feedback submission synchronously.

    The caller's request session is already tenant-scoped by get_caller/get_db.
    A successful form response should therefore mean the durable feedback row
    exists; product_events remains a secondary audit/analytics signal.
    """
    submission = FeedbackSubmission(
        source=source,
        raw_text=raw_text,
        org_id=org_id,
        user_id=user_id,
        page_url=page_url,
        route_id=route_id,
        locale=locale,
        viewport=viewport,
        user_agent=user_agent,
        referrer=referrer,
        metadata_json=metadata_json or {},
    )
    db.add(submission)
    await db.commit()
    return submission


async def get_feedback_submission(db: AsyncSession, submission_id: int) -> FeedbackSubmission:
    submission = (
        await db.execute(select(FeedbackSubmission).where(FeedbackSubmission.id == submission_id))
    ).scalar_one_or_none()
    if submission is None:
        raise FeedbackSubmissionNotFoundError()
    return submission


async def get_feedback_item(db: AsyncSession, item_id: int) -> FeedbackItem:
    item = (await db.execute(select(FeedbackItem).where(FeedbackItem.id == item_id))).scalar_one_or_none()
    if item is None:
        raise FeedbackItemNotFoundError()
    return item


async def search_feedback_items(
    db: AsyncSession,
    *,
    search: str | None,
    status: str | None,
    kind: str | None,
    limit: int,
) -> list[FeedbackItem]:
    query = select(FeedbackItem).order_by(FeedbackItem.updated_at.desc()).limit(limit)
    closed_statuses = ("resolved", "dismissed")
    if status == "active":
        query = query.where(FeedbackItem.status.not_in(closed_statuses))
    elif status == "triage":
        query = query.where(FeedbackItem.status != "dismissed")
    elif status == "closed":
        query = query.where(FeedbackItem.status.in_(closed_statuses))
    elif status and status != "all":
        query = query.where(FeedbackItem.status == status)
    if kind and kind != "all":
        query = query.where(FeedbackItem.kind == kind)
    terms = _feedback_search_terms(search)
    if terms:
        query = query.where(and_(*[_feedback_item_search_clause(term) for term in terms]))
    return list((await db.execute(query)).scalars().all())


def _feedback_search_terms(search: str | None) -> list[str]:
    if not search:
        return []
    normalized = search.replace("_", " ").replace("-", " ")
    words = [word.strip(".,:;!?()[]{}\"'").lower() for word in normalized.split()]
    stopwords = {
        "voor",
        "door",
        "naar",
        "niet",
        "geen",
        "deze",
        "daar",
        "hier",
        "kunnen",
        "willen",
        "moeten",
        "zodat",
        "voordat",
        "with",
        "from",
        "that",
        "this",
    }
    return [word for word in words if len(word) >= 4 and word not in stopwords][:6]


def _feedback_item_search_clause(term: str):
    q = f"%{term}%"
    linked_submission_match = (
        select(FeedbackItemLink.item_id)
        .join(FeedbackSubmission, FeedbackSubmission.id == FeedbackItemLink.submission_id)
        .where(FeedbackSubmission.raw_text.ilike(q))
    )
    return or_(
        FeedbackItem.title.ilike(q),
        FeedbackItem.summary.ilike(q),
        FeedbackItem.area.ilike(q),
        FeedbackItem.id.in_(linked_submission_match),
    )


async def update_feedback_submission(
    db: AsyncSession,
    submission_id: int,
    values: dict[str, object],
) -> FeedbackSubmission:
    submission = await get_feedback_submission(db, submission_id)
    for key, value in values.items():
        setattr(submission, key, value)
    await db.commit()
    return submission


async def delete_feedback_submission(db: AsyncSession, submission_id: int) -> None:
    submission = await get_feedback_submission(db, submission_id)
    linked_item_ids = list(
        (await db.execute(select(FeedbackItemLink.item_id).where(FeedbackItemLink.submission_id == submission_id)))
        .scalars()
        .all()
    )
    await db.execute(
        update(FeedbackNotification)
        .where(FeedbackNotification.submission_id == submission_id)
        .values(submission_id=None)
    )
    await db.execute(delete(FeedbackTriageSuggestion).where(FeedbackTriageSuggestion.submission_id == submission_id))
    await db.execute(delete(FeedbackItemLink).where(FeedbackItemLink.submission_id == submission_id))
    await db.delete(submission)
    await db.flush()
    for item_id in linked_item_ids:
        item_exists = await db.scalar(select(FeedbackItem.id).where(FeedbackItem.id == item_id))
        if item_exists is not None:
            await refresh_feedback_item_counts(db, item_id)
    await db.commit()


async def update_feedback_item(
    db: AsyncSession,
    item_id: int,
    values: dict[str, object],
) -> FeedbackItem:
    item = await get_feedback_item(db, item_id)
    next_status = values.get("status")
    if next_status == "resolved" and item.status != "resolved" and item.shipped_at is None:
        item.shipped_at = datetime.now(UTC)
    elif next_status is not None and next_status != "resolved":
        item.shipped_at = None
    for key, value in values.items():
        setattr(item, key, value)
    await db.commit()
    return item


async def resolve_feedback_item(
    db: AsyncSession,
    item_id: int,
    *,
    resolution_summary: str,
    resolved_by: str,
    channels: list[str],
    subject: str | None = None,
) -> tuple[Any, list[Any]]:
    item = await get_feedback_item(db, item_id)
    now = datetime.now(UTC)
    item.resolution_summary = resolution_summary
    item.resolved_at = now
    item.resolved_by = resolved_by
    item.status = "resolved"
    if item.kind == "feature":
        item.shipped_at = now

    requested_channels = [channel for channel in channels if channel in {"in_app", "email"}]
    notifications: list[FeedbackNotification] = []
    if requested_channels:
        rows = (
            await db.execute(
                select(FeedbackSubmission, PortalUser.email)
                .select_from(FeedbackItemLink)
                .join(FeedbackSubmission, FeedbackSubmission.id == FeedbackItemLink.submission_id)
                .outerjoin(
                    PortalUser,
                    (PortalUser.zitadel_user_id == FeedbackSubmission.user_id)
                    & (PortalUser.org_id == FeedbackSubmission.org_id),
                )
                .where(FeedbackItemLink.item_id == item_id)
                .order_by(FeedbackItemLink.created_at.asc())
            )
        ).all()

        seen: set[tuple[int | None, str | None, str]] = set()
        for submission, email in rows:
            if submission.org_id is None or submission.user_id is None:
                continue
            for channel in requested_channels:
                dedupe_key = (submission.org_id, submission.user_id, channel)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                status = "sent" if channel == "in_app" else "queued"
                if channel == "email" and not email:
                    status = "skipped"
                notification = FeedbackNotification(
                    item_id=item.id,
                    submission_id=submission.id,
                    org_id=submission.org_id,
                    user_id=submission.user_id,
                    recipient_email=email if channel == "email" else None,
                    channel=channel,
                    status=status,
                    subject=subject or item.title,
                    body=resolution_summary,
                    generated_by="staff",
                    sent_at=now if status == "sent" else None,
                )
                db.add(notification)
                notifications.append(notification)

    if not requested_channels:
        item.notification_state = "not_needed"
    elif any(notification.status == "failed" for notification in notifications):
        item.notification_state = "failed"
    elif any(notification.status == "queued" for notification in notifications):
        item.notification_state = "queued"
    elif notifications:
        item.notification_state = "sent"
    else:
        item.notification_state = "not_needed"

    await db.flush()
    linked_submissions = (
        await db.execute(
            select(FeedbackSubmission)
            .select_from(FeedbackItemLink)
            .join(FeedbackSubmission, FeedbackSubmission.id == FeedbackItemLink.submission_id)
            .where(FeedbackItemLink.item_id == item_id)
        )
    ).scalars()
    for submission in linked_submissions:
        submission.status = "resolved"

    await db.flush()
    item_snapshot = SimpleNamespace(
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
        created_at=item.created_at,
        updated_at=now,
    )
    notification_snapshots = [
        SimpleNamespace(
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
            created_at=notification.created_at or now,
        )
        for notification in notifications
    ]
    await db.commit()
    return item_snapshot, notification_snapshots


async def delete_feedback_item(db: AsyncSession, item_id: int) -> None:
    item = await get_feedback_item(db, item_id)
    linked_submission_ids = list(
        (await db.execute(select(FeedbackItemLink.submission_id).where(FeedbackItemLink.item_id == item_id)))
        .scalars()
        .all()
    )
    if linked_submission_ids:
        submissions = (
            await db.execute(select(FeedbackSubmission).where(FeedbackSubmission.id.in_(linked_submission_ids)))
        ).scalars()
        for submission in submissions:
            submission.status = "new"
    await db.execute(delete(FeedbackNotification).where(FeedbackNotification.item_id == item_id))
    await db.execute(delete(FeedbackItemLink).where(FeedbackItemLink.item_id == item_id))
    await db.delete(item)
    await db.commit()


async def dismiss_feedback_submission(db: AsyncSession, submission_id: int) -> FeedbackSubmission:
    submission = await get_feedback_submission(db, submission_id)
    submission.status = "dismissed"
    await db.commit()
    return submission


async def mark_feedback_submission_support(db: AsyncSession, submission_id: int) -> FeedbackSubmission:
    submission = await get_feedback_submission(db, submission_id)
    submission.status = "support"
    await db.commit()
    return submission


async def create_feedback_item_from_submission(
    db: AsyncSession,
    *,
    submission_id: int,
    kind: str,
    title: str,
    summary: str | None,
    area: str | None,
    link_type: str,
) -> tuple[FeedbackSubmission, FeedbackItem]:
    submission = await get_feedback_submission(db, submission_id)
    item = FeedbackItem(
        kind=kind,
        title=title,
        summary=summary or submission.raw_text,
        area=area,
        status="open",
    )
    db.add(item)
    await db.flush()
    db.add(
        FeedbackItemLink(
            item_id=item.id,
            submission_id=submission.id,
            link_type=link_type,
            confidence=100,
            created_by="staff",
        )
    )
    submission.status = "open"
    await db.flush()
    await refresh_feedback_item_counts(db, item.id)
    await db.commit()
    return submission, item


async def link_feedback_submission_to_item(
    db: AsyncSession,
    *,
    submission_id: int,
    item_id: int,
    link_type: str,
    reopen_item: bool = False,
) -> tuple[FeedbackSubmission, FeedbackItem]:
    submission = await get_feedback_submission(db, submission_id)
    item = await get_feedback_item(db, item_id)

    existing = (
        await db.execute(
            select(FeedbackItemLink).where(
                FeedbackItemLink.item_id == item_id,
                FeedbackItemLink.submission_id == submission_id,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            FeedbackItemLink(
                item_id=item_id,
                submission_id=submission_id,
                link_type=link_type,
                confidence=100,
                created_by="staff",
            )
        )
    else:
        existing.link_type = link_type
        existing.created_by = "staff"
        existing.confidence = 100

    submission.status = "open"
    if reopen_item and item.status in {"resolved", "dismissed"}:
        item.status = "open"
        item.shipped_at = None
        item.resolved_at = None
        item.resolved_by = None
        item.resolution_summary = None
        item.notification_state = "not_needed"
    await db.flush()
    await refresh_feedback_item_counts(db, item_id)
    await db.commit()
    return submission, item


async def refresh_feedback_item_counts(db: AsyncSession, item_id: int) -> None:
    row = (
        await db.execute(
            select(
                func.count(func.distinct(FeedbackSubmission.org_id)),
                func.count(func.distinct(FeedbackSubmission.user_id)),
            )
            .select_from(FeedbackItemLink)
            .join(FeedbackSubmission, FeedbackSubmission.id == FeedbackItemLink.submission_id)
            .where(FeedbackItemLink.item_id == item_id)
        )
    ).one()
    org_count = int(row[0] or 0)
    user_count = int(row[1] or 0)
    item = (await db.execute(select(FeedbackItem).where(FeedbackItem.id == item_id))).scalar_one()
    item.org_count = org_count
    item.user_count = user_count
    item.priority_score = org_count * 10 + user_count
