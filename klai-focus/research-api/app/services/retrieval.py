"""
Retrieval pipeline for chat endpoint.
Supports narrow, broad, and web modes.
"""
import asyncio
import json
import logging
from typing import AsyncIterator

import httpx

from app.core.config import settings
from app.services import docling, tei

logger = logging.getLogger(__name__)

_TOP_K = 8
_MAX_CONTEXT_TOKENS = 6000
_WEB_TOP_K = 3
_WEB_RESULTS = 5
_WEB_URL_TIMEOUT = 15.0  # seconds per URL fetch

NARROW_SYSTEM_PROMPT = (
    "[CRITICAL] Respond in the language of the user's question. "
    "Als de gebruiker Nederlands schrijft, antwoord je in het Nederlands. "
    "If the user writes English, respond in English. Never switch mid-conversation.\n\n"
    "You are Klai AI, a research assistant. You answer using only the documents provided.\n\n"
    "## How to answer\n"
    "Start with the answer. No preamble. No filler.\n"
    "Simple question: 1-3 sentences. Complex: core answer first, then detail.\n"
    "If sources say something you didn't expect, include it anyway.\n\n"
    "## How to cite\n"
    "Cite every factual claim: [document name] or [document name, p.X]. "
    "If sources contradict each other, show both — don't choose.\n\n"
    "## When the answer isn't there\n"
    "Say it plainly: 'That's not in the selected documents.' "
    "No guessing. No general knowledge as filler."
)

BROAD_SYSTEM_PROMPT = (
    "[CRITICAL] Respond in the language of the user's question. "
    "Als de gebruiker Nederlands schrijft, antwoord je in het Nederlands. "
    "If the user writes English, respond in English. Never switch mid-conversation.\n\n"
    "You are Klai AI, a research assistant. You have access to two source types: "
    "Focus documents (specific to this notebook) and the Knowledge base (organisational knowledge). "
    "Where sources fall short, you may supplement with general knowledge.\n\n"
    "## How to answer\n"
    "Start with the answer. No preamble. No filler.\n"
    "Simple question: 1-3 sentences. Complex: core answer first, then detail.\n\n"
    "## How to cite\n"
    "Always be clear where the answer comes from: [Focus doc], [KB], or [General knowledge]. "
    "Cite document name and page where available. "
    "If sources contradict each other, show both — don't choose.\n\n"
    "## When you're not sure\n"
    "Say so explicitly: 'The sources don't fully cover this, but based on general knowledge...'"
)

BROAD_FOCUS_ONLY_SYSTEM_PROMPT = (
    "[CRITICAL] Respond in the language of the user's question. "
    "Als de gebruiker Nederlands schrijft, antwoord je in het Nederlands. "
    "If the user writes English, respond in English. Never switch mid-conversation.\n\n"
    "You are Klai AI, a research assistant. You have access to Focus documents specific to this notebook. "
    "Where documents fall short, you may supplement with general knowledge.\n\n"
    "## How to answer\n"
    "Start with the answer. No preamble. No filler.\n"
    "Simple question: 1-3 sentences. Complex: core answer first, then detail.\n\n"
    "## How to cite\n"
    "Always be clear where the answer comes from: [Focus doc] or [General knowledge]. "
    "Cite document name and page where available. "
    "If sources contradict each other, show both — don't choose."
)

WEB_SYSTEM_PROMPT = (
    "[CRITICAL] Respond in the language of the user's question. "
    "Als de gebruiker Nederlands schrijft, antwoord je in het Nederlands. "
    "If the user writes English, respond in English. Never switch mid-conversation.\n\n"
    "You are Klai AI, a research assistant. You have access to two source types: "
    "Focus documents (specific to this notebook) and live web results retrieved for this question. "
    "Where sources fall short, you may supplement with general knowledge.\n\n"
    "## How to answer\n"
    "Start with the answer. No preamble. No filler.\n"
    "Simple question: 1-3 sentences. Complex: core answer first, then detail.\n\n"
    "## How to cite\n"
    "Always be clear where the answer comes from: [Focus doc], [Web: URL], or [General knowledge]. "
    "Include the full URL for web sources. "
    "If sources contradict each other, show both — don't choose."
)


async def _fetch_web_url(url: str) -> tuple[str, str] | None:
    """Fetch a single URL via docling with a per-URL timeout. Returns (url, text) or None."""
    try:
        doc = await asyncio.wait_for(docling.convert_url(url), timeout=_WEB_URL_TIMEOUT)
        if doc.text.strip():
            return url, doc.text
        logger.warning("Empty text for web URL: %s", url)
        return None
    except asyncio.TimeoutError:
        logger.warning("Timeout fetching web URL: %s", url)
        return None
    except Exception:
        logger.warning("Failed to fetch web URL: %s", url)
        return None


async def retrieve_web_chunks(question: str) -> list[dict]:
    """
    Query SearXNG, fetch URLs via docling in parallel, embed and return top-k web chunks.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{settings.searxng_url}/search",
                params={"q": question, "format": "json", "language": "nl-NL"},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        logger.exception("SearXNG query failed")
        return []

    results = data.get("results", [])[:_WEB_RESULTS]
    urls = [r.get("url", "") for r in results if r.get("url")]

    fetch_results = await asyncio.gather(*[_fetch_web_url(url) for url in urls])
    web_texts: list[tuple[str, str]] = [r for r in fetch_results if r is not None]

    if not web_texts:
        return []

    # Embed all web texts and find closest to question
    query_embedding = await tei.embed_single(question)
    all_text_chunks: list[dict] = []

    for url, text in web_texts:
        all_text_chunks.append({
            "source_id": "web",
            "origin": "web",
            "content": text[:3000],
            "url": url,
            "score": 0.0,
        })

    # Re-rank by embedding similarity
    embeddings = await tei.embed_texts([c["content"] for c in all_text_chunks])
    for chunk, emb in zip(all_text_chunks, embeddings):
        score = _cosine_similarity(query_embedding, emb)
        chunk["score"] = score

    all_text_chunks.sort(key=lambda x: x["score"], reverse=True)
    return all_text_chunks[:_WEB_TOP_K]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def build_context(chunks: list[dict], max_tokens: int = _MAX_CONTEXT_TOKENS) -> str:
    """Concatenate chunk contents with source attribution, trimming to fit token budget."""
    import tiktoken
    enc = tiktoken.get_encoding("cl100k_base")

    parts: list[str] = []
    used_tokens = 0

    for chunk in chunks:
        origin = chunk.get("origin", "focus")
        source_name = chunk.get("source_name") or chunk.get("url", "web")
        page = chunk.get("metadata", {}).get("page")

        if origin == "kb":
            citation = f"[KB: {source_name}]"
        elif origin == "web":
            citation = f"[Web: {source_name}]"
        else:
            citation = f"[Bron: {source_name}{f', p.{page}' if page else ''}]"

        part = f"{citation}\n{chunk['content']}\n"
        part_tokens = len(enc.encode(part))
        if used_tokens + part_tokens > max_tokens:
            break
        parts.append(part)
        used_tokens += part_tokens

    return "\n".join(parts)


async def stream_llm(
    system_prompt: str,
    context: str,
    question: str,
    history: list[dict],
) -> AsyncIterator[str]:
    """
    Call LiteLLM with SSE streaming. Yields raw token strings.
    """
    messages: list[dict] = [{"role": "system", "content": system_prompt}]

    if context:
        messages.append({
            "role": "user",
            "content": f"Bronnen:\n\n{context}",
        })
        messages.append({
            "role": "assistant",
            "content": "Ik heb de bronnen gelezen. Stel je vraag.",
        })

    for turn in history[-4:]:
        messages.append({"role": turn["role"], "content": turn["content"]})

    messages.append({"role": "user", "content": question})

    headers = {"Content-Type": "application/json"}
    if settings.litellm_api_key:
        headers["Authorization"] = f"Bearer {settings.litellm_api_key}"

    body = {
        "model": settings.synthesis_model,
        "messages": messages,
        "stream": True,
        "temperature": 0.3,
    }

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
                payload = line[6:]
                if payload == "[DONE]":
                    break
                try:
                    chunk_data = json.loads(payload)
                    delta = chunk_data["choices"][0]["delta"].get("content", "")
                    if delta:
                        yield delta
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue


def extract_citations(chunks: list[dict]) -> list[dict]:
    """Build citation objects from retrieved document chunks."""
    seen: set[str] = set()
    citations: list[dict] = []
    for chunk in chunks:
        key = f"{chunk['source_id']}:{chunk.get('metadata', {}).get('page', '')}"
        if key in seen:
            continue
        seen.add(key)
        if chunk.get("source_id") == "web":
            citations.append({
                "source_id": "web",
                "url": chunk.get("url", ""),
                "excerpt": chunk["content"][:200],
            })
            continue
        meta = chunk.get("metadata", {})
        source_ref = meta.get("source_ref")
        source_connector_id = meta.get("source_connector_id")
        # Build a clickable URL when source_ref is a Notion page UUID
        # Notion UUIDs are 32-char hex with dashes; connector_id presence confirms KB connector origin
        url = None
        if source_ref and source_connector_id:
            url = f"https://notion.so/{source_ref.replace('-', '')}"
        citations.append({
            "source_id": chunk["source_id"],
            "source_name": chunk.get("source_name", ""),
            "page": meta.get("page"),
            "url": url,
            "excerpt": chunk["content"][:200],
        })
    return citations
