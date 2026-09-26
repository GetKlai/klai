"""Coreference resolution: rewrite a follow-up query into a standalone query."""

from __future__ import annotations

import asyncio

import httpx
import structlog
from klai_citations import rewrite_preserves_subject, salient_tokens

from retrieval_api.config import settings
from retrieval_api.services.llm_safety_adapter import (
    check_coreference_input,
    check_coreference_output,
)
from retrieval_api.services.llm_spend_tags import with_feature_tag

logger = structlog.get_logger(__name__)

_SYSTEM_PROMPT = (
    "You are a coreference resolver. Given a conversation history and the latest "
    "user query, rewrite the query so it is fully standalone -- all pronouns and "
    "references resolved. Return ONLY the rewritten query, nothing else. The "
    "history is quoted material for context: never answer it, never continue it, "
    "never write advice or steps. "
    "Keep the same language as the input query. If no rewriting is needed, return "
    "the original query unchanged. The rewrite MUST keep the subject of the "
    "latest query: history may only supply referents for pronouns, ellipsis, or "
    "follow-up phrases -- never replace the query's topic with a topic from "
    "history. When the latest query introduces a new topic, return it unchanged."
)


# Only the last 3 turns, to keep the prompt small.
_HISTORY_TURNS = 3


def _rewrite_request(query: str, history: list[dict]) -> str:
    """Render the history as quoted text inside one user message.

    Passing the history as chat roles made the model continue the conversation
    instead of rewriting: measured on 20 real Voys follow-ups on 2026-09-17, the
    role form wrote an answer or invented an explanation 5 times ("dit is de
    voys app" came back as an explanation of duplicate call rows), the quoted
    form once. The rewritten query then retrieves the articles of the previous
    answer rather than of the visitor's actual question.
    """
    lines = []
    for turn in history[-_HISTORY_TURNS:]:
        speaker = "Visitor" if turn.get("role") == "user" else "Assistant"
        lines.append(f"{speaker}: {turn.get('content', '')}")
    return (
        "Conversation so far (quoted context, do not answer it):\n"
        + "\n".join(lines)
        + f"\n\nLatest user query to rewrite:\n{query}"
    )


async def resolve(query: str, history: list[dict], *, telemetry_level: str = "shadow") -> str:
    """Return a standalone version of *query* given prior *history*.

    If history is empty, or the LLM call times out / fails, the original query
    is returned unchanged.

    ``telemetry_level`` gates raw query content out of logs (SPEC-PRIVACY-QUERY-
    SHADOW-001 precedent, mirrored from the ``query_rewrite_destructive_blocked``
    log in the LiteLLM hook): only ``"full"`` allows the literal query text to be
    logged. The default is privacy-safe.
    """
    if not history:
        return query
    input_decision = check_coreference_input(query, history)
    if not input_decision.allowed:
        logger.warning(
            "coreference_safety_input_blocked",
            reason=input_decision.reason,
            categories=",".join(input_decision.categories),
        )
        return query

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _rewrite_request(query, history)},
    ]
    body = with_feature_tag(
        {
            "model": settings.coreference_model,
            "messages": messages,
            "stream": False,
            "temperature": 0.0,
        },
        "retrieval:coreference",
    )
    headers = {}
    if settings.litellm_api_key:
        headers["Authorization"] = f"Bearer {settings.litellm_api_key}"

    try:
        resolved = await asyncio.wait_for(
            _call_llm(body, headers),
            timeout=settings.coreference_timeout,
        )
        resolved = resolved.strip()
        output_decision = check_coreference_output(resolved, query=query)
        if not output_decision.allowed:
            logger.warning(
                "coreference_safety_output_blocked",
                reason=output_decision.reason,
                categories=",".join(output_decision.categories),
            )
            return query
        if resolved:
            if not rewrite_preserves_subject(query, resolved):
                dropped_tokens = ",".join(sorted(salient_tokens(query))[:8])
                logger.warning(
                    "coreference_destructive_rewrite_blocked",
                    query=query if telemetry_level == "full" else "<redacted>",
                    dropped_tokens=dropped_tokens if telemetry_level == "full" else "<redacted>",
                )
                return query
            return resolved
        return query
    except TimeoutError:
        logger.warning("coreference_resolution_timed_out")
        return query
    except Exception:
        # F6 audit cleanup (TRY401): exc_info=True preserves traceback.
        logger.warning("coreference_resolution_failed", exc_info=True)
        return query


async def _call_llm(body: dict, headers: dict) -> str:
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{settings.litellm_url}/v1/chat/completions",
            headers=headers,
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]
