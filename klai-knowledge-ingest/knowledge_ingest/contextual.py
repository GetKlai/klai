"""
SPEC-RAG-CONTEXTUAL-001 — Anthropic-pattern contextual retrieval.

The enrichment pipeline already produces a per-chunk `context_prefix` that
is prepended to chunk text before embedding. The original prompt template
(`ENRICHMENT_PROMPT` in enrichment.py) sends the FULL document body with
each chunk — for an 8000-token document with 20 chunks that is 160K input
tokens of redundant payload.

This module replaces that pattern with Anthropic's: generate a 1-2-sentence
summary of the document ONCE, cache it, and prepend the (much smaller)
summary to every chunk's enrichment prompt instead of the full document.

Two responsibilities:

1. ``detect_language(text)`` — pick "nl" / "en" / fallback "en". Determines
   which prompt template (Dutch or English) the enrichment pipeline uses.
   Per-document, not per-tenant: a Dutch tenant can have English vendor
   docs in their KB.

2. ``generate_document_summary(text, title, language, …)`` — call klai-fast
   via the LiteLLM proxy and return a 1-2-sentence summary capped at the
   token budget. Returns an empty string on any failure — callers fall back
   to the legacy full-document prompt path (REQ-2 of SPEC-RAG-CONTEXTUAL-001).

Caching:
    Per-document summary is persisted on ``knowledge.artifacts.extra.document_summary``
    keyed by content_hash. This module only generates the summary; the
    persistence/lookup lives at the enrichment-task layer where artifact
    rows are already being read/written. Re-ingesting the same content
    returns the cached summary at zero cost.
"""

from __future__ import annotations

import asyncio
from typing import Final

import httpx
import structlog

from knowledge_ingest.config import settings

logger = structlog.get_logger()

# Languages we have dedicated prompt templates for. Anything else falls
# back to the English template — Mistral Small handles English best on
# evaluation benchmarks and serves as a sensible default for unknown.
SUPPORTED_PROMPT_LANGUAGES: Final[set[str]] = {"nl", "en"}
DEFAULT_PROMPT_LANGUAGE: Final[str] = "en"

# fasttext-langdetect needs at least a few characters to be useful;
# below this threshold we cannot detect reliably and return the default.
_LANGDETECT_MIN_CHARS: Final[int] = 30
_LANGDETECT_SAMPLE_CHARS: Final[int] = 500

# Document summary budget. A 1-2 sentence summary fits comfortably under
# 200 tokens of output; we cap requests at 220 to allow a tiny buffer.
_SUMMARY_MAX_TOKENS: Final[int] = 220
_SUMMARY_TIMEOUT_S: Final[float] = 30.0

_SUMMARY_PROMPT_NL = """\
Documenttitel: {title}

<document>
{document_text}
</document>

Schrijf een Nederlandse samenvatting van max 2 zinnen die beschrijft \
waar dit document over gaat. Geen opsomming, geen markdown, geen aanhef.
Begin direct met de inhoud."""

_SUMMARY_PROMPT_EN = """\
Document title: {title}

<document>
{document_text}
</document>

Write an English summary of at most 2 sentences describing what this \
document is about. No bullet list, no markdown, no preamble. Start \
directly with the content."""

# Approx 4 chars per token for mixed Dutch/English. The full-document
# context that lands in the summary prompt is capped via this conversion.
_DOC_CONTEXT_MAX_CHARS: Final[int] = settings.enrichment_max_document_tokens * 4


def detect_language(text: str) -> str:
    """Return ISO-639-1 code for the dominant language of ``text``.

    Returns ``DEFAULT_PROMPT_LANGUAGE`` ("en") when the text is too short
    to detect reliably or detection fails. Currently recognises "nl" and
    "en"; everything else falls back to the default.

    Detection uses ``langdetect`` (pure Python; ports Nakatani Shuyo's
    Java library, ~99% accuracy on >50 char inputs). Sample is capped at
    ``_LANGDETECT_SAMPLE_CHARS`` chars so long documents don't waste cycles.
    The detector seed is fixed for reproducibility — without a seed the
    library re-randomises on every call and short inputs can flip-flop
    between candidate languages.
    """
    if not text or len(text.strip()) < _LANGDETECT_MIN_CHARS:
        return DEFAULT_PROMPT_LANGUAGE

    try:
        from langdetect import DetectorFactory, detect
        from langdetect.lang_detect_exception import LangDetectException
    except ImportError:
        logger.warning(
            "contextual_langdetect_unavailable",
            reason="langdetect not installed; defaulting to en",
        )
        return DEFAULT_PROMPT_LANGUAGE

    DetectorFactory.seed = 0  # deterministic across calls

    sample = text.strip().replace("\n", " ")[:_LANGDETECT_SAMPLE_CHARS]
    try:
        lang = detect(sample)
    except LangDetectException as exc:
        logger.warning("contextual_langdetect_failed", error=str(exc)[:200])
        return DEFAULT_PROMPT_LANGUAGE

    if lang in SUPPORTED_PROMPT_LANGUAGES:
        return lang
    return DEFAULT_PROMPT_LANGUAGE


def _build_summary_prompt(text: str, title: str, language: str) -> str:
    """Pick the language-specific summary template and inject the doc."""
    template = _SUMMARY_PROMPT_NL if language == "nl" else _SUMMARY_PROMPT_EN
    snippet = text[:_DOC_CONTEXT_MAX_CHARS]
    return template.format(title=title or "(untitled)", document_text=snippet)


async def generate_document_summary(
    text: str,
    title: str,
    language: str | None = None,
    *,
    _transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """Return a 1-2-sentence summary of ``text``, or "" on failure.

    Calls ``klai-fast`` via the LiteLLM proxy with a low-temperature prompt
    in the requested language (auto-detected when ``language`` is None).

    Fail-open: any HTTP/transport/parse error logs a warning and returns
    an empty string. Callers that get "" back fall back to the legacy
    full-document enrichment prompt — chunks are still embedded, just
    without the summary-driven context. (SPEC-RAG-CONTEXTUAL-001 REQ-2.)
    """
    if not text or not text.strip():
        return ""

    if language is None:
        language = detect_language(text)

    prompt = _build_summary_prompt(text, title, language)
    payload = {
        "model": settings.enrichment_model,  # klai-fast
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": _SUMMARY_MAX_TOKENS,
        "temperature": 0.2,
    }
    api_key = settings.litellm_api_key or "no-key"
    headers = {"Authorization": f"Bearer {api_key}"}
    url = f"{settings.litellm_url}/v1/chat/completions"

    client_kwargs: dict = {"timeout": _SUMMARY_TIMEOUT_S}
    if _transport is not None:
        client_kwargs["transport"] = _transport

    try:
        async with httpx.AsyncClient(**client_kwargs) as client:
            resp = await asyncio.wait_for(
                client.post(url, json=payload, headers=headers),
                timeout=_SUMMARY_TIMEOUT_S,
            )
            resp.raise_for_status()
            data = resp.json()
        content = data["choices"][0]["message"]["content"].strip()
        return content
    except Exception as exc:
        logger.warning(
            "contextual_summary_failed",
            title=title[:80] if title else None,
            language=language,
            error=str(exc)[:200],
        )
        return ""
