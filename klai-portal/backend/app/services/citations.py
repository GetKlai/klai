"""Compatibility re-export for deterministic citation helpers.

The implementation lives in ``klai-citations`` so portal/widget code and the
LiteLLM hook use the same composer. Keep this module for existing imports.
"""

from klai_citations import (
    CitationRegistry,
    CitationSource,
    ComposedCitations,
    build_citation_registry,
    citation_sources_from_chunks,
    compose_answer_with_trusted_sources,
    compose_citations,
    evidence_chunks_from_chunks,
    evidence_pack_items_as_chunks,
    format_sources_markdown,
    normalise_source_url,
    render_evidence_context,
    render_markdown_answer,
    render_markdown_answer_with_sources,
    render_markdown_sources,
    render_structured_answer,
    render_structured_sources,
    source_url_key,
    strip_model_citation_artifacts,
    trusted_sources_from_evidence_pack,
)

__all__ = [
    "CitationRegistry",
    "CitationSource",
    "ComposedCitations",
    "build_citation_registry",
    "citation_sources_from_chunks",
    "compose_answer_with_trusted_sources",
    "compose_citations",
    "evidence_chunks_from_chunks",
    "evidence_pack_items_as_chunks",
    "format_sources_markdown",
    "normalise_source_url",
    "render_evidence_context",
    "render_markdown_answer",
    "render_markdown_answer_with_sources",
    "render_markdown_sources",
    "render_structured_answer",
    "render_structured_sources",
    "source_url_key",
    "strip_model_citation_artifacts",
    "trusted_sources_from_evidence_pack",
]
