"""Gap re-scoring service.

Re-evaluates open knowledge gap queries against the retrieval API after new content
is added (page save or connector sync). Marks gaps as resolved when retrieval now
passes the classification threshold.

# @MX:NOTE: [AUTO] Called fire-and-forget via asyncio.create_task from page-save and
# @MX:NOTE: connector sync-status handlers. Must never raise -- all errors are logged.
# @MX:ANCHOR: [AUTO] rescore_open_gaps is called from internal.py and app_knowledge_bases.py
# @MX:REASON: Two trigger points -- ensure signature changes are reflected in both callers.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.retrieval_gaps import PortalRetrievalGap
from app.services.gap_classification import classify_gap
from app.trace import get_trace_headers

logger = logging.getLogger(__name__)

MAX_QUERIES_PER_TRIGGER = 50
RESCORE_WINDOW_DAYS = 30


async def rescore_open_gaps(
    org_id: int,
    zitadel_org_id: str,
    kb_slug: str | None,
    db: AsyncSession,
) -> int:
    """Re-score open gap queries for an org and mark resolved ones.

    Args:
        org_id: Portal DB org ID.
        zitadel_org_id: Zitadel org ID string (used as Qdrant partition key for retrieval).
        kb_slug: If provided, only re-score gaps with matching nearest_kb_slug or NULL (hard gaps).
                 If None, re-scores all open gaps for the org (connector sync case).
        db: Async database session.

    Returns:
        Number of distinct gap query groups resolved.
    """
    if not settings.knowledge_retrieve_url:
        logger.warning("gap_rescorer: KNOWLEDGE_RETRIEVE_URL not configured -- skipping re-scoring")
        return 0

    # Background task runs on a fresh session (db_factory=get_db). Pin + set
    # tenant context so queries against portal_retrieval_gaps (RLS-scoped)
    # see this org's rows.
    from app.core.database import set_tenant

    await set_tenant(db, org_id)

    cutoff = datetime.now(tz=UTC) - timedelta(days=RESCORE_WINDOW_DAYS)

    # Step 1: fetch distinct open gap queries within window
    stmt = (
        select(
            PortalRetrievalGap.query_text,
            PortalRetrievalGap.gap_type,
        )
        .where(
            PortalRetrievalGap.org_id == org_id,
            PortalRetrievalGap.resolved_at.is_(None),
            PortalRetrievalGap.occurred_at >= cutoff,
        )
        .distinct()
        .order_by(PortalRetrievalGap.occurred_at.desc())
        .limit(MAX_QUERIES_PER_TRIGGER)
    )
    if kb_slug is not None:
        stmt = stmt.where(
            (PortalRetrievalGap.nearest_kb_slug == kb_slug) | PortalRetrievalGap.nearest_kb_slug.is_(None)
        )

    result = await db.execute(stmt)
    gap_queries = result.all()

    if not gap_queries:
        logger.debug("gap_rescorer: no open gaps found for org_id=%s kb_slug=%s", org_id, kb_slug)
        return 0

    resolved_count = 0
    headers = {**get_trace_headers()}
    if settings.internal_secret:
        headers["Authorization"] = f"Bearer {settings.internal_secret}"

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        for row in gap_queries:
            try:
                resp = await client.post(
                    f"{settings.knowledge_retrieve_url}/retrieve",
                    headers=headers,
                    json={
                        "query": row.query_text,
                        "org_id": zitadel_org_id,
                        "user_id": "system",
                        "scope": "org",
                        "top_k": 5,
                    },
                )
                if not resp.is_success:
                    logger.warning(
                        "gap_rescorer: retrieval API returned %s for query=%r -- skipping",
                        resp.status_code,
                        row.query_text[:60],
                    )
                    continue
                chunks = resp.json().get("chunks", [])
            except Exception as exc:
                logger.warning("gap_rescorer: retrieval API error for query=%r: %s", row.query_text[:60], exc)
                continue

            gap_result = classify_gap(chunks)
            if gap_result is None:
                # Gap is resolved -- mark all matching rows for this (org, query_text)
                await db.execute(
                    update(PortalRetrievalGap)
                    .where(
                        PortalRetrievalGap.org_id == org_id,
                        PortalRetrievalGap.query_text == row.query_text,
                        PortalRetrievalGap.resolved_at.is_(None),
                    )
                    .values(resolved_at=datetime.now(tz=UTC))
                )
                resolved_count += 1
                logger.info(
                    "gap_rescorer: resolved gap query=%r org_id=%s",
                    row.query_text[:60],
                    org_id,
                )

    if resolved_count > 0:
        await db.commit()

    logger.info(
        "gap_rescorer: completed org_id=%s kb_slug=%s resolved=%d/%d",
        org_id,
        kb_slug,
        resolved_count,
        len(gap_queries),
    )
    return resolved_count


async def schedule_rescore(
    org_id: int,
    zitadel_org_id: str,
    kb_slug: str | None,
    db_factory,
    delay_seconds: float = 5.0,
) -> None:
    """Fire-and-forget wrapper: delay then run rescore_open_gaps with a fresh DB session.

    Uses asyncio.create_task for non-blocking execution. All exceptions are caught and logged.
    """

    async def _run() -> None:
        await asyncio.sleep(delay_seconds)
        async for db in db_factory():
            try:
                await rescore_open_gaps(org_id, zitadel_org_id, kb_slug, db)
            except Exception:
                logger.exception("gap_rescorer: unhandled error in background task")
            break  # only one session needed

    try:
        asyncio.get_running_loop().create_task(_run())
    except RuntimeError:
        logger.warning("gap_rescorer: no running event loop -- cannot schedule re-scoring")
