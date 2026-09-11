"""LLM-as-judge quality verdicts for finished webchat conversations.

SPEC-CHAT-QUALITY-LOOP-001 REQ-2. The heuristic in ``widget_outcome.py``
labels conversations resolved/escalated/abandoned/unknown from stored
signals, and says honestly in its own docstring that a large 'unknown'
share is the argument for a judge that can READ a transcript. This is that
judge: a background pass sends each finished conversation plus its three
known signals (explicit_rating, had_citation_refusal, had_handoff) to an
LLM and stores the verdict in ``conversation_quality_judgments`` (one row
per conversation, UPSERT).

Selection reuses the heuristic's finish signal instead of re-deriving one:
a conversation enters the queue when ``widget_conversations.outcome`` is
non-NULL (the quiet-period labeller already decided it is done) and no
judgment row exists yet. Preview conversations are skipped, same exception
as every other widget background pass.

Worker design mirrors ``widget_outcome.py`` exactly: per-tenant batches run
in a ``tenant_scoped_session(org_id)`` so RLS Cat-D stays enforced on every
read and write; only the org-discovery query runs cross-org. The LiteLLM
call mirrors ``app.klai_feedback.triage._call_triage_llm``. Verdicts are
written with raw SQL ``INSERT … ON CONFLICT … DO UPDATE`` because the
SQLAlchemy ORM appends an implicit RETURNING that breaks on RLS Cat-D
tables under an insert/update-role mismatch (see
``.claude/rules/klai/projects/portal-backend.md``, "SQLAlchemy + RLS").

**Fail-open per conversation, never per batch.** An unparsable or
schema-violating judge response logs ``conversation_judge_parse_failed``
with ``exc_info=True`` and skips only that conversation — the same shape as
``_safe_ascore()`` in knowledge-ingest's ``judge_client.py``. A transient
LLM or DB error likewise costs one conversation or one tenant, never the
pass; the next cycle retries (nothing was written, so the row stays
queued). One deliberate difference from the upstream pattern: the judge
returns a fresh verdict for the same prompt, and the UPSERT overwrites —
there is no model_key-style version history here by design (SPEC REQ-2:
exactly one current verdict per conversation).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import httpx
import structlog
from sqlalchemy import text

from app.core.config import settings
from app.core.database import cross_org_session, tenant_scoped_session
from app.services.widget_outcome import _SUPPORT_REFERRAL_TEXTS
from app.trace import get_trace_headers

logger = structlog.get_logger()

# 30-minute cadence: unlike the heuristic labeller this makes an LLM call per
# conversation, and quality verdicts do not need to be fresh within minutes.
JUDGE_INTERVAL_SECONDS = 30 * 60
# Conversations judged per tenant per pass — bounds the pass's wall time and
# LiteLLM load; the next pass picks up where this one stopped. Deliberately
# smaller than widget_outcome's 500: a pass here is ~50 network calls, not
# ~500 UPDATEs.
_BATCH_SIZE = 50

# Enum sets mirrored from the ck_cqj_* CHECK constraints on
# conversation_quality_judgments (app/models/conversation_quality.py) — a
# verdict outside them would fail at INSERT time anyway, so reject it as a
# parse failure instead and keep the conversation queued for a retry.
_OUTCOMES = frozenset(
    {"resolved", "partially_resolved", "unresolved", "escalated", "out_of_scope", "abandoned_early"}
)
_FAILURE_CATEGORIES = frozenset(
    {
        "retrieval_miss",
        "retrieval_wrong",
        "generation_error",
        "policy_refusal",
        "scope_mismatch",
        "user_confusion",
        "none",
    }
)
_CONFIDENCES = frozenset({"high", "medium", "low"})

# Verbatim from docs/specs/SPEC-CHAT-QUALITY-LOOP-001/spec.md § REQ-2
# ("Judge-prompt (exact, niet vrij te ontwerpen door de bouwer)"). The test
# suite diffs this string against the spec file byte for byte — do not edit
# the wording here, edit the spec and regenerate.
JUDGE_SYSTEM_PROMPT = """You are a quality judge for a webchat AI assistant conversation. You will be
shown a full conversation transcript (visitor + assistant turns) plus known
signals, and must output a single JSON object evaluating the conversation.

Known signals given to you (trust these, do not re-derive them):
- explicit_rating: the visitor's thumbs rating on the last assistant answer,
  if any ("thumbsUp" / "thumbsDown" / null)
- had_citation_refusal: whether any assistant answer was the fixed
  "no sources found" refusal
- had_handoff: whether the conversation was escalated to a human/booking flow

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
    visitor gave no explicit signal and the transcript alone is ambiguous.
  "suggested_action": a short actionable note for a knowledge-base editor,
    or null if none applies.
}

Category definitions:
- retrieval_miss: the assistant found no relevant source and said so (or
  gave the fixed refusal).
- retrieval_wrong: the assistant cited a source, but it does not actually
  answer the visitor's question.
- generation_error: the right source was cited, but the assistant's answer
  is wrong, incomplete, or hallucinated beyond the source.
- policy_refusal: the assistant correctly declined per policy (e.g.
  broad-mode consent, pricing/contract commitment ban) — not a knowledge gap.
- scope_mismatch: the question is entirely outside what this product/company
  does.
- user_confusion: the visitor's own question was unclear or
  self-contradictory, not a system fault.
- none: only for outcome "resolved".

Trust explicit_rating over your own read of tone when they conflict: an
explicit thumbsUp always yields "resolved" outcome with high confidence; an
explicit thumbsDown never yields "resolved"."""


@dataclass(frozen=True)
class JudgeTurn:
    """One stored widget message, as the judge sees it."""

    role: str  # 'user' | 'assistant'
    content: str
    sources: list | dict | str | None  # citation payload stored on the row
    rating: str | None  # 'thumbsUp' / 'thumbsDown' / None — assistant rows only


def _derive_signals(turns: list[JudgeTurn], *, had_handoff: bool) -> dict:
    """The three known signals the SPEC hands to the judge, from stored data.

    ``explicit_rating``: rating of the LAST assistant turn (a rating moved to
    a later answer does not retroactively rate the earlier one).
    ``had_citation_refusal``: any assistant turn is verbatim the fixed
    no-citable-sources refusal — the same ``_SUPPORT_REFERRAL_TEXTS`` set
    ``widget_outcome.derive_outcome`` matches against, not a re-invention.
    ``had_handoff``: passed in by the caller from widget_handoff_sessions,
    same existence check as widget_outcome.
    """
    last_assistant = next((t for t in reversed(turns) if t.role == "assistant"), None)
    return {
        "explicit_rating": last_assistant.rating if last_assistant is not None else None,
        "had_citation_refusal": any(
            t.role == "assistant" and t.content.strip() in _SUPPORT_REFERRAL_TEXTS for t in turns
        ),
        "had_handoff": had_handoff,
    }


def _build_user_prompt(
    turns: list[JudgeTurn],
    *,
    explicit_rating: str | None,
    had_citation_refusal: bool,
    had_handoff: bool,
) -> str:
    """JSON-encoded transcript (role, content, sources per turn) + the three
    signals — same ``json.dumps(..., ensure_ascii=False)`` shape as
    ``triage._build_triage_prompt``."""
    return json.dumps(
        {
            "transcript": [
                {"role": t.role, "content": t.content, "sources": t.sources} for t in turns
            ],
            "signals": {
                "explicit_rating": explicit_rating,
                "had_citation_refusal": had_citation_refusal,
                "had_handoff": had_handoff,
            },
        },
        ensure_ascii=False,
    )


async def _call_judge_llm(
    *, model: str, user: str, system: str = JUDGE_SYSTEM_PROMPT
) -> str:
    """One LiteLLM chat completion — mirrors ``triage._call_triage_llm``.

    ``system`` defaults to the webchat rubric; the LibreChat pass (REQ-5)
    passes its own verbatim rubric, the rest of the call is channel-agnostic.
    """
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{settings.litellm_base_url}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.litellm_master_key}",
                **get_trace_headers(),
            },
            json={
                "model": model,
                "temperature": 0.1,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )
        resp.raise_for_status()
        return str(resp.json()["choices"][0]["message"]["content"])


def _parse_verdict(raw: str) -> dict:
    """Validate the judge's JSON against the SPEC schema.

    Raises on anything the storage CHECK constraints would reject (invalid
    enum, wrong shape, non-string required field) so the caller's fail-open
    path sees one catch-all; never returns a partially trusted dict.
    """
    stripped = raw.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1].rsplit("```", 1)[0]
    data = json.loads(stripped)
    if not isinstance(data, dict):
        raise TypeError(f"judge response is not a JSON object: {type(data).__name__}")

    outcome = data.get("outcome")
    confidence = data.get("confidence")
    failure_category = data.get("failure_category")
    reasoning = data.get("reasoning")
    suggested_action = data.get("suggested_action")

    if not isinstance(outcome, str) or outcome not in _OUTCOMES:
        raise ValueError(f"invalid outcome: {outcome!r}")
    if not isinstance(confidence, str) or confidence not in _CONFIDENCES:
        raise ValueError(f"invalid confidence: {confidence!r}")
    if failure_category is not None and (
        not isinstance(failure_category, str) or failure_category not in _FAILURE_CATEGORIES
    ):
        raise ValueError(f"invalid failure_category: {failure_category!r}")
    if not isinstance(reasoning, str) or not reasoning.strip():
        raise ValueError(f"invalid reasoning: {reasoning!r}")
    if suggested_action is not None and not isinstance(suggested_action, str):
        raise ValueError(f"invalid suggested_action: {suggested_action!r}")

    return {
        "outcome": outcome,
        "failure_category": failure_category,
        "reasoning": reasoning.strip(),
        "confidence": confidence,
        "suggested_action": suggested_action.strip() if suggested_action else None,
    }


# Raw SQL, not ORM: the INSERT/UPDATE role combination on this Cat-D RLS
# table breaks on SQLAlchemy's implicit RETURNING (portal-backend rule
# "SQLAlchemy + RLS"), same reason widget_audit/widget_handoff UPSERT raw.
_UPSERT_SQL = """
INSERT INTO conversation_quality_judgments
      (org_id, conversation_id, channel, outcome, failure_category,
       reasoning, confidence, suggested_action, model_used, judged_at)
VALUES (:org_id, :conversation_id, :channel, :outcome, :failure_category,
        :reasoning, :confidence, :suggested_action, :model_used, NOW())
ON CONFLICT (conversation_id) DO UPDATE SET
    outcome = EXCLUDED.outcome,
    failure_category = EXCLUDED.failure_category,
    reasoning = EXCLUDED.reasoning,
    confidence = EXCLUDED.confidence,
    suggested_action = EXCLUDED.suggested_action,
    model_used = EXCLUDED.model_used,
    judged_at = NOW()
"""


async def _judge_org(org_id: int) -> int:
    """Judge finished, not-yet-judged conversations of one tenant. Returns count.

    Everything after the org_id filter runs inside a tenant-scoped session,
    so RLS Cat-D enforces the boundary even if the WHERE clause regressed.
    """
    judged = 0
    async with tenant_scoped_session(org_id) as db:
        conv_result = await db.execute(
            text(
                """
                SELECT wc.id
                  FROM widget_conversations wc
                  LEFT JOIN conversation_quality_judgments cqj
                         ON cqj.conversation_id = wc.id
                 WHERE wc.org_id = :org_id
                   AND wc.outcome IS NOT NULL
                   AND wc.is_preview = false
                   AND cqj.id IS NULL
                 ORDER BY wc.last_message_at
                 LIMIT :batch_size
                """
            ),
            {"org_id": org_id, "batch_size": _BATCH_SIZE},
        )
        conv_ids = [row.id for row in conv_result.all()]
        if not conv_ids:
            return 0

        msg_result = await db.execute(
            text(
                """
                SELECT conversation_id, role, content, sources, rating
                  FROM widget_messages
                 WHERE conversation_id = ANY(CAST(:conv_ids AS bigint[]))
                 ORDER BY conversation_id, sequence ASC
                """
            ),
            {"conv_ids": conv_ids},
        )
        turns_by_conv: dict[int, list[JudgeTurn]] = {cid: [] for cid in conv_ids}
        for row in msg_result.all():
            turns_by_conv[row.conversation_id].append(
                JudgeTurn(
                    role=row.role, content=row.content, sources=row.sources, rating=row.rating
                )
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

        for conv_id in conv_ids:
            turns = turns_by_conv[conv_id]
            signals = _derive_signals(turns, had_handoff=conv_id in handoff_ids)
            user_prompt = _build_user_prompt(turns, **signals)
            try:
                raw = await _call_judge_llm(model=settings.conversation_judge_model, user=user_prompt)
            except Exception:
                # One tenant's flaky LLM call costs this conversation, not
                # the batch; nothing was written so the next pass retries.
                logger.warning("conversation_judge_llm_failed", conversation_id=conv_id, exc_info=True)
                continue
            try:
                verdict = _parse_verdict(raw)
            except Exception:
                logger.warning("conversation_judge_parse_failed", conversation_id=conv_id, exc_info=True)
                continue
            # conversation_id is NULLABLE on the table only for the later
            # anonymization sweep (REQ-4); rows written here always set it.
            await db.execute(
                text(_UPSERT_SQL),
                {
                    "org_id": org_id,
                    "conversation_id": conv_id,
                    "channel": "webchat",
                    **verdict,
                    "model_used": settings.conversation_judge_model,
                },
            )
            judged += 1

        await db.commit()

    if judged:
        logger.info("conversation_quality_judged", org_id=org_id, judged_count=judged)
    return judged


async def _judge_run_once() -> dict[str, int]:
    """One pass: discover orgs with unjudged finished conversations, judge each.

    Returns ``{"org_count": …, "judged_count": …}``.
    """
    async with cross_org_session() as db:
        org_result = await db.execute(
            text(
                """
                SELECT DISTINCT wc.org_id
                  FROM widget_conversations wc
                  LEFT JOIN conversation_quality_judgments cqj
                         ON cqj.conversation_id = wc.id
                 WHERE wc.outcome IS NOT NULL
                   AND wc.is_preview = false
                   AND cqj.id IS NULL
                 ORDER BY wc.org_id
                """
            ),
        )
        org_ids = list(org_result.scalars().all())

    judged_total = 0
    for org_id in org_ids:
        # One tenant's failure must not strand the other tenants' verdicts;
        # the pass continues and the next cycle retries.
        try:
            judged_total += await _judge_org(org_id)
        except Exception:
            logger.exception("conversation_judge_org_failed", org_id=org_id)

    return {"org_count": len(org_ids), "judged_count": judged_total}


async def conversation_judge_loop() -> None:
    """FastAPI-lifespan-attached conversation quality judge loop.

    Sleeps 60 s on startup so the app can finish wiring before the first
    DB hit. Then runs ``_judge_run_once`` (webchat) followed by
    ``librechat_judge_run_once`` (REQ-5) every JUDGE_INTERVAL_SECONDS until
    cancelled. Exceptions are logged and do not abort the loop; each
    channel's pass has its own try/except so neither one's failure stops
    the other.
    """
    await asyncio.sleep(60)
    while True:
        try:
            await _judge_run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("conversation_judge_loop_unexpected_error")
        try:
            # Lazy import: librechat_quality_judge reuses helpers from this
            # module, so a module-level import here would be circular.
            from app.services.librechat_quality_judge import librechat_judge_run_once

            await librechat_judge_run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("librechat_judge_loop_unexpected_error")
        await asyncio.sleep(JUDGE_INTERVAL_SECONDS)
