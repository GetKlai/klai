"""Statement-level grounding check, and the repair that follows from it.

SPEC-RAG-ANSWER-JUDGES-001 §"Geen onzin". The light answer judge asks one
question about a whole draft and answers it on klai-fast; measured against 54
hand-checked answers on 2026-09-17 it caught 22% of the answers that state
something the articles do not, and 7 of its 17 alarms were false. Asking a
heavier model to list every concrete statement and quote the article text that
supports it caught 96% at 77% precision, and is the only measure that moved the
number at all: temperature 0, a stricter generation instruction and removing the
profile's closing rule each left the rate where it was (49% of answers with any
unsupported statement, 29% serious).

The answer is repaired, not refused. Deleting flagged sentences in code cost one
good answer and damaged two of 150; letting the same model edit the reply cost
none, so that is what runs here. What the repair cannot fix is a real article
applied to the wrong question (6 of 18 serious cases); that stays a knowledge
problem.

Both calls run on ``settings.answer_grounding_model`` (klai-medium: 900 RPM,
against the ~90 RPM the answer model shares with every other small call). The
check runs beside the light judge, so it costs its own latency once: measured
2.1 s median, 4.7 s in the slowest tenth, plus 0.85 s for a repair.
"""

from __future__ import annotations

import asyncio
import re
from typing import Literal

import httpx
import structlog
from pydantic import BaseModel, ConfigDict

from app.core.config import Settings

logger = structlog.get_logger()

_CHECK_TIMEOUT_SECONDS = 8.0
_REPAIR_TIMEOUT_SECONDS = 8.0
# The check reads the article text the model received. Clipping it is what made
# the earlier checks judge against material the model had but the checker did
# not: 369 of 657 stored article pieces were cut at 700 characters.
_ARTICLE_MAX_CHARS = 4000
_MAX_ARTICLES = 8

NOTHING_LEFT = "NOTHING_LEFT"

_CHECK_SYSTEM_PROMPT = (
    "You audit a reply from a company's help chat before a visitor sees it. You get the visitor's "
    "question, the help-article excerpts the reply was written from, and the reply. Judge ONLY against "
    "the excerpts, never against what you know.\n\n"
    "List every concrete statement in the reply about the company or its product: a step, a menu path, a "
    "button or field name, a setting, a feature or capability, a limitation, a policy, a price, an amount, "
    "a time frame, a phone number or address, a cause of a problem, or a claim that something will now "
    "work. Split a list of steps into one statement per step. Skip greetings, empathy, restating or "
    "summarising the visitor's question or situation, any sentence ending in a question mark, a sentence "
    "that only introduces a list, an offer to book an appointment or contact support, saying something was "
    "not found, and a sentence that repeats back what the visitor said about their own situation.\n\n"
    "For each statement:\n"
    "statement: the reply's words, copied exactly.\n"
    "evidence: the shortest excerpt text, copied exactly character for character, that states the same "
    "thing; empty if there is none.\n"
    "support: supported (the excerpts state it, a faithful paraphrase or translation counts), "
    "not_in_articles (the excerpts do not state it, including a plausible step, label or consequence you "
    "would have to infer or guess), contradicted (the excerpts say otherwise, or the text is about a "
    "different product, situation or country than the reply applies it to).\n"
    "A blank in an excerpt such as 'Ga naar .' means a link was removed; a reply that fills in a name for "
    "it is not_in_articles."
)

_REPAIR_SYSTEM_PROMPT = (
    "You edit a reply from a company's help chat before a visitor sees it. A checker found statements in "
    "it that the help articles do not support. Return the reply with ONLY those statements removed or cut "
    "back to the part the articles do support. Keep every other sentence exactly as written, in the same "
    "order and format, and renumber a step list so it stays consecutive with no empty entries. Never add a "
    "fact, step, name, number or advice that is not already in the reply, and never invert the meaning of a "
    "sentence you keep: when a removal would leave a sentence saying the opposite or saying nothing, remove "
    "that whole sentence. Leave a sentence that only says something was not found exactly as it is. Where a "
    "removal leaves a gap the "
    "visitor needs, add one short sentence in the reply's language saying that this part is not described "
    "in our help articles and that they can book an appointment with an employee for a definite answer. "
    "Add that sentence at most once. Remove a closing line that claims the task is now done if the steps no "
    f"longer support it. If nothing useful remains, return exactly: {NOTHING_LEFT}. Return only the edited "
    "reply."
)


class GroundedStatement(BaseModel):
    """One concrete statement the reply makes about the organisation."""

    model_config = ConfigDict(extra="forbid")

    statement: str
    evidence: str
    support: Literal["supported", "not_in_articles", "contradicted"]


class GroundingCheck(BaseModel):
    """Every statement in one reply, with the article text that backs it."""

    model_config = ConfigDict(extra="forbid")

    statements: list[GroundedStatement]

    @property
    def unsupported(self) -> list[GroundedStatement]:
        return [item for item in self.statements if item.support != "supported"]

    @property
    def worth_repairing(self) -> bool:
        """Two unsupported statements, or one that contradicts an article.

        A single flag is right 77% of the time; this threshold was right 92% of
        the time on the same 54 hand-checked answers. Repairing on one flag
        deleted sentences that only restated the visitor's own situation.
        """
        unsupported = self.unsupported
        return len(unsupported) >= 2 or any(item.support == "contradicted" for item in unsupported)


def render_articles(articles: list[tuple[str, str]]) -> str:
    """The article excerpts as the checker reads them: title plus text, barely clipped."""
    return "\n\n".join(f"### {title}\n{text[:_ARTICLE_MAX_CHARS]}" for title, text in articles[:_MAX_ARTICLES])


async def _call(
    *,
    system_prompt: str,
    user_content: str,
    settings: Settings,
    timeout_seconds: float,
    response_format: dict | None = None,
) -> str | None:
    payload: dict = {
        "model": settings.answer_grounding_model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    }
    if response_format is not None:
        payload["response_format"] = response_format
    try:
        async with asyncio.timeout(timeout_seconds):
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(
                    f"{settings.litellm_base_url}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {settings.litellm_master_key}"},
                    json=payload,
                )
                response.raise_for_status()
                return response.json()["choices"][0]["message"]["content"]
    except Exception:
        logger.warning("answer_grounding_call_failed", exc_info=True)
    return None


async def check_grounding(
    *, question: str, draft: str, articles: list[tuple[str, str]], settings: Settings
) -> GroundingCheck | None:
    """List the reply's statements with their evidence; ``None`` when the call fails."""
    content = await _call(
        system_prompt=_CHECK_SYSTEM_PROMPT,
        user_content=(
            f"Visitor question:\n{question}\n\n"
            f"Help-article excerpts:\n{render_articles(articles) or '(none)'}\n\n"
            f"Reply:\n{draft}"
        ),
        settings=settings,
        timeout_seconds=_CHECK_TIMEOUT_SECONDS,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "grounding_check", "strict": True, "schema": GroundingCheck.model_json_schema()},
        },
    )
    if content is None:
        return None
    try:
        return GroundingCheck.model_validate_json(content)
    except Exception:
        logger.warning("answer_grounding_unparseable", exc_info=True)
    return None


# A removal can leave "1." or "**Stap 2:**" with nothing behind it. The repair
# model is told to renumber, and mostly does; this is the mechanical backstop.
_EMPTY_STEP_RE = re.compile(r"(?m)^\s*(?:[-*+]|\d+[.)]|\*\*Stap \d+:\*\*|Stap \d+:)\s*$\n?")


def cleanup_repair_artifacts(text: str) -> str:
    """Drop list markers and step labels the repair left without content."""
    cleaned = _EMPTY_STEP_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


async def repair_answer(*, draft: str, unsupported: list[GroundedStatement], settings: Settings) -> str | None:
    """Return the reply without its unsupported statements, ``None`` on failure.

    Returns :data:`NOTHING_LEFT` when the model judges that nothing useful is
    left; the caller decides what the visitor sees then.
    """
    if not unsupported:
        return draft
    listed = "\n".join(f"- {item.statement}" for item in unsupported)
    content = await _call(
        system_prompt=_REPAIR_SYSTEM_PROMPT,
        user_content=f"Unsupported statements:\n{listed}\n\nReply:\n{draft}",
        settings=settings,
        timeout_seconds=_REPAIR_TIMEOUT_SECONDS,
    )
    if content is None:
        return None
    stripped = content.strip()
    if stripped == NOTHING_LEFT:
        return NOTHING_LEFT
    return cleanup_repair_artifacts(stripped) or NOTHING_LEFT
