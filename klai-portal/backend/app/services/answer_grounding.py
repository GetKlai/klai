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

The prompts and the schema live in ``klai-libs/chat-prompts`` so the internal
chat path judges by the same words; this module is the widget's caller.

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

import httpx
import structlog
from klai_chat_prompts import (
    GROUNDING_CHECK_SYSTEM_PROMPT,
    GROUNDING_NOTHING_LEFT,
    GROUNDING_REPAIR_SYSTEM_PROMPT,
    GroundedStatement,
    GroundingCheck,
    grounding_check_response_format,
    parse_grounding_check,
)

from app.core.config import Settings

logger = structlog.get_logger()

# Measured live on 2026-09-18. The check runs in 1.9 s at the median and 3.4 s in
# the slowest tenth; a short repair takes under a second. On a long step list the
# two together still filled the whole 5 s budget and put a turn at 9.0 s, on top
# of 3.2 s of writing. The visitor waits for all of it, so the check gets 4 s and
# the repair 3 s: the median is untouched and the worst case drops by 3 s. A
# timeout keeps the answer exactly as it was.
_CHECK_TIMEOUT_SECONDS = 4.0
_REPAIR_TIMEOUT_SECONDS = 3.0
NOTHING_LEFT = GROUNDING_NOTHING_LEFT


def render_articles(articles: list[tuple[str, str]]) -> str:
    """Every article the answer model received, whole.

    Clipping here is what made the earlier checks judge against material the
    model had but the checker did not: on a first run against stored copies cut
    at 700 characters, correct steps came back as "not in the articles" and the
    repair then gutted good answers.
    """
    return "\n\n".join(f"### {title}\n{text}" for title, text in articles)


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
        system_prompt=GROUNDING_CHECK_SYSTEM_PROMPT,
        user_content=(
            f"Visitor question:\n{question}\n\n"
            f"Help-article excerpts:\n{render_articles(articles) or '(none)'}\n\n"
            f"Reply:\n{draft}"
        ),
        settings=settings,
        timeout_seconds=_CHECK_TIMEOUT_SECONDS,
        response_format=grounding_check_response_format(),
    )
    if content is None:
        return None
    check = parse_grounding_check(content)
    if check is None:
        logger.warning("answer_grounding_unparseable")
    return check


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
        system_prompt=GROUNDING_REPAIR_SYSTEM_PROMPT,
        user_content=f"Unsupported statements:\n{listed}\n\nReply:\n{draft}",
        settings=settings,
        timeout_seconds=_REPAIR_TIMEOUT_SECONDS,
    )
    if content is None:
        return None
    stripped = content.strip()
    if stripped == NOTHING_LEFT:
        return NOTHING_LEFT
    # Empty output is a failed call, not a verdict that nothing was left: only
    # the sentinel may cost the visitor a sourced answer.
    return cleanup_repair_artifacts(stripped) or None
