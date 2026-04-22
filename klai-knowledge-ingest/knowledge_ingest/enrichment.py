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
import json
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

# Appended to the prompt on the retry call when chunk_type validation fails.
# Strengthens the instruction so the LLM picks one of the five valid values.
# SPEC-CRAWLER-005 REQ-03.2 / EC-4
_CHUNK_TYPE_RETRY_ADDENDUM = (
    '\n\nIMPORTANT: "chunk_type" MUST be exactly one of: '
    '"procedural", "conceptual", "reference", "warning", "example". '
    "No other value is accepted. Reply with ONLY a JSON object."
)


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


async def _call_llm(prompt: str, path: str) -> dict:
    """
    Execute a single LLM chat completion call via LiteLLM proxy.
    Returns the parsed response dict on success.
    Raises EnrichmentError for transport/HTTP failures (Procrastinate retries these).
    """
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
            return resp.json()  # type: ignore[return-value]
    except httpx.TimeoutException as exc:
        logger.warning("enrichment_llm_timeout", path=path)
        raise EnrichmentError(f"LLM timeout enriching {path}") from exc
    except Exception as exc:
        logger.warning("enrichment_llm_error", path=path, error=str(exc))
        raise EnrichmentError(f"LLM error enriching {path}: {exc}") from exc


def _extract_content(data: dict) -> str:
    """
    Extract and clean the text content from a LiteLLM chat completion response dict.
    Strips markdown code fences if the LLM wraps the JSON output in them.
    Raises KeyError/IndexError when the response structure is malformed
    (caller converts these to EnrichmentError).
    """
    content = data["choices"][0]["message"]["content"]
    content = (content or "").strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return content


def _try_parse_result(content: str) -> EnrichmentResult | None:
    """
    Attempt to parse a JSON string into an EnrichmentResult.

    Returns None when the JSON is structurally valid but chunk_type fails Pydantic
    Literal validation (the retry/fallback path applies).
    Raises EnrichmentError for genuine JSON syntax errors (transport problem —
    Procrastinate should retry the whole job).
    """
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, ValueError) as exc:
        raise EnrichmentError(f"Unparseable JSON in LLM response: {exc}") from exc

    try:
        return EnrichmentResult.model_validate(parsed)
    except ValidationError:
        return None


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
    artifact_id: str = "",
    chunk_index: int = 0,
) -> EnrichmentResult:
    """
    Call LiteLLM proxy to generate contextual prefix + HyPE questions for one chunk.

    Transport/HTTP/JSON-parse failures raise EnrichmentError (Procrastinate retries).
    Invalid chunk_type in the LLM response triggers a single retry with a strengthened
    prompt addendum (SPEC-CRAWLER-005 REQ-03.2 / EC-4). If the retry also returns an
    invalid chunk_type, the function falls back to chunk_type="reference" and emits a
    structured crawl_chunk_type_drop warning log for ops monitoring.

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

    # First LLM call
    data = await _call_llm(prompt, path)

    try:
        content = _extract_content(data)
    except (KeyError, IndexError) as exc:
        logger.warning("enrichment_llm_unparseable", path=path, error=str(exc))
        raise EnrichmentError(f"Unparseable LLM response for {path}: {exc}") from exc

    result = _try_parse_result(content)
    if result is not None:
        return result

    # chunk_type validation failed — retry once with a strengthened addendum
    retry_prompt = prompt + _CHUNK_TYPE_RETRY_ADDENDUM
    retry_data = await _call_llm(retry_prompt, path)

    try:
        retry_content = _extract_content(retry_data)
    except (KeyError, IndexError) as exc:
        logger.warning("enrichment_llm_unparseable", path=path, error=str(exc))
        raise EnrichmentError(f"Unparseable LLM response for {path}: {exc}") from exc

    retry_result = _try_parse_result(retry_content)
    if retry_result is not None:
        return retry_result

    # Both calls returned invalid chunk_type — fall back to "reference" and log.
    # Parse what we can from the raw JSON for context_prefix and questions.
    try:
        raw_parsed = json.loads(retry_content)
    except (json.JSONDecodeError, ValueError):
        raw_parsed = {}

    fallback = EnrichmentResult(
        context_prefix=raw_parsed.get("context_prefix") or "",
        chunk_type="reference",
        questions=raw_parsed.get("questions") or [],
    )
    logger.warning(
        "crawl_chunk_type_drop",
        artifact_id=artifact_id,
        chunk_index=chunk_index,
        raw_llm_response=retry_content[:200],
        reason="retry_exhausted",
        path=path,
    )
    return fallback


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
    artifact_id: str = "",
) -> list[EnrichedChunk]:
    """
    Enrich all chunks with a semaphore limiting concurrent LLM calls.
    Raises EnrichmentError on any LLM failure — callers (Procrastinate tasks) let this
    propagate so the job is retried up to max_attempts times.

    context_strategy: name of a strategy in context_strategies.STRATEGIES.
    context_tokens: max tokens for the extracted context window.
    The strategy is applied per-chunk (with chunk_index) so rolling_window gets correct positioning.
    kb_name, connector_type, source_domain: source-aware enrichment fields (SPEC-KB-021).
    artifact_id: passed through to enrich_chunk for crawl_chunk_type_drop log correlation.
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
                artifact_id=artifact_id,
                chunk_index=chunk_index,
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
