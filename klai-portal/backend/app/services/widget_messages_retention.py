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
- cross-org read, per-org write: the candidate SELECT spans all orgs, the
  anonymizing UPDATEs and the DELETE run in a session bound to each org (the
  Cat-D WITH CHECK rejects UPDATEs from a cross-org session). The candidate
  query applies a per-org cutoff (SPEC-CHAT-QUALITY-LOOP-001 open item #2:
  Voys keeps 90 days, every other tenant stays on the global default).
- chunked: bounded-time deletes avoid long-running transactions.
- audit: emits ``widget_messages.retention_deleted`` via structlog.
- resilient: a failing org is logged and skipped; the other orgs are still purged.
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
from app.core.database import cross_org_session, tenant_scoped_session

logger = structlog.get_logger()

# 24-hour cadence: at most a ~25-hour-old row survives one cycle.
RETENTION_INTERVAL_SECONDS = 24 * 60 * 60
# Chunk size for each DELETE pass — avoids long-running table locks.
_CHUNK_SIZE = 10_000


async def _purge_org_chunk(org_id: int, message_ids: list[int], conversation_ids: list[int]) -> tuple[int, int]:
    """Anonymize, then delete, one org's share of a chunk in ONE transaction.

    The session is bound to ``org_id`` because the Cat-D policies on
    ``conversation_quality_judgments`` and ``widget_conversations`` carry
    ``WITH CHECK (org_id = _rls_current_org_id())``: in a cross-org session
    that function is NULL, so every updated row is rejected (incident
    2026-09-16, every run failed with InsufficientPrivilegeError). That
    check is a deliberate security boundary, so the fix is the tenant scope,
    not a looser policy.

    The two UPDATEs and the DELETE share this single transaction on purpose:
    a message may only disappear together with the name, e-mail and judge
    quote tied to its conversation. If any anonymization fails, the DELETE
    rolls back with it and the messages stay until a later run can
    anonymize them first.

    Returns ``(deleted_messages, anonymized_judgments)``.
    """
    async with tenant_scoped_session(org_id) as db:
        # Bounded waits, so one org cannot stall this sequential run: a blocked
        # or runaway statement raises and takes the caller's skip-and-log path
        # (portal-api sets no server-side timeouts). SET LOCAL runs after the
        # after_begin set_config() calls of this same transaction and ends
        # with it. Each statement touches at most _CHUNK_SIZE (10 000) rows by
        # primary key or the unique conversation_id index, which takes well
        # under a second, so 60 s only ends a run that is truly stuck. Row
        # locks from live writers (widget turns, the judge) last milliseconds
        # to one judge batch; 5 s of waiting means the lock is long-held, and
        # the next daily run retries the org.
        await db.execute(text("SET LOCAL lock_timeout = '5s'"))
        await db.execute(text("SET LOCAL statement_timeout = '60s'"))
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
        await db.commit()
    return result.rowcount or 0, anon_result.rowcount or 0  # type: ignore[attr-defined]


async def _retention_run_once() -> dict[str, int]:
    """Delete expired widget_messages rows in chunks.

    REQ-4 (SPEC-CHAT-QUALITY-LOOP-001 §11): before each DELETE pass, nulls
    ``conversation_quality_judgments.reasoning`` (and stamps ``anonymized_at``)
    for the conversations whose messages are being purged — a judge quote may
    not outlive the conversation it came from, while the judgment row itself
    survives (FK is SET NULL, not CASCADE). The visitor contact details on
    ``widget_conversations`` are cleared in the same pass, for the same reason.

    Candidates are selected cross-org (read-only); the writes run per org in
    ``_purge_org_chunk``. An org whose purge fails is logged and excluded for
    the rest of this run, so it neither blocks other orgs nor gets retried in
    a tight loop.

    Returns a dict with ``deleted_count`` (total rows removed) and
    ``chunk_count`` (number of candidate chunks processed).
    """
    default_days = settings.widget_messages_retention_days
    default_cutoff = datetime.now(UTC) - timedelta(days=default_days)
    deleted_total = 0
    anonymized_total = 0
    chunk_count = 0
    failed_org_ids: list[int] = []

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
                    SELECT wm.id, wm.conversation_id, wm.org_id
                    FROM widget_messages wm
                    LEFT JOIN portal_orgs po ON po.id = wm.org_id
                    WHERE wm.created_at < now() - (
                        COALESCE(po.widget_messages_retention_days, :default_days) * interval '1 day'
                    )
                      AND wm.org_id <> ALL(CAST(:failed_org_ids AS integer[]))
                    ORDER BY wm.id
                    LIMIT :chunk_size
                    """
                ),
                {"default_days": default_days, "chunk_size": _CHUNK_SIZE, "failed_org_ids": failed_org_ids},
            )
            rows = list(candidate_result.all())
        if not rows:
            break

        by_org: dict[int, tuple[list[int], set[int]]] = {}
        for message_id, conversation_id, org_id in rows:
            message_ids, conversation_ids = by_org.setdefault(org_id, ([], set()))
            message_ids.append(message_id)
            conversation_ids.add(conversation_id)

        chunk_deleted = 0
        failed_before = len(failed_org_ids)
        for org_id, (message_ids, conversation_ids) in by_org.items():
            try:
                deleted, anonymized = await _purge_org_chunk(org_id, message_ids, sorted(conversation_ids))
            except Exception:
                logger.exception("widget_messages_retention_org_failed", org_id=org_id)
                failed_org_ids.append(org_id)
                continue
            chunk_deleted += deleted
            anonymized_total += anonymized

        chunk_count += 1
        deleted_total += chunk_deleted

        if len(rows) < _CHUNK_SIZE or (chunk_deleted == 0 and len(failed_org_ids) == failed_before):
            break

    logger.info(
        "widget_messages.retention_deleted",
        deleted_count=deleted_total,
        anonymized_judgments=anonymized_total,
        chunk_count=chunk_count,
        failed_org_ids=failed_org_ids,
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
