from sqlalchemy.ext.asyncio import AsyncSession

from app.klai_feedback.models import FeedbackSubmission


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
