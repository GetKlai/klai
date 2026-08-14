"""
LLM enrichment service: contextual prefix generation + HyPE question generation.

Each chunk gets a single LLM call (via LiteLLM proxy) returning:
  {"context_prefix": "...", "questions": ["...", ...]}

Enriched chunk text = "{context_prefix}\n\n{original_text}"
Questions are used for vector_questions (depth 0-1 only) and stored in payload.

Note: the per-chunk ``chunk_type`` classification was removed 2026-06-08
(docs/research/chunk-type-retrieval-value.md) — it was an LLM-classified label
that no retrieval consumer read, and dropping it also removes the strict-Literal
validation retry round-trip per chunk. Document-level ``content_type`` (consumed
by retrieval_api.services.evidence_tier) is unaffected.
"""

import asyncio
import json
from dataclasses import dataclass

import httpx
import structlog
from klai_llm_safety import SafetyPhase, SafetyRequest, SafetySurface, check_text
from pydantic import BaseModel, ValidationError

from knowledge_ingest.config import settings
from knowledge_ingest.context_strategies import STRATEGIES
from knowledge_ingest.llm_throttle import shared_klai_fast_limiter

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
- "questions": 3-5 vragen die deze chunk beantwoordt. \
{question_focus}

Reply with ONLY a JSON object, no markdown, no explanation:
{{"context_prefix": "<string>", "questions": ["<string>", ...]}}"""

# SPEC-RAG-CONTEXTUAL-001 — Anthropic-pattern: instead of feeding the full
# document text on every chunk's enrichment call, feed a pre-computed 1-2
# sentence summary. Cuts per-chunk input tokens ~8x for a 20-chunk doc.
ENRICHMENT_PROMPT_SUMMARY_NL = """\
Kennisbank: {kb_name}
Bron: {source_context}
Documenttitel: {title}
Pad: {path}

<document_summary>
{document_summary}
</document_summary>

<chunk>
{chunk_text}
</chunk>
{participant_context}
Genereer een JSON-object met:
- "context_prefix": een zin van max 120 tokens die deze chunk plaatst binnen het document \
(welke KB en bronsysteem, welk document/sectie, eventuele domeinspecifieke terminologie).
- "questions": 3-5 vragen die deze chunk beantwoordt. \
{question_focus}

Reply with ONLY a JSON object, no markdown, no explanation:
{{"context_prefix": "<string>", "questions": ["<string>", ...]}}"""

ENRICHMENT_PROMPT_SUMMARY_EN = """\
Knowledge base: {kb_name}
Source: {source_context}
Document title: {title}
Path: {path}

<document_summary>
{document_summary}
</document_summary>

<chunk>
{chunk_text}
</chunk>
{participant_context}
Generate a JSON object with:
- "context_prefix": a single sentence (max 120 tokens) that places this chunk \
within the document (which KB and source system, which document/section, any \
domain-specific terminology).
- "questions": 3-5 questions this chunk answers. \
{question_focus}

Reply with ONLY a JSON object, no markdown, no explanation:
{{"context_prefix": "<string>", "questions": ["<string>", ...]}}"""


class EnrichmentError(Exception):
    """Transient LLM failure — Procrastinate will retry the job."""


class EnrichmentResult(BaseModel):
    context_prefix: str
    questions: list[str]


@dataclass
class EnrichedChunk:
    original_text: str
    enriched_text: str  # "{context_prefix}\n\n{original_text}"
    context_prefix: str
    questions: list[str]  # embedded as vector_questions for depth 0-1; stored in payload for all
    heading_path: str = ""


def _safe_empty_result() -> EnrichmentResult:
    return EnrichmentResult(context_prefix="", questions=[])


def _context_safety_violation(text: str) -> str | None:
    decision = check_text(
        SafetyRequest(
            text=text,
            phase=SafetyPhase.CONTEXT,
            surface=SafetySurface.INGEST_ENRICHMENT,
        )
    )
    return None if decision.allowed else decision.reason


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
    return text[end + 4 :].lstrip()


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
        await shared_klai_fast_limiter().acquire()
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

    Returns None when the JSON is structurally valid but is missing the required
    context_prefix / questions fields (the salvage path applies).
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
    document_summary: str | None = None,
    document_language: str | None = None,
) -> EnrichmentResult:
    """
    Call LiteLLM proxy to generate contextual prefix + HyPE questions for one chunk.

    Transport/HTTP/JSON-parse failures raise EnrichmentError (Procrastinate retries).
    A structurally valid JSON response that is missing context_prefix / questions is
    salvaged (empty prefix / no questions) rather than retried — there is no longer a
    strict-Literal field to validate, so the per-chunk retry round-trip is gone.

    Document context selection (in priority order):

    1. ``document_summary`` non-empty → use the SPEC-RAG-CONTEXTUAL-001
       Anthropic-pattern prompt that injects only a 1-2-sentence summary
       instead of the full document body. Cuts per-chunk input tokens ~8x
       for a 20-chunk document. Picks NL or EN template based on
       ``document_language`` (auto-falls-back to EN for unknown).
    2. ``context_window`` provided → use as the document context window
       in the legacy full-document prompt.
    3. Otherwise → truncate ``document_text`` to settings.enrichment_max_document_tokens.
    """
    safety_text = "\n".join(
        part
        for part in (document_summary or "", context_window or "", document_text, chunk_text)
        if part
    )
    if safety_reason := _context_safety_violation(safety_text):
        logger.warning(
            "enrichment_context_blocked",
            path=path,
            artifact_id=artifact_id,
            chunk_index=chunk_index,
            reason=safety_reason,
        )
        return _safe_empty_result()

    use_summary = bool(document_summary and document_summary.strip())
    if not use_summary:
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

    if use_summary:
        # Pick the language-specific summary template; default to EN for
        # unknown languages (klai-fast handles English best on benchmarks).
        if document_language == "nl":
            summary_template = ENRICHMENT_PROMPT_SUMMARY_NL
        else:
            summary_template = ENRICHMENT_PROMPT_SUMMARY_EN
        prompt = summary_template.format(
            kb_name=kb_name or "onbekend",
            source_context=source_context,
            title=title,
            path=path,
            document_summary=document_summary or "",
            chunk_text=chunk_text,
            question_focus=effective_focus,
            participant_context=participant_context,
        )
    else:
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

    # Structurally valid JSON but missing context_prefix / questions. Salvage what we
    # can WITHOUT a retry round-trip: the strict-Literal chunk_type that used to force
    # a second LLM call is gone, and context_prefix/questions are loose fields the
    # model rarely omits. ``content`` is valid JSON here (else _try_parse_result raised).
    try:
        raw_parsed = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        raw_parsed = {}

    if not (raw_parsed.get("context_prefix") or raw_parsed.get("questions")):
        logger.warning(
            "enrichment_result_salvaged",
            artifact_id=artifact_id,
            chunk_index=chunk_index,
            raw_llm_response=content[:200],
            path=path,
        )
    return EnrichmentResult(
        context_prefix=raw_parsed.get("context_prefix") or "",
        questions=raw_parsed.get("questions") or [],
    )


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
    document_summary: str | None = None,
    document_language: str | None = None,
    heading_paths: list[str] | None = None,
) -> list[EnrichedChunk]:
    """
    Enrich all chunks with a semaphore limiting concurrent LLM calls.
    Raises EnrichmentError on any LLM failure — callers (Procrastinate tasks) let this
    propagate so the job is retried up to max_attempts times.

    context_strategy: name of a strategy in context_strategies.STRATEGIES.
    context_tokens: max tokens for the extracted context window.
    The strategy is applied per-chunk (with chunk_index) so rolling_window gets correct positioning.
    kb_name, connector_type, source_domain: source-aware enrichment fields (SPEC-KB-021).
    artifact_id: passed through to enrich_chunk for enrichment-salvage log correlation.

    document_summary / document_language: SPEC-RAG-CONTEXTUAL-001. When a summary
    is provided, every chunk's enrichment prompt feeds the summary instead of
    the full document context window — Anthropic's contextual-retrieval pattern.
    Falls back to the legacy full-document path when summary is None or empty.
    """
    semaphore = asyncio.Semaphore(settings.enrichment_max_concurrent)
    strategy_fn = STRATEGIES.get(context_strategy, STRATEGIES["first_n"])

    use_summary = bool(document_summary and document_summary.strip())

    async def _enrich_one(chunk_text: str, chunk_index: int) -> EnrichedChunk:
        # Skip context-strategy work when we already have a summary — the
        # legacy strategies only matter for the full-document prompt path.
        context_window = (
            None
            if use_summary
            else strategy_fn(document_text, context_tokens, chunk_index=chunk_index)
        )
        async with semaphore:
            result = await enrich_chunk(
                document_text,
                chunk_text,
                title,
                path,
                question_focus=question_focus,
                participant_context=participant_context,
                context_window=context_window,
                kb_name=kb_name,
                connector_type=connector_type,
                source_domain=source_domain,
                artifact_id=artifact_id,
                chunk_index=chunk_index,
                document_summary=document_summary,
                document_language=document_language,
            )
        enriched_text = f"{result.context_prefix}\n\n{chunk_text}"
        heading_path = (
            heading_paths[chunk_index] if heading_paths and chunk_index < len(heading_paths) else ""
        )
        return EnrichedChunk(
            original_text=chunk_text,
            enriched_text=enriched_text,
            context_prefix=result.context_prefix,
            questions=result.questions,
            heading_path=heading_path,
        )

    return await asyncio.gather(*[_enrich_one(c, i) for i, c in enumerate(chunks)])
