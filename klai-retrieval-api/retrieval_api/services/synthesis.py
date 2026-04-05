"""Synthesis service: stream an LLM answer grounded in retrieved chunks."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator

import httpx

from retrieval_api.config import settings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "[CRITICAL] Respond in the language of the user's question. "
    "Als de gebruiker Nederlands schrijft, antwoord je in het Nederlands. "
    "If the user writes English, respond in English. Never switch mid-conversation.\n\n"
    "You are Klai AI, a knowledge assistant. You answer questions based on the knowledge base chunks provided.\n\n"
    "## How to answer\n"
    "Start with the answer. No warm-up, no rephrasing the question, no 'great question!'\n"
    "Simple question: 1-3 sentences. Complex question: the core answer first, then the detail.\n"
    "Be direct. Be honest. If the sources say something unexpected, say it.\n\n"
    "## How to cite\n"
    "Every factual claim gets a [n] citation where n is the chunk number. "
    "If a chunk includes a URL or help page link, include it: [n] (https://...). "
    "If sources contradict each other, say so — don't pick a side silently.\n\n"
    "## When the answer isn't there\n"
    "Say it plainly: 'That's not in the knowledge base.' "
    "Don't guess. Don't fill the gap with general knowledge. "
    "If you're partially sure, say that too: 'The knowledge base touches on this, but doesn't fully answer it.'"
)

# Approximate char budget for context (6000 tokens * ~4 chars/token)
_MAX_CONTEXT_CHARS = 24_000


def _build_context(chunks: list[dict]) -> str:
    """Format chunks as numbered context for the LLM."""
    parts: list[str] = []
    total_chars = 0
    for i, chunk in enumerate(chunks, 1):
        prefix = chunk.get("context_prefix", "") or ""
        text = chunk.get("text", "")
        entry = f"[{i}] {prefix}{text}".strip()
        if total_chars + len(entry) > _MAX_CONTEXT_CHARS:
            break
        parts.append(entry)
        total_chars += len(entry)
    return "\n\n".join(parts)


def _extract_citation_indices(text: str) -> list[int]:
    """Extract all [n] citation indices from the generated text."""
    return sorted(set(int(m) for m in re.findall(r"\[(\d+)\]", text)))


def _build_citations(indices: list[int], chunks: list[dict]) -> list[dict]:
    """Build citation objects from the referenced chunk indices."""
    citations: list[dict] = []
    for idx in indices:
        # indices are 1-based in the text
        chunk_idx = idx - 1
        if 0 <= chunk_idx < len(chunks):
            chunk = chunks[chunk_idx]
            citations.append(
                {
                    "index": idx,
                    "artifact_id": chunk.get("artifact_id"),
                    "title": (chunk.get("context_prefix") or chunk.get("text", ""))[:80],
                    "chunk_ids": [chunk.get("chunk_id", "")],
                    "relevance_score": chunk.get("reranker_score") or chunk.get("score", 0),
                }
            )
    return citations


async def synthesize(
    query_resolved: str,
    chunks: list[dict],
    history: list[dict],
) -> AsyncIterator[str | dict]:
    """Stream synthesis tokens, then yield a final dict with citations.

    Yields:
        str: individual token strings
        dict: final event ``{"citations": [...], "retrieval_bypassed": False}``
    """
    context = _build_context(chunks)

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Knowledge base chunks:\n{context}\n\n"
                f"Question: {query_resolved}"
            ),
        },
    ]

    # Include recent history if available
    if history:
        recent = history[-3:]
        messages = [messages[0], *recent, messages[-1]]

    body = {
        "model": settings.synthesis_model,
        "messages": messages,
        "stream": True,
        "temperature": 0.3,
    }
    headers = {}
    if settings.litellm_api_key:
        headers["Authorization"] = f"Bearer {settings.litellm_api_key}"

    full_text = ""

    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream(
            "POST",
            f"{settings.litellm_url}/v1/chat/completions",
            headers=headers,
            json=body,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk_data = json.loads(payload)
                    delta = chunk_data["choices"][0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        full_text += content
                        yield content
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue

    # Final event with citations
    indices = _extract_citation_indices(full_text)
    citations = _build_citations(indices, chunks)

    yield {
        "citations": citations,
        "retrieval_bypassed": False,
        "query_resolved": query_resolved,
    }
