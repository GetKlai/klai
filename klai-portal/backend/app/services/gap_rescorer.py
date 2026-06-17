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
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.retrieval_gaps import PortalRetrievalGap
from app.services.gap_classification import classify_gap
from app.trace import get_trace_headers

logger = logging.getLogger(__name__)

MAX_QUERIES_PER_TRIGGER = 50
RESCORE_WINDOW_DAYS = 30


def _open_gap_queries_stmt(org_id: int, kb_slug: str | None, cutoff: datetime):
    # PostgreSQL rejects SELECT DISTINCT query_text, gap_type ORDER BY occurred_at
    # because occurred_at is not in the select list. Grouping preserves the
    # one-row-per-gap contract and keeps the ordering deterministic.
    stmt = (
        select(
            PortalRetrievalGap.query_text.label("query_text"),
            PortalRetrievalGap.gap_type.label("gap_type"),
            func.max(PortalRetrievalGap.occurred_at).label("last_occurred"),
        )
        .where(
            PortalRetrievalGap.org_id == org_id,
            PortalRetrievalGap.resolved_at.is_(None),
            PortalRetrievalGap.occurred_at >= cutoff,
        )
        .group_by(PortalRetrievalGap.query_text, PortalRetrievalGap.gap_type)
        .order_by(func.max(PortalRetrievalGap.occurred_at).desc())
        .limit(MAX_QUERIES_PER_TRIGGER)
    )
    if kb_slug is not None:
        stmt = stmt.where(
            (PortalRetrievalGap.nearest_kb_slug == kb_slug) | PortalRetrievalGap.nearest_kb_slug.is_(None)
        )
    return stmt


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

    # Step 1: fetch distinct open gap queries within window, most-recent first.
    stmt = _open_gap_queries_stmt(org_id, kb_slug, cutoff)

    result = await db.execute(stmt)
    gap_queries = result.all()

    if not gap_queries:
        logger.debug("gap_rescorer: no open gaps found for org_id=%s kb_slug=%s", org_id, kb_slug)
        return 0

    resolved_count = 0
    # SPEC-SEC-IDENTITY-ASSERT-001 REQ-4.2: retrieval-api requires
    # X-Caller-Service for any /retrieve with an end-user identity in the
    # body. We send `system` here in user_id, but the header is still
    # validated. Without it: 400 missing_caller_service → silent rescore
    # noop. See pitfalls → retrieve-caller-service-header-mismatch.
    #
    # SPEC-SEC-010 REQ-1: retrieval-api's AuthMiddleware treats
    # `Authorization: Bearer <token>` strictly as a JWT — non-JWT strings
    # (like our shared `internal_secret`) fail decode and return 401
    # `invalid_jwt_signature`. There is NO fallback to X-Internal-Secret
    # when the Bearer arm is taken. So this caller MUST send
    # X-Internal-Secret like every other portal-api → retrieval-api
    # caller (partner_chat, klai_connector_client, litellm hook).
    # Use the dedicated retrieval_api_internal_secret rotation
    # boundary (REQ-6.1) — falls back to internal_secret for
    # backwards-compat with envs that haven't split the secret yet.
    # Audit reference: .moai/audits/retrieval-coupling-2026-05-06/
    # findings/F1-gap-rescorer-bearer-auth.md
    retrieval_secret = settings.retrieval_api_internal_secret or settings.internal_secret
    headers = {
        "X-Internal-Secret": retrieval_secret,
        "X-Caller-Service": "portal-api",
        **get_trace_headers(),
    }

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
                logger.warning(
                    "gap_rescorer: retrieval API error for query=%r: %s", row.query_text[:60], exc, exc_info=True
                )
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
