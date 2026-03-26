"""Coreference resolution: rewrite a follow-up query into a standalone query."""

from __future__ import annotations

import asyncio
import logging

import httpx

from retrieval_api.config import settings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a coreference resolver. Given a conversation history and the latest "
    "user query, rewrite the query so it is fully standalone -- all pronouns and "
    "references resolved. Return ONLY the rewritten query, nothing else. "
    "Keep the same language as the input query. If no rewriting is needed, return "
    "the original query unchanged."
)


async def resolve(query: str, history: list[dict]) -> str:
    """Return a standalone version of *query* given prior *history*.

    If history is empty, or the LLM call times out / fails, the original query
    is returned unchanged.
    """
    if not history:
        return query

    # Take only last 3 turns to keep context small
    recent = history[-3:]

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        *recent,
        {"role": "user", "content": query},
    ]
    body = {
        "model": "klai-fast",
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
        if resolved:
            return resolved
        return query
    except asyncio.TimeoutError:
        logger.warning("Coreference resolution timed out, using original query")
        return query
    except Exception as exc:
        logger.warning("Coreference resolution failed: %s", exc)
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
