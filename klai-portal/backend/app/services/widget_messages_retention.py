"""REQ-8 (SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 Finding B-5, HIGH):
Widget message retention worker.

Background loop that runs every 24 hours and, per org, with that org's
retention term (``portal_orgs.widget_messages_retention_days`` when set, else
``settings.widget_messages_retention_days``, 90 days since 2026-11-12):

1. deletes widget_messages rows older than the term in chunks of 10 000 rows,
   and before each delete pass anonymizes the
   ``conversation_quality_judgments.reasoning`` of the purged conversations
   (REQ-4, SPEC-CHAT-QUALITY-LOOP-001 §11);
2. clears the visitor's name, e-mail, first question and IP/user-agent hashes
   of every conversation whose last message is older than the term;
3. clears the reasoning of every judgment, on any channel, judged longer ago
   than the term.

The rule this enforces: docs/architecture/conversation-retention-and-learning.md.
The loop mirrors ``telemetry_purge.py``:
- cross-org read, per-org write: the candidate SELECTs span all orgs, the
  anonymizing UPDATEs and the DELETE run in a session bound to each org (the
  Cat-D WITH CHECK rejects UPDATEs from a cross-org session).
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
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import cross_org_session, tenant_scoped_session

logger = structlog.get_logger()

# 24-hour cadence: at most a ~25-hour-old row survives one cycle.
RETENTION_INTERVAL_SECONDS = 24 * 60 * 60
# Chunk size for each DELETE pass — avoids long-running table locks.
_CHUNK_SIZE = 10_000

# Orgs with a conversation or a judgment that steps 2 and 3 still have to
# clear, with their effective term. Read cross-org; the writes run per org.
_ORGS_TO_EXPIRE_SQL = """
SELECT po.id AS org_id, COALESCE(po.widget_messages_retention_days, :default_days) AS days
  FROM portal_orgs po
 WHERE EXISTS (
        SELECT 1 FROM widget_conversations wc
         WHERE wc.org_id = po.id
           AND wc.last_message_at
               < now() - COALESCE(po.widget_messages_retention_days, :default_days) * interval '1 day'
           AND (wc.visitor_name IS NOT NULL OR wc.visitor_email IS NOT NULL OR wc.first_user_query IS NOT NULL
                OR wc.ip_hash IS NOT NULL OR wc.user_agent_hash IS NOT NULL)
       )
    OR EXISTS (
        SELECT 1 FROM conversation_quality_judgments cqj
         WHERE cqj.org_id = po.id
           AND cqj.reasoning IS NOT NULL
           AND cqj.judged_at
               < now() - COALESCE(po.widget_messages_retention_days, :default_days) * interval '1 day'
       )
 ORDER BY po.id
"""


def effective_retention_days(override: int | None) -> int:
    """An org's retention term: its own setting when it has one, else the default.

    The Python side of the COALESCE in the queries of this module, for callers
    that show the term to a visitor or an editor.
    """
    return override if override is not None else settings.widget_messages_retention_days


async def _bound_waits(db: AsyncSession) -> None:
    # Bounded waits, so one org cannot stall this sequential run: a blocked
    # or runaway statement raises and takes the caller's skip-and-log path
    # (portal-api sets no server-side timeouts). SET LOCAL runs after the
    # after_begin set_config() calls of this same transaction and ends
    # with it. Each statement touches at most _CHUNK_SIZE (10 000) rows by
    # primary key, or one org's rows past its term, which takes well under a
    # second, so 60 s only ends a run that is truly stuck. Row locks from
    # live writers (widget turns, the judge) last milliseconds to one judge
    # batch; 5 s of waiting means the lock is long-held, and the next daily
    # run retries the org.
    await db.execute(text("SET LOCAL lock_timeout = '5s'"))
    await db.execute(text("SET LOCAL statement_timeout = '60s'"))


async def _purge_org_chunk(org_id: int, message_ids: list[int], conversation_ids: list[int]) -> tuple[int, int]:
    """Anonymize, then delete, one org's share of a chunk in ONE transaction.

    The session is bound to ``org_id`` because the Cat-D policies on
    ``conversation_quality_judgments`` and ``widget_conversations`` carry
    ``WITH CHECK (org_id = _rls_current_org_id())``: in a cross-org session
    that function is NULL, so every updated row is rejected (incident
    2026-09-16, every run failed with InsufficientPrivilegeError). That
    check is a deliberate security boundary, so the fix is the tenant scope,
    not a looser policy.

    The UPDATE and the DELETE share this single transaction on purpose: a
    message may only disappear together with the judge quote tied to its
    conversation. If the anonymization fails, the DELETE rolls back with it
    and the messages stay until a later run can anonymize them first.

    Returns ``(deleted_messages, anonymized_judgments)``.
    """
    async with tenant_scoped_session(org_id) as db:
        await _bound_waits(db)
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


async def _expire_org(org_id: int, days: int) -> tuple[int, int]:
    """Steps 2 and 3 for one org, in one transaction bound to it (Cat-D WITH CHECK, as above).

    Selected on each row's own age and not through the messages deleted in
    this run: a conversation whose messages went in an earlier run, an
    internal-chat judgment (no conversation row here) and a judgment whose
    conversation was deleted would otherwise never be touched again. The rows
    themselves stay for the counts; ``session_key`` stays too, as it is a
    random key that says nothing about the visitor once the hashes are gone.

    Returns ``(cleared_conversations, anonymized_judgments)``.
    """
    cutoff = datetime.now(UTC) - timedelta(days=days)
    async with tenant_scoped_session(org_id) as db:
        await _bound_waits(db)
        conversations = await db.execute(
            text(
                """
                UPDATE widget_conversations
                SET visitor_name = NULL, visitor_email = NULL, first_user_query = NULL,
                    ip_hash = NULL, user_agent_hash = NULL
                WHERE org_id = :org_id
                  AND last_message_at < :cutoff
                  AND (visitor_name IS NOT NULL OR visitor_email IS NOT NULL OR first_user_query IS NOT NULL
                       OR ip_hash IS NOT NULL OR user_agent_hash IS NOT NULL)
                """
            ),
            {"org_id": org_id, "cutoff": cutoff},
        )
        judgments = await db.execute(
            text(
                """
                UPDATE conversation_quality_judgments
                SET reasoning = NULL, anonymized_at = NOW()
                WHERE org_id = :org_id
                  AND judged_at < :cutoff
                  AND reasoning IS NOT NULL
                """
            ),
            {"org_id": org_id, "cutoff": cutoff},
        )
        await db.commit()
    return conversations.rowcount or 0, judgments.rowcount or 0  # type: ignore[attr-defined]


async def _retention_run_once() -> dict[str, int]:
    """Delete expired widget_messages rows in chunks, then clear what outlived the term (steps 2 and 3).

    REQ-4 (SPEC-CHAT-QUALITY-LOOP-001 §11): before each DELETE pass, nulls
    ``conversation_quality_judgments.reasoning`` (and stamps ``anonymized_at``)
    for the conversations whose messages are being purged — a judge quote may
    not outlive the conversation it came from, while the judgment row itself
    survives (FK is SET NULL, not CASCADE).

    Candidates are selected cross-org (read-only); the writes run per org in
    ``_purge_org_chunk`` and ``_expire_org``. An org whose purge fails is
    logged and excluded from further chunks of this run, so it neither blocks
    other orgs nor gets retried in a tight loop. Steps 2 and 3 still run for
    it: they only remove data and do not depend on the messages being gone.

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

    cleared_total = 0
    anonymized_by_age_total = 0
    async with cross_org_session() as db:
        orgs = (await db.execute(text(_ORGS_TO_EXPIRE_SQL), {"default_days": default_days})).all()
    for org_id, days in orgs:
        try:
            cleared, anonymized = await _expire_org(org_id, days)
        except Exception:
            logger.exception("widget_messages_retention_org_failed", org_id=org_id, step="expire")
            if org_id not in failed_org_ids:
                failed_org_ids.append(org_id)
            continue
        cleared_total += cleared
        anonymized_by_age_total += anonymized

    logger.info(
        "widget_messages.retention_deleted",
        deleted_count=deleted_total,
        anonymized_judgments=anonymized_total,
        cleared_conversations=cleared_total,
        anonymized_judgments_by_age=anonymized_by_age_total,
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
