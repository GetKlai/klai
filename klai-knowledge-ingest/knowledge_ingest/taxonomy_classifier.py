"""
Taxonomy classifier -- multi-label classification + tag suggestion for documents.

Uses klai-fast with structured JSON output. One LLM call per document (not per chunk).
Returns (matched_nodes, suggested_tags):
  - matched_nodes: list of (node_id, confidence) tuples, sorted by confidence desc
  - suggested_tags: list of free-form tag strings
Threshold: confidence >= 0.5, max 5 nodes, max 5 tags.
30-second timeout; falls back to ([], []) on error without failing the ingest.

Rate limiting: uses the same _TokenBucketLimiter/_RateLimitedTransport from graph.py,
throttled to settings.graphiti_llm_rps (default 1 req/s) to avoid 429s on LiteLLM.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import structlog

from knowledge_ingest.config import settings
from knowledge_ingest.graph import _RateLimitedTransport, _TokenBucketLimiter

logger = structlog.get_logger()

# Module-level rate limiter shared across all classify_document calls
_llm_limiter: _TokenBucketLimiter | None = None


def _get_llm_limiter() -> _TokenBucketLimiter:
    global _llm_limiter
    if _llm_limiter is None:
        _llm_limiter = _TokenBucketLimiter(rate=settings.graphiti_llm_rps)
    return _llm_limiter


class TaxonomyNode:
    """Lightweight DTO for taxonomy nodes from the portal."""
    __slots__ = ("description", "id", "name")

    def __init__(self, id: int, name: str, description: str | None = None) -> None:
        self.id = id
        self.name = name
        self.description = description


_SYSTEM_PROMPT = (
    "You are a document taxonomy classifier. "
    "Given a document title, a content preview, and a list of taxonomy categories, "
    "return ALL matching categories (multi-label) and suggest free-form tags. "
    "Return nodes sorted by confidence descending. "
    "Only include nodes with confidence >= 0.5. Maximum 5 nodes and 5 tags. "
    "Return empty nodes list if no category matches with confidence >= 0.5. "
    "Tags should be lowercase, concise keywords describing the document content."
    '\n\nReply with ONLY a JSON object, no markdown, no explanation: '
    '{"nodes": [{"node_id": <int>, "confidence": <float 0-1>}], '
    '"tags": ["<string>"], "reasoning": "<string>"}'
)


async def classify_document(
    title: str,
    content_preview: str,
    taxonomy_nodes: list[TaxonomyNode],
) -> tuple[list[tuple[int, float]], list[str]]:
    """Classify a document into matching taxonomy nodes and suggest tags.

    Returns (matched_nodes, suggested_tags):
    - matched_nodes: list of (node_id, confidence) with confidence >= 0.5, max 5
    - suggested_tags: list of tag strings, max 5

    Returns ([], []) when:
    - taxonomy_nodes is empty (skips LLM call entirely)
    - LLM call fails or times out
    """
    if not taxonomy_nodes:
        return [], []

    categories = "\n".join(
        f"- id={node.id}: {node.name}" + (f" -- {node.description}" if node.description else "")
        for node in taxonomy_nodes
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
    except (TimeoutError, Exception) as exc:
        logger.warning(
            "taxonomy_classification_failed",
            title=title,
            error=str(exc),
        )
        return [], []

    valid_ids = {node.id for node in taxonomy_nodes}

    # Parse nodes
    raw_nodes = result.get("nodes", [])
    matched_nodes: list[tuple[int, float]] = []
    for entry in raw_nodes:
        if not isinstance(entry, dict):
            continue
        node_id = entry.get("node_id")
        confidence = float(entry.get("confidence", 0.0))
        if node_id is not None and node_id in valid_ids and confidence >= 0.5:
            matched_nodes.append((node_id, confidence))

    # Sort by confidence desc, limit to 5
    matched_nodes.sort(key=lambda x: x[1], reverse=True)
    matched_nodes = matched_nodes[:5]

    # Parse tags
    raw_tags = result.get("tags", [])
    suggested_tags: list[str] = []
    for tag in raw_tags:
        if isinstance(tag, str):
            cleaned = tag.strip().lower()
            if cleaned and cleaned not in suggested_tags:
                suggested_tags.append(cleaned)
    suggested_tags = suggested_tags[:5]

    return matched_nodes, suggested_tags


async def _call_litellm(user_message: str) -> dict:
    """Call LiteLLM proxy for taxonomy classification.

    Uses _RateLimitedTransport to throttle calls to graphiti_llm_rps (default 1/s).
    """
    transport = _RateLimitedTransport(
        wrapped=httpx.AsyncHTTPTransport(),
        limiter=_get_llm_limiter(),
    )
    async with httpx.AsyncClient(
        transport=transport,
        timeout=settings.taxonomy_classification_timeout,
    ) as client:
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
                "max_tokens": 300,
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
