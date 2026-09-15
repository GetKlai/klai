"""What a turn's answer would assert, decided beside the answer model.

SPEC-RAG-ANSWER-TIERS-001 REQ-1. The widget used to have two outcomes for a
turn: grounded in the help articles, or "I can't find this in our help
articles" plus an appointment offer. A visitor who asked, in English, whether
they could talk English got the second one — the retriever searched the help
articles for "also can english talk", the selector rejected all three
candidates as unsupported, and the citation firewall replaced the answer.
Measured over seven days before this module: 73 of 540 widget turns (13.5%)
ended on that refusal, every one of them with three candidates retrieved and
all three rejected.

The missing distinction is not confidence, it is subject matter. Three classes:

* **about us** — checkable about this organisation: prices, features,
  procedures, availability, outages. Must be grounded or refused, unchanged.
* **about the world** — checkable regardless of provider. Broad mode with
  explicit consent owns this (SPEC-VOYS-HELPBOT-001 REQ-7), unchanged.
* **about this conversation** — asserts nothing checkable outside this chat
  window. This module detects that third class so it can leave the knowledge
  pipeline entirely.

The decidable test, the sibling of REQ-7's "could this sentence be written
unchanged by any other phone provider?", is: **could this sentence be checked
against anything outside this chat window?** "Yes, I can answer in English" is
about the conversation. "Voys supports English-speaking customers" is about us
and needs an article. The two look alike and are in different classes, which is
why the boundary is a test and not a list of phrases.

A list of phrases is in fact the thing this deliberately does NOT do. Path A
carries one, and measured on real sentences it matches "Wie ben je?" and "What
can you do?" while missing "Can I also talk english?", "Spreek je Duits?" and
"dankjewel". This repo already documents that failure mode for hand-curated
language lists.

Cost: the call runs concurrently with retrieval (see the ``asyncio.gather`` in
``app/api/partner.py``), so it adds no wall-clock to the 86.5% of turns that do
not need it. Adaptive-RAG routes the same way for the same reason.

Fail-safe direction matters and is deliberate: any failure — timeout, bad JSON,
upstream error — returns ``None``, and ``None`` means "treat as a knowledge
question". A broken classifier therefore restores exactly today's behaviour and
can never open the grounding firewall (REQ-3).

Known ceiling, named rather than hidden: this reads only the visitor's latest
turn, while the answer model also sees the history and the retrieved articles.
A MISCLASSIFICATION is therefore the residual risk — a turn wrongly called
conversational renders without the firewall. Review on 2026-09-15 found one
concrete instance and it was this prompt's own fault: it used to call "translate
that" conversational, which let a grounded price answer be restated without its
sources. That clause is gone. The structural upgrade, if the residual ever
justifies it, is to validate the FINAL answer for organisation-specific claims
rather than trusting a pre-generation label; REQ-4's counter is the measurement
that would tell us whether it does.

Caller-supplied ``system`` messages are not part of this risk on the widget
path: ``_normalize_llm_message`` keeps only ``user`` and ``assistant`` roles, so
an injected claim never reaches the model. Verified 2026-09-15.
"""

from __future__ import annotations

import asyncio
from typing import Literal

import httpx
import structlog
from pydantic import BaseModel, ConfigDict

from app.core.config import Settings

logger = structlog.get_logger()

_CLASSIFIER_TIMEOUT_SECONDS = 2.0

_SYSTEM_PROMPT = (
    "Classify the visitor's message to a company's help chat into exactly one "
    "category.\n\n"
    "conversation — a correct answer is only about this chat: which languages "
    "you can reply in, that you are an AI, a greeting, a thank-you, an apology, "
    "or a remark about the conversation itself. Nothing in the answer could be "
    "checked against the outside world.\n"
    "organisation — the answer would state something about this company: its "
    "products, prices, procedures, settings, availability or outages.\n"
    "world — the answer would state a general fact that is true regardless of "
    "which company is asked.\n\n"
    "Repeating, rephrasing or translating an earlier answer is never "
    "conversation: the claims inside it are still claims. When a message mixes "
    "categories, pick the one the visitor most needs answered."
)


class TurnScope(BaseModel):
    """Which of the three classes this turn belongs to."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    category: Literal["conversation", "organisation", "world"]


async def classify_turn_scope(text: str, settings: Settings) -> str | None:
    """Return ``conversation``, ``organisation``, ``world``, or ``None``.

    ``None`` means "could not decide"; callers MUST treat it like a question
    that needs grounding. Never raises — a classification failure may cost a
    turn its friendly answer, never its correctness.

    An enum, not a boolean, and the reason is measured rather than stylistic.
    The first version asked for one boolean and told the model that answering
    "yes, this needs grounding" was always the safe choice. On klai-fast it then
    answered exactly that for EVERY message, including "dankjewel" — a free pass
    taken every time. Probed against the running service on 2026-09-15: naming
    the three classes and making the model pick one scores 11/11 on the same
    set, where the boolean scored 0 on the six conversational cases.
    """
    if not text or not text.strip():
        return None
    try:
        async with asyncio.timeout(_CLASSIFIER_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(timeout=_CLASSIFIER_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    f"{settings.litellm_base_url}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {settings.litellm_master_key}"},
                    json={
                        "model": settings.extraction_model,
                        "messages": [
                            {"role": "system", "content": _SYSTEM_PROMPT},
                            {"role": "user", "content": text},
                        ],
                        "response_format": {
                            "type": "json_schema",
                            "json_schema": {
                                "name": "turn_category",
                                "strict": True,
                                "schema": TurnScope.model_json_schema(),
                            },
                        },
                    },
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                return TurnScope.model_validate_json(content, strict=True).category
    except Exception:
        logger.warning("turn_scope_classification_failed", exc_info=True)
    return None


def is_conversational(scope: str | None) -> bool:
    """One place decides how ``None`` reads, so no call site can get it wrong."""
    return scope == "conversation"


def scope_label(scope: str | None) -> str:
    """The class as one queryable word, for all three outcomes.

    REQ-4. Logging only the conversational turns would make the share of each
    class unmeasurable, which is how a boundary drifts unnoticed — the failure
    the widget already lived through. ``classifier_failed`` is kept apart from
    ``knowledge`` even though both behave identically: they are the same
    behaviour for different reasons, and a rising failure rate is a signal about
    the classifier rather than about visitors.
    """
    return scope or "classifier_failed"


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
