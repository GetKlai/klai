"""
Generate short descriptions for taxonomy nodes using klai-fast.

Max 200 chars, 5-second timeout, empty string fallback on error.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import structlog

from knowledge_ingest.config import settings

logger = structlog.get_logger()

_SYSTEM_PROMPT = (
    "You are a taxonomy description writer. "
    "Given a category name, its parent category, and sample document titles, "
    "write a concise description (max 200 characters) of what questions and content "
    "belong in this category. "
    "Write in the same language as the document titles."
    "\n\nReply with ONLY a JSON object, no markdown, no explanation: "
    '{"description": "<string>"}'
)


async def generate_node_description(
    node_name: str,
    parent_name: str | None,
    sample_titles: list[str],
) -> str:
    """Generate a short description for a taxonomy node.

    Returns empty string on error or timeout.
    """
    context_parts = [f"Category: {node_name}"]
    if parent_name:
        context_parts.append(f"Parent category: {parent_name}")
    if sample_titles:
        titles_str = ", ".join(sample_titles[:10])
        context_parts.append(f"Sample documents: {titles_str}")

    user_message = "\n".join(context_parts)

    try:
        result = await asyncio.wait_for(
            _call_litellm(user_message),
            timeout=5.0,
        )
    except (TimeoutError, Exception) as exc:
        logger.warning(
            "description_generation_failed",
            node_name=node_name,
            error=str(exc),
        )
        return ""

    description = result.get("description", "")
    if not isinstance(description, str):
        return ""
    return description[:200]


async def _call_litellm(user_message: str) -> dict:
    """Call LiteLLM proxy for description generation."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.post(
            f"{settings.litellm_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.litellm_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.taxonomy_classification_model,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                "temperature": 0.3,
                "max_tokens": 100,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        content = (content or "").strip()
        # Strip markdown code fences if present
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return json.loads(content)
