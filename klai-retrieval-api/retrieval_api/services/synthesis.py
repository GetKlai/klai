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
from klai_citations import normalise_source_url, render_evidence_context, source_url_key

from retrieval_api.config import settings
from retrieval_api.models import EvidencePack
from retrieval_api.services.evidence_pack import (
    build_evidence_pack,
    evidence_pack_items_as_chunks,
    evidence_pack_sources_payload,
)
from retrieval_api.util.language_detect import (
    detect_language,
    language_correctness,
)

logger = logging.getLogger(__name__)

# Approximate char budget for context (6000 tokens * ~4 chars/token)
_MAX_CONTEXT_CHARS = 24_000


def _normalise_source_url(url: object) -> str:
    return normalise_source_url(url)


def _source_url_key(url: str) -> str:
    return source_url_key(url)


def _chunk_source_url(chunk: dict) -> str:
    candidates: list[object] = [
        chunk.get("source_url"),
        chunk.get("sourceUrl"),
        chunk.get("canonical_url"),
        chunk.get("page_url"),
        chunk.get("url"),
    ]
    metadata = chunk.get("metadata")
    if isinstance(metadata, dict):
        candidates.extend(
            [
                metadata.get("source_url"),
                metadata.get("sourceUrl"),
                metadata.get("canonical_url"),
                metadata.get("page_url"),
                metadata.get("url"),
            ]
        )
    source = chunk.get("source")
    if isinstance(source, dict):
        candidates.extend([source.get("source_url"), source.get("url"), source.get("href")])

    for candidate in candidates:
        normalised = _normalise_source_url(candidate)
        if normalised:
            return normalised
    return ""


def _chunk_title(chunk: dict) -> str:
    metadata = chunk.get("metadata")
    title = chunk.get("title")
    if not title and isinstance(metadata, dict):
        title = metadata.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return (chunk.get("context_prefix") or chunk.get("text", ""))[:80]


def _build_context(chunks: list[dict], evidence_pack: EvidencePack | None = None) -> str:
    """Format retrieved chunks as structured evidence for the LLM."""
    context_chunks = evidence_pack_items_as_chunks(evidence_pack) if evidence_pack else chunks
    return render_evidence_context(
        context_chunks,
        include_source_urls=True,
        max_chars=_MAX_CONTEXT_CHARS,
    )


def _extract_citation_indices(text: str) -> list[int]:
    """Extract all [n] citation indices from the generated text."""
    return sorted(set(int(m) for m in re.findall(r"\[(\d+)\]", text)))


def _build_citations(indices: list[int], chunks: list[dict]) -> list[dict]:
    """Build document-level citation objects from referenced chunk indices."""
    citations: list[dict] = []
    by_source_key: dict[str, dict] = {}
    for idx in indices:
        # indices are 1-based in the text
        chunk_idx = idx - 1
        if 0 <= chunk_idx < len(chunks):
            chunk = chunks[chunk_idx]
            source_url = _chunk_source_url(chunk)
            source_key = _source_url_key(source_url) if source_url else f"chunk:{idx}"
            relevance_score = chunk.get("reranker_score") or chunk.get("score", 0)

            citation = by_source_key.get(source_key)
            if citation is None:
                citation = {
                    "index": idx,
                    "indices": [idx],
                    "artifact_id": chunk.get("artifact_id"),
                    "title": _chunk_title(chunk),
                    "source_url": source_url or None,
                    "chunk_ids": [chunk.get("chunk_id", "")],
                    "relevance_score": relevance_score,
                }
                by_source_key[source_key] = citation
                citations.append(citation)
                continue

            citation["indices"].append(idx)
            citation["chunk_ids"].append(chunk.get("chunk_id", ""))
            citation["relevance_score"] = max(citation["relevance_score"], relevance_score)
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
    evidence_pack: EvidencePack | None = None,
) -> AsyncIterator[str | dict]:
    """Stream synthesis tokens, then yield a final dict with citations.

    Yields:
        str: individual token strings
        dict: final event ``{"citations": [...], "retrieval_bypassed": False}``
    """
    if evidence_pack is None:
        evidence_pack = build_evidence_pack(
            chunks,
            min_relevance_score=settings.confidence_band_low_threshold
            if settings.reranker_enabled
            else None,
        )
    if not evidence_pack.sources:
        message = "I cannot answer this reliably from the available knowledge sources."
        yield message
        yield {
            "citations": [],
            "retrieval_bypassed": False,
            "query_resolved": query_resolved,
            "evidence_pack": evidence_pack.model_dump(),
        }
        return
    context = _build_context(chunks, evidence_pack)

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

    yield {
        "citations": evidence_pack_sources_payload(evidence_pack),
        "retrieval_bypassed": False,
        "query_resolved": query_resolved,
        "evidence_pack": evidence_pack.model_dump(),
    }
