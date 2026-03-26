"""
Retrieval pipeline for chat endpoint.
Supports narrow, broad, and web modes.
"""
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

NARROW_SYSTEM_PROMPT = """You are a research assistant. Answer the user's question using only the provided source excerpts below.
If the answer is not found in the provided sources, respond with:
"Ik kan dit niet vinden in de geselecteerde documenten."
Do not use any knowledge beyond what is explicitly present in the sources.
Always cite which source and page your answer is based on."""

BROAD_SYSTEM_PROMPT = """You are a research assistant. Use the provided source excerpts as your primary reference.
You have access to two types of sources:
- Focus documents: uploaded documents specific to this notebook
- Knowledge base: organizational knowledge from the Klai Knowledge system
Supplement with your general knowledge where helpful.
Always indicate which parts of your answer come from Focus documents, the Knowledge base,
or your general knowledge."""

BROAD_FOCUS_ONLY_SYSTEM_PROMPT = """You are a research assistant. Use the provided source excerpts as your primary reference.
You have access to Focus documents: uploaded documents specific to this notebook.
Supplement with your general knowledge where helpful.
Always indicate which parts of your answer come from the Focus documents or your general knowledge."""


async def retrieve_web_chunks(question: str) -> list[dict]:
    """
    Query SearXNG, fetch URLs via docling, embed and return top-k web chunks.
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
    web_texts: list[tuple[str, str]] = []  # (url, text)

    for result in results:
        url = result.get("url", "")
        if not url:
            continue
        try:
            doc = await docling.convert_url(url)
            if doc.text.strip():
                web_texts.append((url, doc.text))
        except Exception:
            logger.warning("Failed to fetch web URL: %s", url)
            continue

    if not web_texts:
        return []

    # Embed all web texts and find closest to question
    query_embedding = await tei.embed_single(question)
    all_text_chunks: list[dict] = []

    for url, text in web_texts:
        # Simple single-chunk representation per URL for web results
        all_text_chunks.append({"source_id": "web", "content": text[:3000], "url": url, "score": 0.0})

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
        "model": "klai-primary",
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
            continue
        citations.append({
            "source_id": chunk["source_id"],
            "source_name": chunk.get("source_name", ""),
            "page": chunk.get("metadata", {}).get("page"),
            "excerpt": chunk["content"][:200],
        })
    return citations
