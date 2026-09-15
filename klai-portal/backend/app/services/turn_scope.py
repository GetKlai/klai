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
"""

from __future__ import annotations

import asyncio

import httpx
import structlog
from pydantic import BaseModel, ConfigDict

from app.core.config import Settings

logger = structlog.get_logger()

_CLASSIFIER_TIMEOUT_SECONDS = 2.0

_SYSTEM_PROMPT = (
    "You decide ONE thing about a visitor's message to a company's help chat: "
    "would a correct answer to it assert anything that could be checked against "
    "the world outside this chat window?\n"
    "Answer false (conversational) ONLY when a correct answer is purely about "
    "the conversation itself: which languages you can reply in, that you are an "
    "AI assistant, a greeting, a thank-you, an apology, or a request to repeat, "
    "rephrase or translate what was already said.\n"
    "Answer true (asserts_about_the_world) for everything else, including any "
    "question about the company, its products, prices, procedures, availability "
    "or outages, AND any general factual question about the wider world.\n"
    "When the message mixes the two, answer true. When you are unsure, answer "
    "true. Answering true is always safe; it only means the answer must be "
    "grounded in sources."
)


class TurnScope(BaseModel):
    """Whether the answer to this turn would assert something checkable."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    asserts_about_the_world: bool


async def classify_turn_scope(text: str, settings: Settings) -> bool | None:
    """Return True when the turn needs grounding, False when it does not.

    ``None`` means "could not decide"; callers MUST treat that exactly like
    True. Never raises — a classification failure may cost a turn its friendly
    answer, never its correctness.
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
                                "name": "turn_scope",
                                "strict": True,
                                "schema": TurnScope.model_json_schema(),
                            },
                        },
                    },
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                return TurnScope.model_validate_json(content, strict=True).asserts_about_the_world
    except Exception:
        logger.warning("turn_scope_classification_failed", exc_info=True)
    return None


def is_conversational(scope: bool | None) -> bool:
    """One place decides how ``None`` reads, so no call site can get it wrong."""
    return scope is False
