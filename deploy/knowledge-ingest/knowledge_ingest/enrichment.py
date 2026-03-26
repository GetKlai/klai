"""
LLM enrichment service: contextual prefix generation + HyPE question generation.

Each chunk gets a single LLM call (via LiteLLM proxy) returning:
  {"context_prefix": "...", "questions": ["...", ...]}

Enriched chunk text = "{context_prefix}\n\n{original_text}"
Questions are used for vector_questions (depth 0-1 only) and stored in payload.
"""
import asyncio
import logging
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, ValidationError

from knowledge_ingest.config import settings

logger = logging.getLogger(__name__)

ENRICHMENT_PROMPT = """\
Documenttitel: {title}
Pad: {path}

<document>
{document_text}
</document>

<chunk>
{chunk_text}
</chunk>

Genereer een JSON-object met:
- "context_prefix": een zin van 1-2 regels die deze chunk plaatst binnen het document \
(welk document, welk onderwerp, welke sectie).
- "questions": 3-5 vragen die deze chunk beantwoordt. De vragen moeten natuurlijke \
zoekopdrachten zijn die een gebruiker zou typen.

Antwoord ALLEEN met geldig JSON."""


class EnrichmentResult(BaseModel):
    context_prefix: str
    questions: list[str]


@dataclass
class EnrichedChunk:
    original_text: str
    enriched_text: str       # "{context_prefix}\n\n{original_text}"
    context_prefix: str
    questions: list[str]     # embedded as vector_questions for depth 0-1; stored in payload for all


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Rough truncation: 1 token ≈ 4 chars for Dutch/English mixed text."""
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def _strip_frontmatter(text: str) -> str:
    """Remove YAML frontmatter before passing document context to LLM."""
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    return text[end + 4:].lstrip()


async def enrich_chunk(
    document_text: str,
    chunk_text: str,
    title: str,
    path: str,
) -> EnrichmentResult | None:
    """
    Call LiteLLM proxy to generate contextual prefix + HyPE questions for one chunk.
    Returns None on any failure (timeout, HTTP error, parse error).
    """
    doc_context = _truncate_to_tokens(
        _strip_frontmatter(document_text),
        settings.enrichment_max_document_tokens,
    )
    prompt = ENRICHMENT_PROMPT.format(
        title=title,
        path=path,
        document_text=doc_context,
        chunk_text=chunk_text,
    )

    payload = {
        "model": settings.enrichment_model,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "max_tokens": 300,
    }

    headers = {"Content-Type": "application/json"}
    if settings.litellm_api_key:
        headers["Authorization"] = f"Bearer {settings.litellm_api_key}"

    try:
        async with httpx.AsyncClient(timeout=settings.enrichment_timeout) as client:
            resp = await client.post(
                f"{settings.litellm_url}/v1/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        logger.warning("Enrichment LLM timeout for path=%s", path)
        return None
    except Exception as exc:
        logger.warning("Enrichment LLM error for path=%s: %s", path, exc)
        return None

    try:
        content = data["choices"][0]["message"]["content"]
        return EnrichmentResult.model_validate_json(content)
    except (KeyError, IndexError, ValidationError, ValueError) as exc:
        logger.warning("Enrichment LLM unparseable response for path=%s: %s", path, exc)
        return None


async def enrich_chunks(
    document_text: str,
    chunks: list[str],
    title: str,
    path: str,
) -> list[EnrichedChunk]:
    """
    Enrich all chunks with a semaphore limiting concurrent LLM calls.
    Chunks that fail enrichment fall back to their original text.
    """
    semaphore = asyncio.Semaphore(settings.enrichment_max_concurrent)

    async def _enrich_one(chunk_text: str) -> EnrichedChunk:
        async with semaphore:
            result = await enrich_chunk(document_text, chunk_text, title, path)
        if result is None:
            return EnrichedChunk(
                original_text=chunk_text,
                enriched_text=chunk_text,
                context_prefix="",
                questions=[],
            )
        enriched_text = f"{result.context_prefix}\n\n{chunk_text}"
        return EnrichedChunk(
            original_text=chunk_text,
            enriched_text=enriched_text,
            context_prefix=result.context_prefix,
            questions=result.questions,
        )

    return await asyncio.gather(*[_enrich_one(c) for c in chunks])
