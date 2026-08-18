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

logger = structlog.get_logger(__name__)

_SYSTEM_PROMPT = (
    "You are a coreference resolver. Given a conversation history and the latest "
    "user query, rewrite the query so it is fully standalone -- all pronouns and "
    "references resolved. Return ONLY the rewritten query, nothing else. "
    "Keep the same language as the input query. If no rewriting is needed, return "
    "the original query unchanged. The rewrite MUST keep the subject of the "
    "latest query: history may only supply referents for pronouns, ellipsis, or "
    "follow-up phrases -- never replace the query's topic with a topic from "
    "history. When the latest query introduces a new topic, return it unchanged."
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

    # Take only last 3 turns to keep context small
    recent = history[-3:]

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        *recent,
        {"role": "user", "content": query},
    ]
    body = {
        "model": settings.coreference_model,
        "messages": messages,
        "stream": False,
        "temperature": 0.0,
    }
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
