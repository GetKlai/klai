from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.klai_feedback.models import FeedbackItem, FeedbackItemLink, FeedbackSubmission


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
) -> None:
    """Persist a raw first-party feedback submission synchronously.

    The caller's request session is already tenant-scoped by get_caller/get_db.
    A successful form response should therefore mean the durable feedback row
    exists; product_events remains a secondary audit/analytics signal.
    """
    db.add(
        FeedbackSubmission(
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
    )
    await db.commit()


async def get_feedback_submission(db: AsyncSession, submission_id: int) -> FeedbackSubmission:
    submission = (
        await db.execute(select(FeedbackSubmission).where(FeedbackSubmission.id == submission_id))
    ).scalar_one_or_none()
    if submission is None:
        raise FeedbackSubmissionNotFoundError()
    return submission


async def search_feedback_items(
    db: AsyncSession,
    *,
    search: str | None,
    limit: int,
) -> list[FeedbackItem]:
    query = select(FeedbackItem).order_by(FeedbackItem.updated_at.desc()).limit(limit)
    if search:
        q = f"%{search}%"
        query = query.where(
            or_(
                FeedbackItem.title.ilike(q),
                FeedbackItem.summary.ilike(q),
                FeedbackItem.area.ilike(q),
            )
        )
    return list((await db.execute(query)).scalars().all())


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
        status="inbox",
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
    submission.status = "linked"
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
) -> tuple[FeedbackSubmission, FeedbackItem]:
    submission = await get_feedback_submission(db, submission_id)
    item = (await db.execute(select(FeedbackItem).where(FeedbackItem.id == item_id))).scalar_one_or_none()
    if item is None:
        raise FeedbackItemNotFoundError()

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

    submission.status = "linked"
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
