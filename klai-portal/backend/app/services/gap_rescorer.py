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
from fastapi import HTTPException
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import cross_org_session, get_db
from app.models.portal import PortalOrg
from app.models.retrieval_gaps import PortalRetrievalGap
from app.models.support_cases import PortalSupportCase
from app.schemas_support_cases import SupportCasePayload
from app.services.gap_classification import classify_gap
from app.services.support_cases import (
    GAP_FEATURE,
    OversizedCaseError,
    StaleCaseError,
    SupportTelemetryError,
    upsert_support_case,
)
from app.trace import get_trace_headers

logger = logging.getLogger(__name__)

MAX_QUERIES_PER_TRIGGER = 50
RESCORE_WINDOW_DAYS = 30
# A KB ingestion may touch many support cases; reanalyse a bounded batch per
# trigger (oldest-analysed first) so one large sync cannot fan out unboundedly.
MAX_SUPPORT_CASES_PER_TRIGGER = 50


def _open_gap_queries_stmt(org_id: int, kb_slug: str | None, cutoff: datetime):
    # PostgreSQL rejects SELECT DISTINCT query_text, gap_type ORDER BY occurred_at
    # because occurred_at is not in the select list. Grouping preserves the
    # one-row-per-gap contract and keeps the ordering deterministic.
    stmt = (
        select(
            PortalRetrievalGap.query_text.label("query_text"),
            PortalRetrievalGap.gap_type.label("gap_type"),
            # Gaps group per question language (SPEC-KNOWLEDGE-ACTIVITY-001
            # §4.9): a re-ask that succeeds in English must not close the
            # Dutch group for the same words.
            PortalRetrievalGap.language.label("language"),
            func.max(PortalRetrievalGap.occurred_at).label("last_occurred"),
            PortalOrg.telemetry_level.label("telemetry_level"),
        )
        .join(PortalOrg, PortalOrg.id == PortalRetrievalGap.org_id)
        .where(
            PortalRetrievalGap.org_id == org_id,
            PortalRetrievalGap.resolved_at.is_(None),
            PortalRetrievalGap.occurred_at >= cutoff,
            # SPEC-RAG-SUPPORT-GAP: case-backed content findings are never
            # closed by retrieval re-scoring — a better retrieval score does
            # not mean the answer was written. Exclude them from selection so
            # they are not even re-queried.
            PortalRetrievalGap.support_case_id.is_(None),
        )
        .group_by(
            PortalRetrievalGap.query_text,
            PortalRetrievalGap.gap_type,
            PortalRetrievalGap.language,
            PortalOrg.telemetry_level,
        )
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
    # X-Caller-Service for every internal-secret /retrieve call, whether or
    # not the body carries an end-user identity. Without it: 400
    # missing_caller_service → silent rescore noop. See pitfalls →
    # retrieve-caller-service-header-mismatch.
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
    # Audit reference: retrieval-coupling-2026-05-06 finding F1, gap-rescorer
    # bearer auth (historical — audit removed in repo cleanup 2026-08-18).
    retrieval_secret = settings.retrieval_api_internal_secret or settings.internal_secret
    headers = {
        "X-Internal-Secret": retrieval_secret,
        "X-Caller-Service": "portal-api",
        **get_trace_headers(),
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        for row in gap_queries:
            try:
                # No user_id: a rescore has no end user. retrieval-api verifies
                # any user_id against portal_users, so the old "system"
                # placeholder got a 403 on every call. "background" keeps the
                # rescore out of the tenant's knowledge.queried count.
                resp = await client.post(
                    f"{settings.knowledge_retrieve_url}/retrieve",
                    headers=headers,
                    json={
                        "query": row.query_text,
                        "org_id": zitadel_org_id,
                        "scope": "org",
                        "top_k": 5,
                        "purpose": "background",
                    },
                )
                if not resp.is_success:
                    logger.warning(
                        "gap_rescorer: retrieval API returned %s for query=%r -- skipping",
                        resp.status_code,
                        row.query_text[:60] if row.telemetry_level == "full" else "<redacted>",
                    )
                    continue
                chunks = resp.json().get("chunks", [])
            except Exception as exc:
                logger.warning(
                    "gap_rescorer: retrieval API error for query=%r: %s",
                    row.query_text[:60] if row.telemetry_level == "full" else "<redacted>",
                    exc,
                    exc_info=True,
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
                        PortalRetrievalGap.language.is_not_distinct_from(row.language),
                        PortalRetrievalGap.resolved_at.is_(None),
                        # Belt-and-braces with the selection filter: a
                        # case-backed content finding must never be closed by
                        # the rescorer even if a legacy row shares its text.
                        PortalRetrievalGap.support_case_id.is_(None),
                    )
                    .values(resolved_at=datetime.now(tz=UTC), resolved_by="rescorer")
                )
                resolved_count += 1
                logger.info(
                    "gap_rescorer: resolved gap query=%r org_id=%s",
                    row.query_text[:60] if row.telemetry_level == "full" else "<redacted>",
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


async def reanalyse_scoped_support_cases(
    org_id: int,
    zitadel_org_id: str,
    kb_slug: str | None,
    db: AsyncSession,
) -> tuple[int, int]:
    """Force-reanalyse the org's analysed support cases after new content lands.

    A retrieval score never closes a support finding — only a fresh analysis
    against the new content decides whether the answer now exists. So when
    ingestion completes we re-run the shared analysis (``force_reanalysis=True``)
    for the affected cases, scoped to the changed KB when one is given (a page
    save) or across the org when none is (a connector sync). The store keeps a
    good prior analysis if the run fails and preserves manual/review closes for
    findings it still returns, so a transient analyzer outage or an already-closed
    finding is never redelivered as a new open gap. Bounded per trigger; failures
    are logged and counted, never swallowed as success. Returns ``(ok, failed)``.

    Gated on the same full-telemetry + ``knowledge_gaps`` policy as every other
    support path; a tenant outside it reanalyses nothing.
    """
    from app.core.database import set_tenant

    await set_tenant(db, org_id)
    policy = (
        await db.execute(
            select(PortalOrg.telemetry_level, PortalOrg.platform_unlocked_features).where(PortalOrg.id == org_id)
        )
    ).one_or_none()
    if (
        policy is None
        or policy.telemetry_level != "full"
        or GAP_FEATURE not in (policy.platform_unlocked_features or [])
    ):
        return 0, 0

    stmt = select(PortalSupportCase).where(PortalSupportCase.org_id == org_id, PortalSupportCase.status == "analyzed")
    if kb_slug is not None:
        stmt = stmt.where(PortalSupportCase.kb_slug == kb_slug)
    stmt = stmt.order_by(PortalSupportCase.updated_at.asc()).limit(MAX_SUPPORT_CASES_PER_TRIGGER)
    cases = (await db.execute(stmt)).scalars().all()

    ok = 0
    failed = 0
    for case in cases:
        payload = SupportCasePayload.model_validate(case.payload)
        payload.bind_kb(case.kb_slug)
        try:
            result = await upsert_support_case(
                db,
                org_id=org_id,
                zitadel_org_id=zitadel_org_id,
                telemetry_level=policy.telemetry_level,
                connector_id=case.connector_id,
                created_by=case.created_by,
                kb_slug=case.kb_slug,
                payload=payload,
                force_reanalysis=True,
                expected_content_hash=case.content_hash,
            )
        except (SupportTelemetryError, StaleCaseError, OversizedCaseError, HTTPException) as exc:
            failed += 1
            logger.warning("gap_rescorer: support reanalysis error case_id=%s: %s", case.id, type(exc).__name__)
            continue
        if result.reanalysis_failed or result.status == "failed":
            failed += 1
        else:
            ok += 1

    logger.info(
        "gap_rescorer: support reanalysis org_id=%s kb_slug=%s candidates=%d ok=%d failed=%d",
        org_id,
        kb_slug,
        len(cases),
        ok,
        failed,
    )
    return ok, failed


async def schedule_rescore(
    org_id: int,
    zitadel_org_id: str,
    kb_slug: str | None,
    db_factory,
    delay_seconds: float = 5.0,
    reanalyse_support: bool = True,
) -> None:
    """Fire-and-forget wrapper: delay then rescore telemetry gaps and, unless
    ``reanalyse_support`` is off, reanalyse support cases, each on its own fresh
    DB session. The connector-sync caller turns it off and goes through
    ``request_support_reanalysis`` instead.

    Uses asyncio.create_task for non-blocking execution. All exceptions are caught
    and logged so one failing pass never aborts the other.
    """

    async def _run() -> None:
        await asyncio.sleep(delay_seconds)
        async for db in db_factory():
            try:
                await rescore_open_gaps(org_id, zitadel_org_id, kb_slug, db)
            except Exception:
                logger.exception("gap_rescorer: unhandled error in background task")
            break  # only one session needed
        if not reanalyse_support:
            return
        async for db in db_factory():
            try:
                await reanalyse_scoped_support_cases(org_id, zitadel_org_id, kb_slug, db)
            except Exception:
                logger.exception("gap_rescorer: unhandled error reanalysing support cases")
            break  # fresh tenant session for the support-case pass

    try:
        asyncio.get_running_loop().create_task(_run())
    except RuntimeError:
        logger.warning("gap_rescorer: no running event loop -- cannot schedule re-scoring")


# A connector sync that changed knowledge force-reanalyses up to
# MAX_SUPPORT_CASES_PER_TRIGGER support cases at ~9 klai-medium calls each. An
# org's connectors finish one after another in the same nightly window, so each
# qualifying sync restamps portal_orgs.support_reanalysis_requested_at, and the
# org is reanalysed once its request has been quiet for 15 minutes instead of
# once per sync. The request lives in Postgres so a portal-api restart cannot
# drop it; one sequential loop serves all orgs, so two runs never overlap.
SUPPORT_REANALYSIS_DEBOUNCE_SECONDS = 15 * 60
SUPPORT_REANALYSIS_POLL_SECONDS = 60


async def request_support_reanalysis(db: AsyncSession, org_id: int) -> None:
    """Record (or restamp, restarting the debounce) the org's pending reanalysis."""
    await db.execute(
        text("UPDATE portal_orgs SET support_reanalysis_requested_at = now() WHERE id = :org_id"),
        {"org_id": org_id},
    )
    await db.commit()


async def run_due_support_reanalyses(db_factory=get_db) -> int:
    """Reanalyse every org whose request is older than the debounce; returns how many ran.

    A request is cleared only if it still holds the timestamp picked up here,
    so a sync that lands during the run keeps its request for the next pass.
    It is cleared after a failed run too: retrying a failure every poll would
    spend on the same cases again, and the next sync that changes knowledge
    requests a fresh run.
    """
    async with cross_org_session() as db:
        due = (
            await db.execute(
                text(
                    "SELECT id, zitadel_org_id, support_reanalysis_requested_at FROM portal_orgs "
                    "WHERE support_reanalysis_requested_at <= now() - make_interval(secs => :debounce_seconds)"
                ),
                {"debounce_seconds": SUPPORT_REANALYSIS_DEBOUNCE_SECONDS},
            )
        ).all()
    for org in due:
        try:
            async for db in db_factory():
                await reanalyse_scoped_support_cases(org.id, org.zitadel_org_id, None, db)
                break
        except Exception:
            logger.exception("gap_rescorer: unhandled error reanalysing support cases org_id=%s", org.id)
        finally:
            async with cross_org_session() as db:
                await db.execute(
                    text(
                        "UPDATE portal_orgs SET support_reanalysis_requested_at = NULL "
                        "WHERE id = :org_id AND support_reanalysis_requested_at = :requested_at"
                    ),
                    {"org_id": org.id, "requested_at": org.support_reanalysis_requested_at},
                )
                await db.commit()
    return len(due)


async def support_reanalysis_loop() -> None:
    """FastAPI-lifespan loop serving connector-sync reanalysis requests."""
    await asyncio.sleep(60)
    while True:
        try:
            await run_due_support_reanalyses()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("gap_rescorer: support reanalysis loop error")
        await asyncio.sleep(SUPPORT_REANALYSIS_POLL_SECONDS)
