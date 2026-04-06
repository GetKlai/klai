"""
Blind content labeler -- generates free-form keywords describing a document
BEFORE any taxonomy context is introduced.

This is a deliberate separation of concerns (SPEC-KB-023):
- content_label: what IS this document (blind, no taxonomy bias)
- taxonomy_node_ids: which categories does it match (anchored to existing taxonomy)

The blind label is the raw material for clustering in SPEC-KB-024.

Rate limiting: shares the module-level _TokenBucketLimiter from taxonomy_classifier
(same 1 req/s default) so label generation and taxonomy classification never race.
Since labeling runs first and classification runs second, the two calls are
naturally sequential with the shared limiter enforcing the upstream rate limit.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import structlog

from knowledge_ingest.config import settings
from knowledge_ingest.graph import _RateLimitedTransport
from knowledge_ingest.taxonomy_classifier import _get_llm_limiter

logger = structlog.get_logger()

_SYSTEM_PROMPT = (
    "You are a document keyword extractor. "
    "Given a document title and content preview, return 3-5 lowercase keywords "
    "that describe what this document is about. "
    'Return JSON only: {"keywords": ["keyword1", "keyword2"]}. '
    "Do NOT use category names or organisational terms — "
    "use only descriptive content keywords."
)


# @MX:NOTE: [AUTO] Blind labeling — runs BEFORE taxonomy fetch to prevent confirmation bias.
# @MX:SPEC: SPEC-KB-023 R1, R5
# @MX:NOTE: Total LLM budget per document is 2 calls: this (blind) + classify_document (anchored).
async def generate_content_label(
    title: str,
    content_preview: str,
) -> list[str]:
    """Generate a blind content label for a document.

    Returns 3-5 lowercase keywords describing the document content.
    The label is generated WITHOUT any taxonomy context to avoid confirmation bias.

    Returns [] when:
    - LLM call times out (15s)
    - LLM call fails for any reason
    Ingest continues normally in both cases (non-fatal).
    """
    user_message = f"Document title: {title}\nContent preview: {content_preview[:500]}"

    try:
        result = await asyncio.wait_for(
            _call_litellm(user_message),
            timeout=settings.content_label_timeout,
        )
    except Exception as exc:
        logger.warning(
            "content_label_generation_failed",
            title=title,
            error=str(exc),
        )
        return []

    raw_keywords = result.get("keywords", [])
    keywords: list[str] = []
    for kw in raw_keywords:
        if isinstance(kw, str):
            cleaned = kw.strip().lower()
            if cleaned and cleaned not in keywords:
                keywords.append(cleaned)
    # Clamp to 5 in case the LLM returns more than asked
    return keywords[:5]


async def _call_litellm(user_message: str) -> dict:
    """Call LiteLLM proxy for blind content label generation.

    Uses _RateLimitedTransport with the shared taxonomy limiter (graphiti_llm_rps).
    """
    transport = _RateLimitedTransport(
        wrapped=httpx.AsyncHTTPTransport(),
        limiter=_get_llm_limiter(),
    )
    async with httpx.AsyncClient(
        transport=transport,
        timeout=settings.content_label_timeout,
    ) as client:
        resp = await client.post(
            f"{settings.litellm_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.litellm_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "klai-fast",
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                "temperature": 0.0,
                "max_tokens": 100,
                "response_format": {"type": "json_object"},
            },
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)
