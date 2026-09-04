"""Widget conversation outcome labelling — derived from data we already store.

The widget dashboard otherwise only measures volume (conversations, messages,
top queries, hourly activity). Volume cannot tell a public helpdesk whether a
visitor actually got helped. This module derives a coarse ``outcome`` label
per conversation from signals that already exist: handoff sessions, message
roles/ordering, the canned helpdesk "contact support" refusal, visitor ratings,
and the quiet period (``settings.widget_outcome_quiet_period_minutes``).

Derivation rules, evaluated in this order (first match wins):

1. ``escalated`` — a ``widget_handoff_sessions`` row is attached to the
   conversation, OR the last assistant answer is the canned helpdesk refusal
   that directs the visitor to support (``no_citable_sources_message(...,
   helpdesk=True)`` — the exact string the chat stores when it could not
   answer, in either language).
2. ``abandoned`` — the conversation ends on a visitor message with no
   assistant answer after it, OR it consists of exactly one exchange
   (question + answer) that was never followed up on within the quiet period
   and the visitor did not rate the answer positively.
3. ``resolved`` — the conversation ends on an assistant answer carrying a
   positive rating (``rating='thumbsUp'``), OR it ends on an assistant answer
   and the visitor asked nothing further within the quiet period. A positive
   rating is checked before the one-exchange rule: an explicit satisfaction
   signal outweighs the single-turn bounce heuristic.
4. ``unknown`` — none of the above closes (e.g. the messages of an old
   conversation were already purged by the retention worker).

**WARNING: this is a heuristic, not a verdict.** "resolved" does NOT mean the
visitor's real-world problem was solved — it means the conversation ended on
an assistant answer without further questions or escalation signals. A visitor
who silently gave up after a confident-sounding wrong answer is labelled
resolved. Use the distribution as a directional funnel metric for the helpdesk,
never as per-conversation truth.

Worker design mirrors ``widget_messages_retention.py`` (periodic loop,
resilient, cancellable) with one deliberate difference: the labelling UPDATEs
run in a ``tenant_scoped_session(org_id)`` per tenant so RLS Cat-D stays
enforced on every read and write. Only the discovery query (which orgs have
unlabelled quiet conversations at all) runs cross-org — a legitimate
cross-tenant use per the ``cross_org_session`` docstring.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import structlog
from klai_chat_prompts import no_citable_sources_message
from sqlalchemy import text

from app.core.config import settings
from app.core.database import cross_org_session, tenant_scoped_session

logger = structlog.get_logger()

Outcome = Literal["resolved", "escalated", "abandoned", "unknown"]

# 15-minute cadence: a conversation is labelled within one interval after its
# quiet period elapses. Cheap SELECT (partial index on unlabelled rows).
OUTCOME_INTERVAL_SECONDS = 15 * 60
# Conversations labelled per tenant per pass — bounds transaction time,
# the next pass picks up where this one stopped.
_BATCH_SIZE = 500

# The two canned helpdesk refusal strings the widget chat stores verbatim as
# the assistant answer when it could not ground a reply (see
# app/services/partner_chat.py::_compose_answer_with_sources helpdesk branch).
# Generated from the single source of truth instead of duplicated here; the
# public helper picks Dutch on any DUTCH_QUERY_MARKERS token, English otherwise.
_SUPPORT_REFERRAL_TEXTS: frozenset[str] = frozenset(
    {
        no_citable_sources_message("", helpdesk=True).strip(),
        no_citable_sources_message("de", helpdesk=True).strip(),
    }
)


@dataclass(frozen=True)
class ConversationTurn:
    """One stored widget message, reduced to what the outcome rules read."""

    role: str  # 'user' | 'assistant'
    content: str
    rating: str | None  # 'thumbsUp' / 'thumbsDown' / None — assistant rows only


def derive_outcome(
    turns: list[ConversationTurn],
    *,
    has_handoff: bool,
    quiet_period_elapsed: bool = True,
) -> Outcome:
    """Label one conversation from its stored turns. Pure function, no I/O.

    Rules and their precedence are documented in the module docstring.
    ``quiet_period_elapsed`` is False while the visitor may still reply (last
    message newer than the configured quiet period); no absence-based rule may
    fire then, so the result stays 'unknown' until the worker sees the
    conversation go quiet. The background loop only queries rows that already
    passed the window, but the flag keeps the function honest for direct
    callers and tests.
    """
    if not turns:
        # Nothing to derive from — e.g. widget_messages rows already purged by
        # the retention worker while the conversation row survives.
        return "unknown"

    last_turn = turns[-1]
    last_assistant = next((t for t in reversed(turns) if t.role == "assistant"), None)

    # 1. Escalated: live human handoff, or the assistant pointed to support.
    if has_handoff:
        return "escalated"
    if last_assistant is not None and last_assistant.content.strip() in _SUPPORT_REFERRAL_TEXTS:
        return "escalated"

    # 2. Abandoned: ends on an unanswered visitor message.
    if last_turn.role == "user":
        return "abandoned"

    # 3. Resolved (explicit signal): ends on an answer the visitor rated up.
    if last_assistant is not None and last_assistant.rating == "thumbsUp":
        return "resolved"

    # 2b. Abandoned: exactly one exchange and no follow-up within the window.
    user_turns = [t for t in turns if t.role == "user"]
    if len(user_turns) == 1 and len(turns) == 2:
        return "abandoned" if quiet_period_elapsed else "unknown"

    # 3b. Resolved (absence signal): answer stood, nothing asked after it.
    if last_turn.role == "assistant":
        return "resolved" if quiet_period_elapsed else "unknown"

    return "unknown"


async def _label_org(org_id: int, cutoff: datetime) -> int:
    """Label unlabelled quiet conversations of one tenant. Returns the count.

    Everything after the org_id filter runs inside a tenant-scoped session,
    so RLS Cat-D enforces the boundary even if the WHERE clause regressed.
    """
    labelled = 0
    async with tenant_scoped_session(org_id) as db:
        conv_result = await db.execute(
            text(
                """
                SELECT id, last_message_at
                  FROM widget_conversations
                 WHERE org_id = :org_id
                   AND outcome IS NULL
                   AND is_preview = false
                   AND last_message_at < :cutoff
                 ORDER BY last_message_at
                 LIMIT :batch_size
                """
            ),
            {"org_id": org_id, "cutoff": cutoff, "batch_size": _BATCH_SIZE},
        )
        conversations = [(row.id, row.last_message_at) for row in conv_result.all()]
        if not conversations:
            return 0

        conv_ids = [cid for cid, _ in conversations]

        msg_result = await db.execute(
            text(
                """
                SELECT conversation_id, role, content, rating
                  FROM widget_messages
                 WHERE conversation_id = ANY(CAST(:conv_ids AS bigint[]))
                 ORDER BY conversation_id, sequence ASC
                """
            ),
            {"conv_ids": conv_ids},
        )
        turns_by_conv: dict[int, list[ConversationTurn]] = {cid: [] for cid in conv_ids}
        for row in msg_result.all():
            turns_by_conv[row.conversation_id].append(
                ConversationTurn(role=row.role, content=row.content, rating=row.rating)
            )

        handoff_result = await db.execute(
            text(
                """
                SELECT DISTINCT conversation_id
                  FROM widget_handoff_sessions
                 WHERE conversation_id = ANY(CAST(:conv_ids AS bigint[]))
                """
            ),
            {"conv_ids": conv_ids},
        )
        handoff_ids = {row.conversation_id for row in handoff_result.all()}

        now = datetime.now(UTC)
        quiet_delta = timedelta(minutes=settings.widget_outcome_quiet_period_minutes)
        for conv_id, last_message_at in conversations:
            outcome = derive_outcome(
                turns_by_conv[conv_id],
                has_handoff=conv_id in handoff_ids,
                quiet_period_elapsed=last_message_at <= now - quiet_delta,
            )
            # Guard against a race with a fresh visitor message: only label
            # rows that are still unlabelled AND still quiet at UPDATE time.
            await db.execute(
                text(
                    """
                    UPDATE widget_conversations
                       SET outcome = :outcome
                     WHERE id = :conv_id
                       AND org_id = :org_id
                       AND outcome IS NULL
                       AND last_message_at < :cutoff
                    """
                ),
                {
                    "outcome": outcome,
                    "conv_id": conv_id,
                    "org_id": org_id,
                    "cutoff": now - quiet_delta,
                },
            )
            labelled += 1

        await db.commit()

    if labelled:
        logger.info(
            "widget_conversations.outcome_labelled",
            org_id=org_id,
            labelled_count=labelled,
        )
    return labelled


async def _outcome_run_once() -> dict[str, int]:
    """One pass: discover orgs with quiet unlabelled conversations, label each.

    Returns ``{"org_count": …, "labelled_count": …}``.
    """
    cutoff = datetime.now(UTC) - timedelta(minutes=settings.widget_outcome_quiet_period_minutes)

    async with cross_org_session() as db:
        org_result = await db.execute(
            text(
                """
                SELECT DISTINCT org_id
                  FROM widget_conversations
                 WHERE outcome IS NULL
                   AND is_preview = false
                   AND last_message_at < :cutoff
                 ORDER BY org_id
                """
            ),
            {"cutoff": cutoff},
        )
        org_ids = list(org_result.scalars().all())

    labelled_total = 0
    for org_id in org_ids:
        # One tenant's failure must not strand the other tenants' labels; the
        # loop continues and the next pass retries.
        try:
            labelled_total += await _label_org(org_id, cutoff)
        except Exception:
            logger.exception("widget_outcome_org_failed", org_id=org_id)

    return {"org_count": len(org_ids), "labelled_count": labelled_total}


async def widget_outcome_loop() -> None:
    """FastAPI-lifespan-attached widget conversation outcome labelling loop.

    Sleeps 60 s on startup so the app can finish wiring before the first
    DB hit. Then runs ``_outcome_run_once`` every OUTCOME_INTERVAL_SECONDS
    until cancelled. Exceptions are logged and do not abort the loop.
    """
    await asyncio.sleep(60)
    while True:
        try:
            await _outcome_run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("widget_outcome_loop_unexpected_error")
        await asyncio.sleep(OUTCOME_INTERVAL_SECONDS)
