"""Statement-level grounding check for the internal chat, measuring only.

SPEC-RAG-ANSWER-JUDGES-001. The widget path has this check deciding what the
visitor sees: every concrete statement in a draft is listed with the article
text behind it, and an answer with two unsupported statements (or one that
contradicts an article) is repaired. Measured on 25 real widget answers on
2026-09-18, 64% of answers stated something the articles do not, and two were
invented end to end.

The internal chat asks different questions — employees paste correspondence and
tickets — so the same number was not a given. Measured on 2026-09-18 against 50
real answers from a customer's own LibreChat tenant it is worse here: 86% state
something the articles do not carry (widget: 64%), and 70% would reach the
repair threshold (widget: 40%). Internal answers are also four times longer
(median 1173 characters), which is most of the difference.

The repair the widget runs cannot simply be ported. Every internal answer is
streamed (41 of 41 turns in the week to 2026-09-18), so by the time this check
could speak the user has already read the text. Repairing would mean buffering
the whole answer and showing nothing for the length of the generation plus the
check, and the check alone takes 3.6 s at the median here. So this module still
changes nothing about the answer; what it now does is hand the caller the
verdict, and log the contradiction count separately, because "warn on every
flagged answer" and "warn only on a contradiction" are very different amounts
of noise.

It never delays the user: the caller schedules it and moves on.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from klai_chat_prompts import (
    GROUNDING_CHECK_SYSTEM_PROMPT,
    GROUNDING_NOTHING_LEFT,
    grounding_repair_system_prompt,
    GroundingCheck,
    grounding_check_response_format,
    grounding_check_user_content,
    grounding_repair_user_content,
    parse_grounding_check,
)
from klai_kb_query_rewrite import _post_to_rewrite_model, _rewrite_call_metadata

logger = logging.getLogger(__name__)

# klai-medium: 900 requests a minute, against the ~90 the answer model shares
# with every other small call on this host.
ANSWER_GROUNDING_MODEL = os.getenv("ANSWER_GROUNDING_MODEL", "klai-medium")
ANSWER_GROUNDING_API_KEY = os.getenv("LITELLM_MASTER_KEY", "")
ANSWER_GROUNDING_TIMEOUT = 8.0


def _articles(citation_chunks: list[dict]) -> list[tuple[str, str]]:
    """The retrieved chunks as (title, text), the shape the shared builder takes."""
    return [
        (chunk.get("title") or "Bron", chunk.get("text") or "")
        for chunk in citation_chunks
        if isinstance(chunk, dict) and (chunk.get("text") or "").strip()
    ]


async def log_answer_grounding(
    *,
    user_query: str,
    draft: str,
    citation_chunks: list[dict],
    kb_meta: dict[str, Any],
) -> GroundingCheck | None:
    """Check one answer and log what the articles do not support. Never raises."""
    if not ANSWER_GROUNDING_API_KEY or not draft.strip():
        return None
    payload = {
        "model": ANSWER_GROUNDING_MODEL,
        "messages": [
            {"role": "system", "content": GROUNDING_CHECK_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": grounding_check_user_content(
                    question=user_query,
                    articles=_articles(citation_chunks),
                    draft=draft,
                ),
            },
        ],
        "temperature": 0.0,
        "response_format": grounding_check_response_format(),
        "metadata": _rewrite_call_metadata(kb_meta.get("org_id")),
    }
    headers = {
        "Authorization": f"Bearer {ANSWER_GROUNDING_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        resp = await _post_to_rewrite_model(
            payload, headers, {"timeout": ANSWER_GROUNDING_TIMEOUT}, ANSWER_GROUNDING_TIMEOUT
        )
        resp.raise_for_status()
        check = parse_grounding_check(resp.json()["choices"][0]["message"]["content"])
    except Exception as exc:
        logger.warning("kb_answer_grounding_failed error=%s", repr(exc)[:120])
        return None
    if check is None:
        logger.warning("kb_answer_grounding_unparseable org_id=%s", kb_meta.get("org_id"))
        return None
    logger.warning(
        "kb_answer_grounding org_id=%s user_id=%s request_id=%s statements=%s unsupported=%s "
        "contradicted=%s worth_repairing=%s sources=%s",
        kb_meta.get("org_id"),
        kb_meta.get("user_id"),
        kb_meta.get("request_id"),
        len(check.statements),
        len(check.unsupported),
        sum(1 for item in check.statements if item.support == "contradicted"),
        check.worth_repairing,
        len(citation_chunks),
    )
    return check


# The widget gives the check 4 s and the repair 3 s because a visitor waits for
# both. Internal answers are four times longer (median 1173 characters against a
# few hundred) and their check takes 3.6 s at the median, so the same budget
# would drop about half of them — measured on 2026-09-18, where the widget's
# budget lost 17 of 50. These are the same numbers with room for the length.
REPAIR_CHECK_TIMEOUT = 12.0
REPAIR_TIMEOUT = 8.0


async def _call(system_prompt: str, user_content: str, timeout: float, kb_meta: dict, fmt=None):
    payload = {
        "model": ANSWER_GROUNDING_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.0,
        "metadata": _rewrite_call_metadata(kb_meta.get("org_id")),
    }
    if fmt is not None:
        payload["response_format"] = fmt
    headers = {
        "Authorization": f"Bearer {ANSWER_GROUNDING_API_KEY}",
        "Content-Type": "application/json",
    }
    resp = await _post_to_rewrite_model(payload, headers, {"timeout": timeout}, timeout)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _log_check(check: GroundingCheck, citation_chunks: list[dict], kb_meta: dict[str, Any]) -> None:
    logger.warning(
        "kb_answer_grounding org_id=%s user_id=%s request_id=%s statements=%s unsupported=%s "
        "contradicted=%s worth_repairing=%s sources=%s",
        kb_meta.get("org_id"),
        kb_meta.get("user_id"),
        kb_meta.get("request_id"),
        len(check.statements),
        len(check.unsupported),
        sum(1 for item in check.statements if item.support == "contradicted"),
        check.worth_repairing,
        len(citation_chunks),
    )


async def repair_answer(
    *,
    user_query: str,
    draft: str,
    citation_chunks: list[dict],
    kb_meta: dict[str, Any],
) -> str | None:
    """The answer without the statements the articles do not carry, or ``None``.

    Same words and same threshold as the widget (shared in ``klai_chat_prompts``),
    because the two paths are compared against each other. Measured on fifty real
    answers from a customer's own tenant on 2026-09-18: 86% state something the
    articles do not carry against 64% on the widget, and 70% reach this threshold
    against 40%.

    ``None`` means "leave the answer alone": a failed call, an unchanged answer,
    or a check that stayed under the threshold. The internal path never refuses
    on this signal — an employee pasting correspondence is not a visitor reading
    a help page, and the widget's refusal was written for the second.
    """
    if not ANSWER_GROUNDING_API_KEY or not draft.strip():
        return None
    try:
        raw = await _call(
            GROUNDING_CHECK_SYSTEM_PROMPT,
            grounding_check_user_content(
                question=user_query, articles=_articles(citation_chunks), draft=draft
            ),
            REPAIR_CHECK_TIMEOUT,
            kb_meta,
            grounding_check_response_format(),
        )
    except Exception as exc:
        logger.warning("kb_answer_repair_check_failed error=%s", repr(exc)[:120])
        return None
    check = parse_grounding_check(raw)
    if check is None:
        logger.warning("kb_answer_grounding_unparseable org_id=%s", kb_meta.get("org_id"))
        return None
    # One check, always logged: an answer under the threshold is still a
    # measurement, and the comparison between the two paths is built on those
    # counts. Only reparations used to reach the log, which made the trend read
    # as if nothing else had been checked.
    _log_check(check, citation_chunks, kb_meta)
    if not check.worth_repairing:
        return None
    try:
        repaired = await _call(
            grounding_repair_system_prompt,
            grounding_repair_user_content(draft=draft, unsupported=check.unsupported),
            REPAIR_TIMEOUT,
            kb_meta,
        )
    except Exception as exc:
        logger.warning("kb_answer_repair_failed error=%s", repr(exc)[:120])
        return None
    repaired = (repaired or "").strip()
    # The sentinel means the model judged that nothing survives. On this path the
    # answer stays as it was: emptying an employee's answer is a bigger change
    # than the measurement supports, and the log carries the signal instead.
    if not repaired or repaired == GROUNDING_NOTHING_LEFT or repaired == draft.strip():
        logger.warning(
            "kb_answer_repair_kept org_id=%s request_id=%s unsupported=%s reason=%s",
            kb_meta.get("org_id"),
            kb_meta.get("request_id"),
            len(check.unsupported),
            "nothing_left" if repaired == GROUNDING_NOTHING_LEFT else "unchanged",
        )
        return None
    logger.warning(
        "kb_answer_repaired org_id=%s request_id=%s unsupported=%s contradicted=%s was=%d now=%d",
        kb_meta.get("org_id"),
        kb_meta.get("request_id"),
        len(check.unsupported),
        sum(1 for item in check.statements if item.support == "contradicted"),
        len(draft),
        len(repaired),
    )
    return repaired
