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

The prompts, the schema and the request text live in ``klai-libs/chat-prompts``
so the internal chat path judges by the same words and reads the same shape;
this module is the widget's caller and owns the repair.

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
import time

import httpx
import structlog
from klai_chat_prompts import (
    GROUNDING_CHECK_SYSTEM_PROMPT,
    GROUNDING_NOTHING_LEFT,
    GROUNDING_REPAIR_SYSTEM_PROMPT,
    GroundedStatement,
    GroundingCheck,
    grounding_check_response_format,
    grounding_check_user_content,
    grounding_repair_user_content,
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


async def _post(
    *,
    system_prompt: str,
    user_content: str,
    settings: Settings,
    transport_timeout: float,
    response_format: dict | None = None,
) -> str:
    """One model call. Raises on any failure; the callers decide what that means."""
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
    async with httpx.AsyncClient(timeout=transport_timeout) as client:
        response = await client.post(
            f"{settings.litellm_base_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.litellm_master_key}"},
            json=payload,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]


async def _call(
    *,
    system_prompt: str,
    user_content: str,
    settings: Settings,
    timeout_seconds: float,
    response_format: dict | None = None,
) -> str | None:
    try:
        async with asyncio.timeout(timeout_seconds):
            return await _post(
                system_prompt=system_prompt,
                user_content=user_content,
                settings=settings,
                transport_timeout=timeout_seconds,
                response_format=response_format,
            )
    except Exception:
        logger.warning("answer_grounding_call_failed", exc_info=True)
    return None


# A check that outlives its budget is not thrown away. The visitor gets the
# answer at 4 s exactly as before, but the call keeps running and its verdict is
# logged as ``answer_grounding_late``. Without that, a timed-out check leaves no
# trace of what it would have found, and the daily report can say how often the
# budget was hit but never whether those answers carried an invention — which is
# the only question that decides whether the budget should move. Measured on
# 2026-09-18 after query paraphrases went live: about one first turn in six ran
# out of time, and a replay of 4 s against 8 s could not tell whether that
# mattered (logboek 2.37).
_LATE_CHECK_CEILING_SECONDS = 20.0
# A provider slowdown would otherwise turn every visitor turn into one more
# background call, each with its own client, feeding the slowdown it came from.
# Past this many the new one is cancelled and says so; the measurement is a
# sample, and a sample does not need every turn of an outage.
_LATE_CHECK_LIMIT = 20
_late_checks: set[asyncio.Task] = set()


async def _post_with_deadline(**kwargs) -> str:
    """``_post`` under one TOTAL deadline.

    httpx's timeout applies per phase (connect, write, read), so a slow response
    could run well past it; this bounds the whole call.
    """
    async with asyncio.timeout(_LATE_CHECK_CEILING_SECONDS):
        return await _post(**kwargs)


def _log_late_verdict(task: asyncio.Task, *, org_id: int | str | None, started: float) -> None:
    _late_checks.discard(task)
    elapsed = round(time.perf_counter() - started, 2)
    if task.cancelled():
        return
    if (error := task.exception()) is not None:
        logger.warning("answer_grounding_late_failed", org_id=org_id, elapsed_s=elapsed, error=repr(error)[:120])
        return
    check = parse_grounding_check(task.result())
    if check is None:
        logger.warning("answer_grounding_late_unparseable", org_id=org_id, elapsed_s=elapsed)
        return
    logger.info(
        "answer_grounding_late",
        org_id=org_id,
        elapsed_s=elapsed,
        statements=len(check.statements),
        unsupported=len(check.unsupported),
        contradicted=sum(1 for item in check.statements if item.support == "contradicted"),
        worth_repairing=check.worth_repairing,
    )


async def check_grounding(
    *,
    question: str,
    draft: str,
    articles: list[tuple[str, str]],
    settings: Settings,
    org_id: int | str | None = None,
) -> GroundingCheck | None:
    """List the reply's statements with their evidence; ``None`` when it did not return in time.

    ``None`` is what the caller has always acted on. A call that is merely slow
    keeps running and reports what it would have said; see ``_log_late_verdict``.
    """
    started = time.perf_counter()
    task = asyncio.create_task(
        _post_with_deadline(
            system_prompt=GROUNDING_CHECK_SYSTEM_PROMPT,
            user_content=grounding_check_user_content(question=question, articles=articles, draft=draft),
            settings=settings,
            transport_timeout=_LATE_CHECK_CEILING_SECONDS,
            response_format=grounding_check_response_format(),
        )
    )
    try:
        # shield: the budget ends the wait, not the call.
        content = await asyncio.wait_for(asyncio.shield(task), _CHECK_TIMEOUT_SECONDS)
    except TimeoutError:
        logger.warning("answer_grounding_call_failed", reason="budget", budget_s=_CHECK_TIMEOUT_SECONDS)
        if len(_late_checks) >= _LATE_CHECK_LIMIT:
            task.cancel()
            logger.warning("answer_grounding_late_skipped", org_id=org_id, running=len(_late_checks))
            return None
        _late_checks.add(task)
        task.add_done_callback(lambda done: _log_late_verdict(done, org_id=org_id, started=started))
        return None
    except asyncio.CancelledError:
        # shield() also protects the call from the request being cancelled, so
        # without this a shutdown or a dropped connection would leave it running
        # untracked, with no callback to log or release it.
        task.cancel()
        raise
    except Exception:
        logger.warning("answer_grounding_call_failed", exc_info=True)
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
    content = await _call(
        system_prompt=GROUNDING_REPAIR_SYSTEM_PROMPT,
        user_content=grounding_repair_user_content(draft=draft, unsupported=unsupported),
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
