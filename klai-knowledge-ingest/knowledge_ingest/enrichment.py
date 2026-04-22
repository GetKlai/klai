"""
LLM enrichment service: contextual prefix generation + HyPE question generation.

Each chunk gets a single LLM call (via LiteLLM proxy) returning:
  {"context_prefix": "...", "chunk_type": "...", "questions": ["...", ...]}

Enriched chunk text = "{context_prefix}\n\n{original_text}"
Questions are used for vector_questions (depth 0-1 only) and stored in payload.
chunk_type (SPEC-KB-021) classifies each chunk as one of
procedural/conceptual/reference/warning/example for downstream retrieval
routing and assertion-mode filtering. This is chunk-level and distinct from
the document-level content_type (kb_article/pdf_document/meeting_transcript/
web_crawl/...) consumed by retrieval_api.services.evidence_tier.
"""
import asyncio
from dataclasses import dataclass
from typing import Literal

import httpx
import structlog
from pydantic import BaseModel, ValidationError

from knowledge_ingest.config import settings
from knowledge_ingest.context_strategies import STRATEGIES

logger = structlog.get_logger()

ENRICHMENT_PROMPT = """\
Kennisbank: {kb_name}
Bron: {source_context}
Documenttitel: {title}
Pad: {path}

<document>
{document_text}
</document>

<chunk>
{chunk_text}
</chunk>
{participant_context}
Genereer een JSON-object met:
- "context_prefix": een zin van max 120 tokens die deze chunk plaatst binnen het document \
(welke KB en bronsysteem, welk document/sectie, eventuele domeinspecifieke terminologie).
- "chunk_type": classificeer de chunk als exact één van: \
"procedural" (stap-voor-stap instructies), "conceptual" (uitleg van begrippen), \
"reference" (naslag/specificaties), "warning" (waarschuwingen/beperkingen), \
"example" (voorbeelden/cases).
- "questions": 3-5 vragen die deze chunk beantwoordt. \
{question_focus}

Reply with ONLY a JSON object, no markdown, no explanation:
{{"context_prefix": "<string>", "chunk_type": "<procedural|conceptual|reference|warning|example>", \
"questions": ["<string>", ...]}}"""


class EnrichmentError(Exception):
    """Transient LLM failure — Procrastinate will retry the job."""


class EnrichmentResult(BaseModel):
    context_prefix: str
    chunk_type: Literal["procedural", "conceptual", "reference", "warning", "example"]
    questions: list[str]


@dataclass
class EnrichedChunk:
    original_text: str
    enriched_text: str       # "{context_prefix}\n\n{original_text}"
    context_prefix: str
    questions: list[str]     # embedded as vector_questions for depth 0-1; stored in payload for all
    # @MX:NOTE: SPEC-KB-021 chunk-level classification (procedural/conceptual/
    #   reference/warning/example). Distinct from the document-level content_type
    #   field ("kb_article", "pdf_document", ...) stored on the Qdrant point
    #   payload and consumed by retrieval_api.services.evidence_tier.
    chunk_type: str = ""


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
    question_focus: str = "",
    participant_context: str = "",
    context_window: str | None = None,
    kb_name: str = "",
    connector_type: str = "",
    source_domain: str = "",
) -> EnrichmentResult:
    """
    Call LiteLLM proxy to generate contextual prefix + HyPE questions for one chunk.
    Raises EnrichmentError on any failure (timeout, HTTP error, parse error, invalid
    content_type). Chunks must NOT be upserted to Qdrant when this raises.
    When context_window is provided, it is used as the document context in the prompt
    instead of truncating the full document_text.
    """
    if context_window is not None:
        doc_context = context_window
    else:
        doc_context = _truncate_to_tokens(
            _strip_frontmatter(document_text),
            settings.enrichment_max_document_tokens,
        )
    # Default question focus if none provided
    effective_focus = question_focus or (
        "De vragen moeten natuurlijke zoekopdrachten zijn die een gebruiker zou typen."
    )
    # Build source context string for the prompt
    source_parts = []
    if kb_name:
        source_parts.append(kb_name)
    if connector_type:
        source_parts.append(connector_type)
    if source_domain:
        source_parts.append(source_domain)
    source_context = " | ".join(source_parts) if source_parts else "onbekend"

    prompt = ENRICHMENT_PROMPT.format(
        kb_name=kb_name or "onbekend",
        source_context=source_context,
        title=title,
        path=path,
        document_text=doc_context,
        chunk_text=chunk_text,
        question_focus=effective_focus,
        participant_context=participant_context,
    )

    payload = {
        "model": settings.enrichment_model,
        "messages": [{"role": "user", "content": prompt}],
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
    except httpx.TimeoutException as exc:
        logger.warning("enrichment_llm_timeout", path=path)
        raise EnrichmentError(f"LLM timeout enriching {path}") from exc
    except Exception as exc:
        logger.warning("enrichment_llm_error", path=path, error=str(exc))
        raise EnrichmentError(f"LLM error enriching {path}: {exc}") from exc

    try:
        content = data["choices"][0]["message"]["content"]
        content = (content or "").strip()
        # Strip markdown code fences if present
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return EnrichmentResult.model_validate_json(content)
    except (KeyError, IndexError, ValidationError, ValueError) as exc:
        logger.warning("enrichment_llm_unparseable", path=path, error=str(exc))
        raise EnrichmentError(f"Unparseable LLM response for {path}: {exc}") from exc


async def enrich_chunks(
    document_text: str,
    chunks: list[str],
    title: str,
    path: str,
    question_focus: str = "",
    participant_context: str = "",
    context_strategy: str = "first_n",
    context_tokens: int = 2000,
    kb_name: str = "",
    connector_type: str = "",
    source_domain: str = "",
) -> list[EnrichedChunk]:
    """
    Enrich all chunks with a semaphore limiting concurrent LLM calls.
    Raises EnrichmentError on any LLM failure — callers (Procrastinate tasks) let this
    propagate so the job is retried up to max_attempts times.

    context_strategy: name of a strategy in context_strategies.STRATEGIES.
    context_tokens: max tokens for the extracted context window.
    The strategy is applied per-chunk (with chunk_index) so rolling_window gets correct positioning.
    kb_name, connector_type, source_domain: source-aware enrichment fields (SPEC-KB-021).
    """
    semaphore = asyncio.Semaphore(settings.enrichment_max_concurrent)
    strategy_fn = STRATEGIES.get(context_strategy, STRATEGIES["first_n"])

    async def _enrich_one(chunk_text: str, chunk_index: int) -> EnrichedChunk:
        context_window = strategy_fn(document_text, context_tokens, chunk_index=chunk_index)
        async with semaphore:
            result = await enrich_chunk(
                document_text, chunk_text, title, path,
                question_focus=question_focus,
                participant_context=participant_context,
                context_window=context_window,
                kb_name=kb_name,
                connector_type=connector_type,
                source_domain=source_domain,
            )
        enriched_text = f"{result.context_prefix}\n\n{chunk_text}"
        return EnrichedChunk(
            original_text=chunk_text,
            enriched_text=enriched_text,
            context_prefix=result.context_prefix,
            questions=result.questions,
            chunk_type=result.chunk_type,
        )

    return await asyncio.gather(*[_enrich_one(c, i) for i, c in enumerate(chunks)])
