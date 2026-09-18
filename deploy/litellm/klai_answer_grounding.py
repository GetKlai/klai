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
    GroundingCheck,
    grounding_check_response_format,
    grounding_check_user_content,
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
