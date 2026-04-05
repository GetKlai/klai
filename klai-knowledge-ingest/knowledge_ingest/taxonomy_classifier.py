"""
Taxonomy classifier — classifies a document into the best matching taxonomy node.

Uses klai-fast with structured JSON output. One LLM call per document (not per chunk).
Returns (node_id, confidence): node_id=None when confidence < 0.5 or no nodes exist.
5-second timeout; falls back to (None, 0.0) on error without failing the ingest.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import structlog

from knowledge_ingest.config import settings

logger = structlog.get_logger()


class TaxonomyNode:
    """Lightweight DTO for taxonomy nodes from the portal."""
    __slots__ = ("id", "name")

    def __init__(self, id: int, name: str) -> None:
        self.id = id
        self.name = name


_SYSTEM_PROMPT = (
    "You are a document taxonomy classifier. "
    "Given a document title, a content preview, and a list of taxonomy categories, "
    "return the best matching category. "
    "Respond with JSON only: {\"node_id\": <int or null>, \"confidence\": <float 0-1>, \"reasoning\": <string>}. "
    "Use null for node_id if no category matches with confidence >= 0.5."
)


async def classify_document(
    title: str,
    content_preview: str,
    taxonomy_nodes: list[TaxonomyNode],
) -> tuple[int | None, float]:
    """Classify a document into the best matching taxonomy node.

    Returns (node_id, confidence). node_id=None when:
    - confidence < 0.5
    - taxonomy_nodes is empty
    - LLM call fails or times out
    """
    if not taxonomy_nodes:
        return None, 0.0

    categories = "\n".join(
        f"- id={node.id}: {node.name}" for node in taxonomy_nodes
    )
    user_message = (
        f"Document title: {title}\n"
        f"Content preview: {content_preview[:500]}\n\n"
        f"Available taxonomy categories:\n{categories}"
    )

    try:
        result = await asyncio.wait_for(
            _call_litellm(user_message),
            timeout=settings.taxonomy_classification_timeout,
        )
    except (asyncio.TimeoutError, Exception) as exc:
        logger.warning(
            "taxonomy_classification_failed",
            title=title,
            error=str(exc),
        )
        return None, 0.0

    node_id = result.get("node_id")
    confidence = float(result.get("confidence", 0.0))

    # Validate that node_id is actually in our taxonomy
    if node_id is not None:
        valid_ids = {node.id for node in taxonomy_nodes}
        if node_id not in valid_ids:
            logger.warning(
                "taxonomy_invalid_node_id",
                title=title,
                returned_id=node_id,
            )
            return None, 0.0

    if confidence < 0.5:
        return None, confidence

    return node_id, confidence


async def _call_litellm(user_message: str) -> dict:
    """Call LiteLLM proxy for taxonomy classification."""
    async with httpx.AsyncClient(timeout=settings.taxonomy_classification_timeout) as client:
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
                "temperature": 0.0,
                "max_tokens": 200,
                "response_format": {"type": "json_object"},
            },
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)
