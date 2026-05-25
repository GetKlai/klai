"""Compatibility re-export for deterministic citation helpers.

The implementation lives in ``klai-citations`` so portal/widget code and the
LiteLLM hook use the same composer. Keep this module for existing imports.
"""

from klai_citations import (  # noqa: F401
    CitationSource,
    ComposedCitations,
    citation_sources_from_chunks,
    compose_citations,
    format_sources_markdown,
    normalise_source_url,
    render_markdown_answer_with_sources,
    source_url_key,
    strip_model_citation_artifacts,
)

__all__ = [
    "CitationSource",
    "ComposedCitations",
    "citation_sources_from_chunks",
    "compose_citations",
    "format_sources_markdown",
    "normalise_source_url",
    "render_markdown_answer_with_sources",
    "source_url_key",
    "strip_model_citation_artifacts",
]
