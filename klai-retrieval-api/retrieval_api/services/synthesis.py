"""Synthesis service: stream an LLM answer grounded in retrieved chunks.

System-prompt language: SPEC-RAG-MULTILINGUAL-CHAT-001 moved the
grounded chat prompt to the shared library ``klai-chat-prompts`` so
both this service and ``klai-portal/backend/app/services/partner_chat``
load the same string. Do NOT inline the prompt here — a CI lint
asserts no service contains a hardcoded copy.

Observability: REQ-07 wires a passive `lingua`-based language detector
on both the user query and the model's response so VictoriaLogs gets
``language_correctness`` per ``chat_synthesis_complete`` event. The
detector is observability-only; it never alters synthesis behaviour.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator

import httpx
from klai_chat_prompts import GROUNDED_CHAT_SYSTEM_PROMPT

from retrieval_api.config import settings
from retrieval_api.util.language_detect import (
    detect_language,
    language_correctness,
)

logger = logging.getLogger(__name__)

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


def _emit_language_correctness_log(query: str, response_text: str) -> None:
    """Emit chat_synthesis_complete with passive language metrics.

    SPEC-RAG-MULTILINGUAL-CHAT-001 REQ-07. Failure-safe: any exception
    inside detection MUST NOT block synthesis from returning normally.
    """
    try:
        query_lang = detect_language(query)
        response_lang = detect_language(response_text)
        correct = language_correctness(query_lang, response_lang)
        logger.info(
            "chat_synthesis_complete",
            extra={
                "event": "chat_synthesis_complete",
                "query_language_detected": query_lang,
                "response_language_detected": response_lang,
                "language_correctness": correct,
                "response_length_chars": len(response_text or ""),
                "service": "retrieval-api",
            },
        )
    except Exception:
        logger.warning("chat_synthesis_language_log_failed", exc_info=True)


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
        {"role": "system", "content": GROUNDED_CHAT_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (f"Knowledge base chunks:\n{context}\n\nQuestion: {query_resolved}"),
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

    # Passive language-correctness telemetry (SPEC-RAG-MULTILINGUAL-CHAT-001 REQ-07).
    _emit_language_correctness_log(query_resolved, full_text)

    # Final event with citations
    indices = _extract_citation_indices(full_text)
    citations = _build_citations(indices, chunks)

    yield {
        "citations": citations,
        "retrieval_bypassed": False,
        "query_resolved": query_resolved,
    }
