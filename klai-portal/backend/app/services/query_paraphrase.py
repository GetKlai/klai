"""Two other ways to ask the visitor's first question, for retrieval only.

SPEC-RAG-ANSWER-JUDGES-001, logbook 2.27 and 2.33. A visitor's own words often
miss the article that answers them: on 54 real first questions of the help
widget an answering passage sat in the top-8 for 35%, and for 13 of the 16
questions a paraphrase rescued, the article was nowhere in the literal query's
top-50. It is not a matter of too few words: thin questions (six words or
fewer) gained nothing, long specific ones went from 31% to 60%. Two paraphrases,
each run by retrieval-api as its own pass and RRF-fused with the literal query,
raised the share to 59% (14 wins, 1 loss), and end to end, blind with the
articles shown, won 63 against 43 over two rounds while stating something the
articles do not carry less often (30 to 23).

Only the first turn: a follow-up already has the history to search on, and the
same idea measured there (the previous answer as an extra pass) won at the
retrieval level and drew end to end (65 against 61, logbook 2.32).

Costs a call on the roomier model, 0.6 s at the median. The visitor's exact
words stay the primary query; the paraphrases may add no detail, device or
cause the visitor did not name.
"""

from __future__ import annotations

import time

import structlog
from pydantic import BaseModel, ConfigDict

from app.core.config import Settings
from app.services.turn_judge import structured_judge_call

logger = structlog.get_logger()

PARAPHRASE_SYSTEM_PROMPT = (
    "Je krijgt de eerste vraag van een bezoeker van het helpcentrum van een zakelijke dienstverlener. "
    "Schrijf precies twee alternatieve formuleringen die een andere bezoeker met hetzelfde probleem had "
    "kunnen typen: in dezelfde taal als de vraag, elk hooguit vijftien woorden, met de woorden die in "
    "helpartikelen staan. Voeg geen details, apparaten of oorzaken toe die de bezoeker niet noemt."
)

_TIMEOUT_SECONDS = 2.5
_MAX_VARIANTS = 2
# retrieval-api rejects a variant over 500 characters (models.py); the prompt
# asks for fifteen words, so this only guards against a runaway answer.
_MAX_VARIANT_CHARS = 500


class QueryParaphrases(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    variants: list[str]


async def first_question_variants(
    messages: list[dict], query: str, settings: Settings, *, support_mode: bool
) -> list[str]:
    """Paraphrases for a first support-mode question; a follow-up has its history to search on.

    "First" counts the visitor's turns, not the assistant's: the browser widget
    seeds every conversation with its welcome line as an assistant message and
    sends it back with the first question.
    """
    if not support_mode or sum(message.get("role") == "user" for message in messages) > 1:
        return []
    return await paraphrase_first_question(query, settings)


async def paraphrase_first_question(question: str, settings: Settings) -> list[str]:
    """Up to two paraphrases of ``question``; empty on any failure, so retrieval runs as before."""
    started = time.perf_counter()
    result = await structured_judge_call(
        name="query_paraphrase",
        system_prompt=PARAPHRASE_SYSTEM_PROMPT,
        user_content=question,
        schema=QueryParaphrases,
        timeout_seconds=_TIMEOUT_SECONDS,
        settings=settings,
        model=settings.retrieval_paraphrase_model,
    )
    variants: list[str] = []
    for raw in result.variants if result is not None else []:
        text = " ".join(raw.split())[:_MAX_VARIANT_CHARS]
        if text and text.lower() != question.strip().lower() and text not in variants:
            variants.append(text)
    variants = variants[:_MAX_VARIANTS]
    logger.info(
        "partner_chat_query_paraphrase",
        variants=len(variants),
        paraphrase_ms=int((time.perf_counter() - started) * 1000),
    )
    return variants
