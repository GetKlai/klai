"""REQ-8 (SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 Finding B-5, HIGH):
Widget message retention worker.

Background loop that runs every 24 hours and deletes widget_messages rows
older than each row's org's retention window in chunks of 10 000 rows.
The per-org window is ``portal_orgs.widget_messages_retention_days`` when
set, else ``settings.widget_messages_retention_days`` (the global default,
7 days since 2026-09-11). Before each delete pass it anonymizes the
``conversation_quality_judgments.reasoning`` of the purged conversations
(REQ-4, SPEC-CHAT-QUALITY-LOOP-001 §11).

Design mirrors ``telemetry_purge.py``:
- cross-org: the loop itself is not tenant-scoped, but the candidate query
  now applies a per-org cutoff (SPEC-CHAT-QUALITY-LOOP-001 open item #2:
  Voys keeps 90 days, every other tenant stays on the global default).
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

    REQ-4 (SPEC-CHAT-QUALITY-LOOP-001 §11): before each DELETE pass, nulls
    ``conversation_quality_judgments.reasoning`` (and stamps ``anonymized_at``)
    for the conversations whose messages are being purged — a judge quote may
    not outlive the conversation it came from, while the judgment row itself
    survives (FK is SET NULL, not CASCADE). The visitor contact details on
    ``widget_conversations`` are cleared in the same pass, for the same reason.

    Returns a dict with ``deleted_count`` (total rows removed) and
    ``chunk_count`` (number of DELETE passes executed).
    """
    default_days = settings.widget_messages_retention_days
    default_cutoff = datetime.now(UTC) - timedelta(days=default_days)
    deleted_total = 0
    anonymized_total = 0
    chunk_count = 0

    while True:
        async with cross_org_session() as db:
            # Per-org cutoff: portal_orgs.widget_messages_retention_days
            # overrides the global default (NULL = default). Joined on
            # widget_messages.org_id, which is already denormalised onto
            # the row for the RLS Cat-D policy, so this needs no separate
            # lookup per conversation.
            candidate_result = await db.execute(
                text(
                    """
                    SELECT wm.id, wm.conversation_id
                    FROM widget_messages wm
                    LEFT JOIN portal_orgs po ON po.id = wm.org_id
                    WHERE wm.created_at < now() - (
                        COALESCE(po.widget_messages_retention_days, :default_days) * interval '1 day'
                    )
                    ORDER BY wm.id
                    LIMIT :chunk_size
                    """
                ),
                {"default_days": default_days, "chunk_size": _CHUNK_SIZE},
            )
            rows = list(candidate_result.all())
            if not rows:
                break
            message_ids = [row[0] for row in rows]

            # Anonymize first, in this same transaction: batched per chunk,
            # keyed off the messages we are about to delete (no separate
            # cutoff computation on widget_conversations).
            conversation_ids = sorted({row[1] for row in rows})
            anon_result = await db.execute(
                text(
                    """
                    UPDATE conversation_quality_judgments
                    SET reasoning = NULL, anonymized_at = NOW()
                    WHERE conversation_id = ANY(CAST(:conversation_ids AS bigint[]))
                      AND reasoning IS NOT NULL
                    """
                ),
                {"conversation_ids": conversation_ids},
            )
            anonymized_total += anon_result.rowcount or 0  # type: ignore[attr-defined]

            # Same rule one table over: the visitor's name and e-mail were
            # given so a reviewer could answer this conversation, so they may
            # not outlive it. The conversation row itself survives for the
            # aggregate counts, without the identifiable part.
            await db.execute(
                text(
                    """
                    UPDATE widget_conversations
                    SET visitor_name = NULL, visitor_email = NULL
                    WHERE id = ANY(CAST(:conversation_ids AS bigint[]))
                      AND (visitor_name IS NOT NULL OR visitor_email IS NOT NULL)
                    """
                ),
                {"conversation_ids": conversation_ids},
            )

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
        anonymized_judgments=anonymized_total,
        chunk_count=chunk_count,
        default_cutoff=default_cutoff.isoformat(),
        default_retention_days=default_days,
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
