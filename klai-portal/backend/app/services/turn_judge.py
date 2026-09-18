"""The question judge: one call that reads the turn before the answer is written.

SPEC-RAG-ANSWER-JUDGES-001 REQ-1. It replaces two earlier classifiers on the
widget path, the escalation classification (wants a person? tone?) and the turn
scope (about this conversation, this organisation, or the world?), and adds the
one question neither asked: is the visitor's question clear enough to answer?
Before this module that last decision followed the retrieval band and a count
of words shared with an article. Measured on 2026-09-17, all 13 reviewed widget
turns since that rule shipped were answered, including all 4 with band ``low``:
"die", "heb", "kan", "geen" and "steeds" counted as direct evidence.

It reads the last few turns, not only the latest message, because "and on my
iPhone?" is clear or not depending on what came before.

Scope, the measured reason it is an enum and not a boolean: the first version
of the scope classifier asked one boolean and told the model that "yes, this
needs grounding" was always the safe choice. On klai-fast it then answered
exactly that for EVERY message, including "dankjewel". Probed against the
running service on 2026-09-15, naming the three classes and making the model
pick one scored 11/11 on the same set, where the boolean scored 0 on the six
conversational cases. Clarity is an enum for the same reason.

Cost: the call runs concurrently with retrieval (the ``asyncio.gather`` in
``app/api/partner.py``), so it adds no wall-clock beyond retrieval's own 1.0 to
1.4 s, and the scope call that used to run sequentially after a retrieval gap
is gone.

Fail direction: any failure (timeout, upstream error, bad JSON) returns
``None``, which the route reads as "scope unknown, treat as a knowledge
question; clarity clear; no escalation from the model". The regex in
``escalation_intent`` still fires. That is exactly the behaviour before this
module whenever its classifiers timed out, so a broken judge can never open the
grounding firewall.

Caller-supplied ``system`` messages never reach the judge: only ``user`` and
``assistant`` turns are rendered, the same filter the answer model gets.
"""

from __future__ import annotations

import asyncio
from typing import Literal

import httpx
import structlog
from pydantic import BaseModel, ConfigDict

from app.core.config import Settings

logger = structlog.get_logger()

_TURN_JUDGE_TIMEOUT_SECONDS = 2.0
# Six turns is three exchanges, the same window retrieval-api uses for
# coreference. Clipped per turn so one pasted log cannot fill the prefill that
# the 2 s budget is spent on; the latest visitor message is rarely that long.
_EXCERPT_TURNS = 6
_EXCERPT_TURN_CHARS = 800

_SYSTEM_PROMPT = (
    "You read a conversation between a visitor and a company's help chat and judge the visitor's "
    "LATEST message, using the earlier turns only to understand it. Return only the required schema.\n\n"
    "scope — exactly one category:\n"
    "conversation — a correct answer is only about this chat: which languages you can reply in, that "
    "you are an AI, a greeting, a thank-you, an apology, or a remark about the conversation itself. "
    "Nothing in the answer could be checked against the outside world.\n"
    "organisation — the answer would state something about this company: its products, prices, "
    "procedures, settings, availability or outages.\n"
    "world — the answer would state a general fact that is true regardless of which company is asked.\n"
    "Repeating, rephrasing or translating an earlier answer is never conversation: the claims inside it "
    "are still claims. When a message mixes categories, pick the one the visitor most needs answered.\n\n"
    "wants_human — true only when the visitor asks to involve a human. Distinguish a request from "
    "negation and from quoted or reported speech.\n\n"
    "sentiment — the visitor's own tone in the latest message.\n\n"
    "clarity — exactly one category:\n"
    "clear — a correct answer can be given from what the visitor has said so far, including earlier turns.\n"
    "ambiguous — a correct answer depends on information the visitor has not given: which product, app, "
    "device or subscription they mean, or which of several different procedures applies. A short message "
    "is not ambiguous by itself; a greeting, a thank-you or a request for a person is clear.\n\n"
    "missing — when ambiguous, a few words naming what the visitor has not told you, in the visitor's "
    "language. Empty when clear.\n\n"
    "topic — exactly one category:\n"
    "not_handled — the visitor's latest message asks about one of the subjects this help chat does not "
    "answer, listed below. Judge what the visitor wants, not which words they use.\n"
    "handled — anything else, and everything when the list below is empty."
)

_NO_SUBJECTS = "(none: every subject is handled)"


def _system_prompt(off_topic_subjects: str) -> str:
    subjects = " ".join(off_topic_subjects.split()) or _NO_SUBJECTS
    return f"{_SYSTEM_PROMPT}\n\nSubjects this help chat does not answer:\n{subjects}"


class TurnJudgement(BaseModel):
    """The question judge's verdict on the visitor's latest turn."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    topic: Literal["handled", "not_handled"]
    scope: Literal["conversation", "organisation", "world"]
    wants_human: bool
    sentiment: Literal["negative", "neutral", "positive"]
    clarity: Literal["clear", "ambiguous"]
    # Read by no code, and kept for a measured reason: without it, on production
    # klai-fast on 2026-09-17, "mijn telefoon werkt niet" was ambiguous in 1 of 3
    # rounds instead of 3 of 3, and "dankjewel" was conversation in 1 of 3
    # instead of 3 of 3. Naming what is missing makes the model decide clarity.
    missing: str


async def structured_judge_call[J: BaseModel](
    *,
    name: str,
    system_prompt: str,
    user_content: str,
    schema: type[J],
    timeout_seconds: float,
    settings: Settings,
) -> J | None:
    """One strict-json_schema call on klai-fast; ``None`` and ``<name>_failed`` on any failure."""
    failure_event = f"{name}_failed"
    try:
        async with asyncio.timeout(timeout_seconds):
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(
                    f"{settings.litellm_base_url}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {settings.litellm_master_key}"},
                    json={
                        "model": settings.extraction_model,
                        # The same turn must get the same verdict: without a
                        # fixed temperature 5 of 9 replayed questions flipped.
                        "temperature": 0,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_content},
                        ],
                        "response_format": {
                            "type": "json_schema",
                            "json_schema": {"name": name, "strict": True, "schema": schema.model_json_schema()},
                        },
                    },
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                return schema.model_validate_json(content, strict=True)
    except Exception:
        logger.warning(failure_event, exc_info=True)
    return None


def _clip(text: str) -> str:
    return text if len(text) <= _EXCERPT_TURN_CHARS else text[:_EXCERPT_TURN_CHARS].rstrip() + " […]"


def conversation_excerpt(messages: list[dict]) -> str:
    """The last few user/assistant turns, clipped, with the latest visitor message marked."""
    turns: list[tuple[str, str]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            turns.append((role, content.strip()))
    turns = turns[-_EXCERPT_TURNS:]
    latest_visitor = max((i for i, (role, _) in enumerate(turns) if role == "user"), default=-1)
    lines = []
    for index, (role, content) in enumerate(turns):
        label = "Assistant" if role == "assistant" else "Visitor"
        if index == latest_visitor:
            label = "Visitor (LATEST message)"
        lines.append(f"{label}: {_clip(content)}")
    return "\n\n".join(lines)


async def judge_turn(messages: list[dict], settings: Settings, *, off_topic_subjects: str = "") -> TurnJudgement | None:
    """Judge the visitor's latest turn; ``None`` means the judge failed. Never raises."""
    excerpt = conversation_excerpt(messages)
    if "(LATEST message)" not in excerpt:
        return None
    return await structured_judge_call(
        name="turn_judge",
        system_prompt=_system_prompt(off_topic_subjects),
        user_content=excerpt,
        schema=TurnJudgement,
        timeout_seconds=_TURN_JUDGE_TIMEOUT_SECONDS,
        settings=settings,
    )


def is_conversational(scope: str | None) -> bool:
    """One place decides how ``None`` reads, so no call site can get it wrong."""
    return scope == "conversation"


def scope_label(scope: str | None) -> str:
    """The scope as one queryable word, for all three outcomes and a failed judge.

    ``judge_failed`` is kept apart from ``organisation`` even though both behave
    identically: a rising failure rate is a signal about the judge rather than
    about visitors, and a share you cannot see is a boundary that drifts.
    """
    return scope or "judge_failed"


# Appended to the system prompt for a conversational turn. The profile the model
# receives otherwise tells it to say the help articles do not cover this and to
# offer support -- correct for a question about the organisation, wrong for
# "do you speak English", and the composer would pass such a self-written
# refusal straight through because it never looks at the words. Same shape as
# escalation_intent.ESCALATION_TURN_ADDENDUM: one turn, one instruction.
CONVERSATIONAL_TURN_ADDENDUM = (
    "\n\n[This turn] The visitor's message is about this conversation itself, "
    "not about the organisation — which languages you speak, a greeting, a "
    "thank-you, or a remark about the chat. Answer it directly and briefly in "
    "your own words. Do NOT say the help articles do not cover it, do NOT offer "
    "to look more broadly, and do NOT point to support: none of those is an "
    "answer to what was asked. Keep the rule that you never write URLs or "
    "citations. If the message ALSO asks something about the organisation, "
    "answer that part only from the help articles as usual."
)
