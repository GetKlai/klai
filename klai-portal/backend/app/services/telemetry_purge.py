"""SPEC-PRIVACY-QUERY-SHADOW-001 Unit 7 — daily 7-day TTL purge.

Background loop that runs every 24 hours and deletes rows older than
7 days from the privacy-sensitive stores, and the internal chat's stored
cases older than 30 days:

1. ``telemetry.query_shadow``       — every row > 7d (REQ-7 retention)
2. ``portal_retrieval_gaps``         — raw search telemetry > 7d: no redaction
                                        sentinel, no support case and no
                                        verdict behind it
                                        (EXPIRED_RAW_TELEMETRY_GAPS_SQL)
3. portal-side mirror of the Redis
   retrieval-log already has its own 1h TTL, so no DB sweep here
4. ``internal_chat_cases``           — the case and kept passages an internal
                                        conversation carries to its next turn,
                                        30 days (widget_audit.INTERNAL_CASE_DAYS)
5. ``portal_gap_kept_questions``     — a widget gap question kept past the
                                        message retention: 28 days, and only
                                        while the org is at telemetry full
                                        (knowledge_gap_report.KEPT_QUESTION_DAYS)

The job is scheduled via ``asyncio.create_task`` from the FastAPI
lifespan, mirroring ``recording_cleanup_loop`` (SPEC-GDPR-002-R5). It
runs cross-org via ``cross_org_session`` because the privacy contract
is platform-wide, not per-tenant.

Failures are logged and the loop continues — a transient Postgres blip
must not silently disable retention. Cancellation (lifespan shutdown)
exits the loop cleanly via ``asyncio.CancelledError``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import text

from app.core.database import cross_org_session
from app.services.knowledge_gap_report import KEPT_QUESTION_DAYS, conversation_ref
from app.services.widget_audit import prune_internal_cases

logger = structlog.get_logger()

# 24-hour cadence keeps the Postgres footprint negligible (~10k deletes
# per day at production volume) while staying within the 7-day TTL
# contract — at most a 25-hour-old row survives for one cycle, well
# within the privacy fence.
PURGE_INTERVAL_SECONDS = 24 * 60 * 60
RETENTION_DAYS = 7
_RETRIEVAL_GAP_CHUNK_SIZE = 10_000

# The TTL covers raw search telemetry: the query a widget, LibreChat or MCP
# search logged. Two kinds of row are a finding about a need instead, and the
# knowledge inbox is built on them: a support-case finding (its evidence lives
# in portal_support_cases and cascades on its delete) and a verdict, filed by a
# human reviewer or the conversation judge. Purging those while a theme needs
# three conversations in 30 days left the inbox emptying itself every week, so
# they are kept (product decision, 2026-09-24; the privacy documentation and
# DPA are being updated to match). Everything else still expires here.
EXPIRED_RAW_TELEMETRY_GAPS_SQL = """
    SELECT id FROM public.portal_retrieval_gaps
    WHERE query_text NOT LIKE '[REDACTED:%'
    AND support_case_id IS NULL
    AND caller_client_id IS DISTINCT FROM 'human-review'
    AND caller_client_id IS DISTINCT FROM 'quality-judge'
    AND occurred_at < :cutoff
    ORDER BY id
    LIMIT :chunk_size
"""


# A kept gap question (portal_gap_kept_questions) lives KEPT_QUESTION_DAYS, only
# while its org stays at telemetry full (an org without a readable level counts
# as below full) and only while its group is undecided. The removals at the
# source handle each case at once; this is the backstop that clears whatever
# they missed within a day.
_EXPIRED_KEPT_GAP_QUESTIONS_SQL = """
    DELETE FROM portal_gap_kept_questions k
    WHERE k.turn_at < :cutoff
       OR NOT EXISTS (
           SELECT 1 FROM portal_orgs po WHERE po.id = k.org_id AND po.telemetry_level = 'full'
       )
       OR EXISTS (
           SELECT 1 FROM portal_gap_proposals p
            WHERE p.org_id = k.org_id AND p.cluster_key = k.cluster_key AND p.decision <> 'none'
       )
"""

# Conversations marked test or preview that can still have kept rows: a kept
# turn is at most KEPT_QUESTION_DAYS old, so its conversation's last message is too.
_MARKED_CONVERSATIONS_SQL = """
    SELECT wc.org_id, wc.id
      FROM widget_conversations wc
     WHERE (wc.is_test OR wc.is_preview)
       AND wc.last_message_at >= :cutoff
       AND EXISTS (SELECT 1 FROM portal_gap_kept_questions k WHERE k.org_id = wc.org_id)
"""


async def purge_kept_gap_questions() -> int:
    """Delete kept gap questions past their lifetime, of an org below full, of a decided group or of a
    conversation marked test or preview, in a transaction of its own."""
    cutoff = datetime.now(UTC) - timedelta(days=KEPT_QUESTION_DAYS)
    async with cross_org_session() as db:
        result = await db.execute(text(_EXPIRED_KEPT_GAP_QUESTIONS_SQL), {"cutoff": cutoff})
        purged = result.rowcount or 0  # type: ignore[attr-defined]
        # The reference is a keyed hash computed in the app, so the marked
        # conversations are hashed here, the same way the sweep did.
        refs: dict[int, list[str | None]] = {}
        for org_id, conversation_id in (await db.execute(text(_MARKED_CONVERSATIONS_SQL), {"cutoff": cutoff})).all():
            refs.setdefault(org_id, []).append(conversation_ref(org_id, conversation_id))
        for org_id, org_refs in refs.items():
            result = await db.execute(
                text(
                    "DELETE FROM portal_gap_kept_questions "
                    "WHERE org_id = :org_id AND conversation_ref = ANY(CAST(:refs AS varchar[]))"
                ),
                {"org_id": org_id, "refs": org_refs},
            )
            purged += result.rowcount or 0  # type: ignore[attr-defined]
        await db.commit()
    return purged


async def _purge_once() -> dict[str, int]:
    """Run the DELETEs in one pass and return per-store counts."""
    cutoff = datetime.now(UTC) - timedelta(days=RETENTION_DAYS)
    counts: dict[str, int] = {"query_shadow": 0, "retrieval_gaps": 0, "internal_chat_cases": 0, "kept_gap_questions": 0}

    async with cross_org_session() as db:
        # 1. telemetry.query_shadow — fully ephemeral, every row > 7d.
        try:
            result = await db.execute(
                text("DELETE FROM telemetry.query_shadow WHERE created_at < :cutoff"),
                {"cutoff": cutoff},
            )
            # SQLAlchemy's stub does not expose ``rowcount`` on ``Result``,
            # but every DELETE/UPDATE result carries it at runtime — see
            # https://docs.sqlalchemy.org/en/20/core/connections.html#sqlalchemy.engine.CursorResult.rowcount
            counts["query_shadow"] = result.rowcount or 0  # type: ignore[attr-defined]
        except Exception:
            logger.warning("telemetry_purge_query_shadow_failed", exc_info=True)

        # 2. portal_retrieval_gaps — raw retrieval telemetry only. Rows with
        #    sentinel '[REDACTED:%' carry no privacy debt and stay until the
        #    operator resolves the underlying gap. The check uses NOT LIKE so
        #    both '[REDACTED:legacy]' (one-time cleanup) and
        #    '[REDACTED:shadow]' (ongoing shadow-mode inserts) survive.
        try:
            while True:
                candidate_result = await db.execute(
                    text(EXPIRED_RAW_TELEMETRY_GAPS_SQL),
                    {"cutoff": cutoff, "chunk_size": _RETRIEVAL_GAP_CHUNK_SIZE},
                )
                gap_ids = list(candidate_result.scalars().all())
                if not gap_ids:
                    break

                result = await db.execute(
                    text(
                        """
                        DELETE FROM public.portal_retrieval_gaps
                        WHERE id = ANY(CAST(:gap_ids AS integer[]))
                        """
                    ),
                    {"gap_ids": gap_ids},
                )
                rows_deleted = result.rowcount or 0  # type: ignore[attr-defined]
                counts["retrieval_gaps"] += rows_deleted
                if rows_deleted == 0 or len(gap_ids) < _RETRIEVAL_GAP_CHUNK_SIZE:
                    break
        except Exception:
            logger.warning("telemetry_purge_retrieval_gaps_failed", exc_info=True)

        await db.commit()

    # 4. internal_chat_cases, in its own transaction, so a failure here cannot
    #    roll back the deletes above.
    try:
        counts["internal_chat_cases"] = await prune_internal_cases()
    except Exception:
        logger.warning("telemetry_purge_internal_chat_cases_failed", exc_info=True)

    # 5. portal_gap_kept_questions, also in its own transaction.
    try:
        counts["kept_gap_questions"] = await purge_kept_gap_questions()
    except Exception:
        logger.warning("telemetry_purge_kept_gap_questions_failed", exc_info=True)

    logger.info(
        "telemetry_purge_complete",
        cutoff=cutoff.isoformat(),
        purged_query_shadow=counts["query_shadow"],
        purged_retrieval_gaps=counts["retrieval_gaps"],
        purged_internal_chat_cases=counts["internal_chat_cases"],
        purged_kept_gap_questions=counts["kept_gap_questions"],
    )
    return counts


async def telemetry_purge_loop() -> None:
    """FastAPI-lifespan-attached daily purge loop.

    Sleeps 60s on startup so the app can finish wiring before the first
    DB hit. Then runs ``_purge_once`` every PURGE_INTERVAL_SECONDS until
    cancelled.
    """
    await asyncio.sleep(60)
    while True:
        try:
            await _purge_once()
        except asyncio.CancelledError:
            break
        except Exception:
            # Last-resort guard: anything that escapes _purge_once's
            # per-table try/except. Loop continues so the next cycle
            # has a fresh attempt.
            logger.exception("telemetry_purge_unexpected_error")
        await asyncio.sleep(PURGE_INTERVAL_SECONDS)
