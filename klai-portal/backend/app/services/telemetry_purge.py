"""SPEC-PRIVACY-QUERY-SHADOW-001 Unit 7 — daily 7-day TTL purge.

Background loop that runs every 24 hours and deletes rows older than
7 days from the three privacy-sensitive stores:

1. ``telemetry.query_shadow``       — every row > 7d (REQ-7 retention)
2. ``portal_retrieval_gaps``         — every row whose query_text is NOT
                                        a redaction sentinel AND > 7d
                                        (legacy or full-mode raw text)
3. portal-side mirror of the Redis
   retrieval-log already has its own 1h TTL, so no DB sweep here

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

logger = structlog.get_logger()

# 24-hour cadence keeps the Postgres footprint negligible (~10k deletes
# per day at production volume) while staying within the 7-day TTL
# contract — at most a 25-hour-old row survives for one cycle, well
# within the privacy fence.
PURGE_INTERVAL_SECONDS = 24 * 60 * 60
RETENTION_DAYS = 7


async def _purge_once() -> dict[str, int]:
    """Run the three DELETEs in one pass and return per-store counts."""
    cutoff = datetime.now(UTC) - timedelta(days=RETENTION_DAYS)
    counts: dict[str, int] = {"query_shadow": 0, "retrieval_gaps": 0}

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

        # 2. portal_retrieval_gaps — only rows whose query_text is the
        #    raw or legacy text. Rows with sentinel '[REDACTED:%' carry
        #    no privacy debt and stay until the operator resolves the
        #    underlying gap. The check uses NOT LIKE so both
        #    '[REDACTED:legacy]' (one-time cleanup) and
        #    '[REDACTED:shadow]' (ongoing shadow-mode inserts) survive.
        try:
            result = await db.execute(
                text(
                    "DELETE FROM public.portal_retrieval_gaps "
                    "WHERE query_text NOT LIKE '[REDACTED:%' "
                    "AND occurred_at < :cutoff"
                ),
                {"cutoff": cutoff},
            )
            counts["retrieval_gaps"] = result.rowcount or 0  # type: ignore[attr-defined]
        except Exception:
            logger.warning("telemetry_purge_retrieval_gaps_failed", exc_info=True)

        await db.commit()

    logger.info(
        "telemetry_purge_complete",
        cutoff=cutoff.isoformat(),
        purged_query_shadow=counts["query_shadow"],
        purged_retrieval_gaps=counts["retrieval_gaps"],
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
