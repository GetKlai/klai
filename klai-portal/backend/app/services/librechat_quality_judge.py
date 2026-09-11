"""LLM-as-judge quality verdicts for LibreChat conversations (SPEC-CHAT-QUALITY-LOOP-001 REQ-5).

Second rubric on top of the webchat judge (``conversation_judge.py``, REQ-2):
the same verdict schema, aimed at an internal, knowledge-system-fluent
audience instead of an anonymous web visitor. Gated per org on the
``librechat_quality_judge`` platform unlock (``portal_orgs.platform_unlocked_features``,
default off; Voys is the pilot) — no hardcoded tenant check, the same lesson
as SPEC-VOYS-HELPBOT-001 REQ-10.

LibreChat lives in a per-tenant MongoDB, not Postgres, so there is no local
row to foreign-key a judgment against: rows are stored in the same
``conversation_quality_judgments`` table with ``channel='librechat'`` and
``external_conversation_id`` (the Mongo conversationId string) as the UPSERT
key; ``conversation_id`` stays NULL.

Mongo access mirrors ``librechat_chat_context.py`` exactly — same root
credentials, same 2 s timeouts, sync ``pymongo`` inside ``asyncio.to_thread``,
database name derived from the org slug via ``provisioning_names_for_slug``.
Scoping is enforced in code: one org, its own database, one pass at a time.
``_call_judge_llm`` and ``_parse_verdict`` are reused from
``conversation_judge.py`` via import (channel-agnostic apart from the rubric,
which this pass overrides through its ``system`` argument).

Per-conversation fail-open, per-org isolation and the raw-SQL UPSERT mirror
the webchat pass, including the RLS Cat-D reason for raw SQL (see
``.claude/rules/klai/projects/portal-backend.md``, "SQLAlchemy + RLS").
"""

from __future__ import annotations

import asyncio
import json

import pymongo
import structlog
from sqlalchemy import text

from app.core.config import settings
from app.core.database import cross_org_session, tenant_scoped_session
from app.core.provisioning_names import provisioning_names_for_slug
from app.services.conversation_judge import _call_judge_llm, _parse_verdict

logger = structlog.get_logger()

# Platform-unlock key from KNOWN_FEATURES (app/core/extensions_registry.py).
_FEATURE = "librechat_quality_judge"
# Conversations fetched per org per pass — same cap as the webchat batch;
# the newest come first, already-judged ones are filtered out afterwards.
_BATCH_SIZE = 50
# Same constructor values as librechat_chat_context.py::_sync_recent_conversations.
_MONGO_TIMEOUT_MS = 2000

# Verbatim from docs/specs/SPEC-CHAT-QUALITY-LOOP-001/spec.md § REQ-5
# ("System (LETTERLIJK over te nemen, niet te herschrijven)"). The test suite
# diffs this string against the spec file byte for byte — do not edit the
# wording here, edit the spec and regenerate.
LIBRECHAT_JUDGE_SYSTEM_PROMPT = """You are a quality judge for an internal AI knowledge assistant conversation.
The user is an authenticated employee who already knows how to use this
tool and how to talk to a knowledge system — unlike an anonymous public
visitor, they can rephrase, drill down, and are not put off by a
clarifying question. You will be shown a full conversation transcript
(user + assistant turns) plus known signals, and must output a single JSON
object evaluating the conversation.

Known signals given to you (trust these, do not re-derive them):
- explicit_rating: the user's thumbs rating on the last assistant answer,
  if any ("thumbsUp" / "thumbsDown" / null)
- had_error: whether any assistant turn was flagged as a system/generation
  error by the platform itself

Output EXACTLY one JSON object, no other text, matching this schema:
{
  "outcome": one of "resolved" | "partially_resolved" | "unresolved" |
    "escalated" | "out_of_scope" | "abandoned_early",
  "failure_category": one of "retrieval_miss" | "retrieval_wrong" |
    "generation_error" | "policy_refusal" | "scope_mismatch" |
    "user_confusion" | "none" (use "none" only when outcome is "resolved"),
  "reasoning": 1-3 sentences in the conversation's own language, MUST quote
    or closely paraphrase a specific turn as evidence. Never invent evidence
    not present in the transcript.
  "confidence": one of "high" | "medium" | "low" — use "low" whenever the
    user gave no explicit signal and the transcript alone is ambiguous.
  "suggested_action": a short actionable note for a knowledge-base editor,
    or null if none applies.
}

Category definitions (adjusted for this internal audience):
- retrieval_miss: the assistant found no relevant source and said so, or
  answered generically without grounding in this organisation's own
  knowledge base.
- retrieval_wrong: the assistant referenced material that does not answer
  what the user actually asked.
- generation_error: the right information was available, but the
  assistant's answer is wrong, incomplete, or hallucinated beyond it — or
  had_error is true.
- policy_refusal: not typically applicable to this internal audience; use
  only if the assistant explicitly declined for a stated policy reason.
- scope_mismatch: the question is entirely outside what this
  organisation's knowledge base could ever cover.
- user_confusion: the user's own question was unclear or
  self-contradictory, not a system fault.
- none: only for outcome "resolved".

There is no escalation/handoff concept on this channel and no
citation-firewall — judge the answer on its own merits, not on whether a
source was formally cited. Trust explicit_rating over your own read of
tone when they conflict: an explicit thumbsUp always yields "resolved"
outcome with high confidence; an explicit thumbsDown never yields
"resolved"."""

_ORG_SQL = """
                SELECT id, slug
                  FROM portal_orgs
                 WHERE deleted_at IS NULL
                   AND :feature = ANY(platform_unlocked_features)
                 ORDER BY id
"""

_EXCLUDE_SQL = """
                SELECT external_conversation_id
                  FROM conversation_quality_judgments
                 WHERE channel = 'librechat'
                   AND org_id = :org_id
"""

# Raw SQL for the same RLS Cat-D reason as the webchat UPSERT; conflict
# target is the separate unique key on external_conversation_id (migration
# d3c8b6a5f1e0). channel is pinned to 'librechat' in the SQL text, and
# conversation_id is never written — it stays NULL for this channel.
_UPSERT_SQL = """
INSERT INTO conversation_quality_judgments
      (org_id, channel, external_conversation_id, outcome, failure_category,
       reasoning, confidence, suggested_action, model_used, judged_at)
VALUES (:org_id, 'librechat', :external_conversation_id, :outcome, :failure_category,
        :reasoning, :confidence, :suggested_action, :model_used, NOW())
ON CONFLICT (external_conversation_id) DO UPDATE SET
    outcome = EXCLUDED.outcome,
    failure_category = EXCLUDED.failure_category,
    reasoning = EXCLUDED.reasoning,
    confidence = EXCLUDED.confidence,
    suggested_action = EXCLUDED.suggested_action,
    model_used = EXCLUDED.model_used,
    judged_at = NOW()
"""


def _mongo_client() -> pymongo.MongoClient:
    """Exact copy of librechat_chat_context.py's constructor — root
    credentials, per-tenant database chosen in code."""
    return pymongo.MongoClient(
        host=settings.mongodb_container_name,
        port=27017,
        username=settings.mongo_root_username,
        password=settings.mongo_root_password,
        authSource="admin",
        serverSelectionTimeoutMS=_MONGO_TIMEOUT_MS,
        connectTimeoutMS=_MONGO_TIMEOUT_MS,
        socketTimeoutMS=_MONGO_TIMEOUT_MS,
    )


def _sync_fetch_conversations(db_name: str, limit: int) -> list[dict]:
    """The tenant's most recent LibreChat conversations, newest first."""
    with _mongo_client() as client:
        cursor = (
            client[db_name]
            .conversations.find(
                {},
                {"conversationId": 1, "title": 1, "updatedAt": 1, "_id": 0},
            )
            .sort("updatedAt", pymongo.DESCENDING)
            .limit(limit)
        )
        return list(cursor)


def _sync_fetch_messages(db_name: str, conversation_ids: list[str]) -> dict[str, list[dict]]:
    """All messages of the given conversations, grouped and ordered by createdAt.

    Field set verified against the pinned LibreChat fork (v0.8.7,
    packages/data-schemas/src/schema/message.ts); there is no structured
    sources field on this channel, hence no had_citation_refusal signal.
    """
    projection = {
        "conversationId": 1,
        "isCreatedByUser": 1,
        "text": 1,
        "content": 1,
        "sender": 1,
        "createdAt": 1,
        "unfinished": 1,
        "error": 1,
        "feedback": 1,
        "_id": 0,
    }
    with _mongo_client() as client:
        cursor = (
            client[db_name]
            .messages.find({"conversationId": {"$in": conversation_ids}}, projection)
            .sort("createdAt", pymongo.ASCENDING)
        )
        grouped: dict[str, list[dict]] = {}
        for doc in cursor:
            cid = doc.get("conversationId")
            if isinstance(cid, str):
                grouped.setdefault(cid, []).append(doc)
        return grouped


def _message_text(doc: dict) -> str:
    """Message body: ``text`` is the primary field; ``content`` is LibreChat's
    mixed array (or plain string) of parts — flatten its text parts. Anything
    else yields an empty string, which the caller drops instead of judging."""
    text_value = doc.get("text")
    if isinstance(text_value, str) and text_value.strip():
        return text_value
    content = doc.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [p["text"] for p in content if isinstance(p, dict) and isinstance(p.get("text"), str)]
        return "\n".join(parts)
    return ""


def _turns_from_messages(docs: list[dict]) -> list[dict]:
    """Judge transcript: role from the real ``isCreatedByUser`` field; messages
    with no usable text are skipped instead of becoming empty turns."""
    turns = []
    for doc in docs:
        content = _message_text(doc)
        if not content.strip():
            continue
        turns.append({"role": "user" if doc.get("isCreatedByUser") else "assistant", "content": content})
    return turns


def _derive_signals(docs: list[dict]) -> dict:
    """The two known signals of the REQ-5 rubric, from stored data.

    ``explicit_rating``: ``feedback.rating`` of the LAST assistant message
    (same last-answer rule as the webchat pass).
    ``had_error``: any assistant message flagged ``error: true`` by the
    platform itself.
    """
    assistant_docs = [d for d in docs if not d.get("isCreatedByUser")]
    explicit_rating = None
    if assistant_docs:
        feedback = assistant_docs[-1].get("feedback")
        if isinstance(feedback, dict) and isinstance(feedback.get("rating"), str):
            explicit_rating = feedback["rating"]
    return {
        "explicit_rating": explicit_rating,
        "had_error": any(d.get("error") for d in assistant_docs),
    }


def _build_user_prompt(turns: list[dict], *, explicit_rating: str | None, had_error: bool) -> str:
    """JSON-encoded transcript (role/content per turn) plus the two signals —
    same ``json.dumps(..., ensure_ascii=False)`` shape as the webchat pass;
    this channel has no sources field to carry."""
    return json.dumps(
        {
            "transcript": turns,
            "signals": {
                "explicit_rating": explicit_rating,
                "had_error": had_error,
            },
        },
        ensure_ascii=False,
    )


async def _judge_org(org_id: int, slug: str) -> int:
    """Judge recent, not-yet-judged LibreChat conversations of one org.

    The exclude query and the UPSERT run inside a tenant-scoped session so
    RLS Cat-D enforces the org boundary even if the WHERE clause regressed;
    Mongo and the LLM are reached per conversation with fail-open.
    """
    db_name = provisioning_names_for_slug(slug, domain=settings.domain).mongodb_database
    judged = 0
    async with tenant_scoped_session(org_id) as db:
        excl_result = await db.execute(text(_EXCLUDE_SQL), {"org_id": org_id})
        excluded = {row.external_conversation_id for row in excl_result.all()}

        conversations = await asyncio.to_thread(_sync_fetch_conversations, db_name, _BATCH_SIZE)
        todo = [
            conv["conversationId"]
            for conv in conversations
            if isinstance(conv.get("conversationId"), str)
            and conv["conversationId"]
            and conv["conversationId"] not in excluded
        ]
        if not todo:
            return 0

        messages_by_cid = await asyncio.to_thread(_sync_fetch_messages, db_name, todo)

        for cid in todo:
            docs = messages_by_cid.get(cid, [])
            turns = _turns_from_messages(docs)
            if not turns:
                # Nothing to judge (e.g. an empty chat); skipping without a
                # row keeps it eligible once it has real content.
                logger.debug("librechat_judge_no_usable_turns", org_id=org_id, conversation_id=cid)
                continue
            signals = _derive_signals(docs)
            user_prompt = _build_user_prompt(turns, **signals)
            try:
                raw = await _call_judge_llm(
                    model=settings.conversation_judge_model,
                    user=user_prompt,
                    system=LIBRECHAT_JUDGE_SYSTEM_PROMPT,
                )
            except Exception:
                # One flaky LLM call costs this conversation, not the batch;
                # nothing was written so the next pass retries.
                logger.warning("librechat_judge_llm_failed", conversation_id=cid, exc_info=True)
                continue
            try:
                verdict = _parse_verdict(raw)
            except Exception:
                logger.warning("librechat_judge_parse_failed", conversation_id=cid, exc_info=True)
                continue
            await db.execute(
                text(_UPSERT_SQL),
                {
                    "org_id": org_id,
                    "external_conversation_id": cid,
                    **verdict,
                    "model_used": settings.conversation_judge_model,
                },
            )
            judged += 1

        await db.commit()

    if judged:
        logger.info("librechat_quality_judged", org_id=org_id, judged_count=judged)
    return judged


async def librechat_judge_run_once() -> dict[str, int]:
    """One pass: discover orgs with the platform unlock, judge each.

    Returns ``{"org_count": …, "judged_count": …}`` like the webchat pass.
    Called by ``conversation_judge_loop`` after the webchat pass — there is
    deliberately no second lifespan task with its own interval.
    """
    async with cross_org_session() as db:
        org_result = await db.execute(text(_ORG_SQL), {"feature": _FEATURE})
        orgs = [(row.id, row.slug) for row in org_result.all()]

    judged_total = 0
    for org_id, slug in orgs:
        # One tenant's Mongo or DB failure must not strand the other tenants'
        # verdicts; the pass continues and the next cycle retries.
        try:
            judged_total += await _judge_org(org_id, slug)
        except Exception:
            logger.exception("librechat_judge_org_failed", org_id=org_id)

    return {"org_count": len(orgs), "judged_count": judged_total}
