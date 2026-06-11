"""REQ-8 (SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 Finding B-5, HIGH):
Widget message retention worker.

Background loop that runs every 24 hours and deletes widget_messages rows
older than ``settings.widget_messages_retention_days`` days in chunks of
10 000 rows.

Design mirrors ``telemetry_purge.py``:
- cross-org: retention is platform-wide, not per-tenant.
- chunked: bounded-time deletes avoid long-running transactions.
- audit: emits ``widget_messages.retention_deleted`` via structlog.
- resilient: exceptions are caught and logged; the loop continues.
- cancellable: ``asyncio.CancelledError`` exits cleanly (lifespan shutdown).

# @MX:NOTE: [AUTO] See also: telemetry_purge.py for the canonical loop pattern.
# @MX:SPEC: SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-8
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import text

from app.core.config import settings
from app.core.database import cross_org_session

logger = structlog.get_logger()

# 24-hour cadence: at most a ~25-hour-old row survives one cycle.
RETENTION_INTERVAL_SECONDS = 24 * 60 * 60
# Chunk size for each DELETE pass — avoids long-running table locks.
_CHUNK_SIZE = 10_000


async def _retention_run_once() -> dict[str, int]:
    """Delete expired widget_messages rows in chunks.

    Returns a dict with ``deleted_count`` (total rows removed) and
    ``chunk_count`` (number of DELETE passes executed).
    """
    cutoff = datetime.now(UTC) - timedelta(days=settings.widget_messages_retention_days)
    deleted_total = 0
    chunk_count = 0

    while True:
        async with cross_org_session() as db:
            candidate_result = await db.execute(
                text(
                    """
                    SELECT id FROM widget_messages
                    WHERE created_at < :cutoff
                    ORDER BY id
                    LIMIT :chunk_size
                    """
                ),
                {"cutoff": cutoff, "chunk_size": _CHUNK_SIZE},
            )
            message_ids = list(candidate_result.scalars().all())
            if not message_ids:
                break

            result = await db.execute(
                text(
                    """
                    DELETE FROM widget_messages
                    WHERE id = ANY(CAST(:message_ids AS bigint[]))
                    """
                ),
                {"message_ids": message_ids},
            )
            rows_deleted: int = result.rowcount or 0  # type: ignore[attr-defined]
            await db.commit()

        chunk_count += 1
        deleted_total += rows_deleted

        if rows_deleted == 0 or len(message_ids) < _CHUNK_SIZE:
            break

    logger.info(
        "widget_messages.retention_deleted",
        deleted_count=deleted_total,
        chunk_count=chunk_count,
        cutoff=cutoff.isoformat(),
        retention_days=settings.widget_messages_retention_days,
    )
    return {"deleted_count": deleted_total, "chunk_count": chunk_count}


async def widget_messages_retention_loop() -> None:
    """FastAPI-lifespan-attached daily widget_messages retention loop.

    Sleeps 60 s on startup so the app can finish wiring before the first
    DB hit. Then runs ``_retention_run_once`` every RETENTION_INTERVAL_SECONDS
    until cancelled.
    """
    await asyncio.sleep(60)
    while True:
        try:
            await _retention_run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("widget_messages_retention_unexpected_error")
        await asyncio.sleep(RETENTION_INTERVAL_SECONDS)
