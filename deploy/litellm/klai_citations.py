"""Deterministic source citation composition for RAG answers.

The LLM may write prose, but it must not be the authority for source URLs or
visible citation labels. This module turns retrieved chunk provenance into a
small structured citation contract that clients can render safely.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse, urlunparse


@dataclass
class CitationSource:
    key: str
    url: str
    title: str
    chunk_texts: list[str] = field(default_factory=list)


@dataclass
class ComposedCitations:
    content: str
    sources: list[dict[str, str]]


@dataclass
class CitationRegistry:
    sources: list[CitationSource]

    @property
    def has_sources(self) -> bool:
        return bool(self.sources)


_RAW_URL_RE = re.compile(r"https?://[^\s<>)]+")
_MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\((\S+?)(?:\s+['\"][^'\"]*['\"])?\)")
_MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\([^)]*\)")
_NUMERIC_MARKDOWN_CITATION_RE = re.compile(r"\[\s*\d{1,3}\s*\]\([^)]*\)")
_BARE_BRACKET_CITATION_RE = re.compile(r"(?<!!)\[\s*\d{1,3}\s*\]")
_PAREN_CITATION_RE = re.compile(r"\(\s*\d{1,3}(?:\s*[,;]\s*\d{1,3})*\s*\)")
_MALFORMED_NUMBER_URL_RE = re.compile(r"\b\d{1,3}\(https?://[^)\s]+\)")
_BARE_NUMBER_RUN_RE = re.compile(r"(?<![\w/])\b\d{1,3}(?:\s*[,;]\s*\d{1,3})+\b(?=(?:[.!?])?(?:\s|$))")
_SOURCE_HEADING_RE = re.compile(r"^\s*(?:bronnen?|sources?|references?)\s*:?\s*$", re.IGNORECASE)
_SOURCE_LIST_LINE_RE = re.compile(r"^\s*(?:\(\s*\d{1,3}\s*\)|\[\s*\d{1,3}\s*\]|\d{1,3}[.)])\s*(.+)$")
_DEFAULT_MAX_SOURCES = 3


def normalise_source_url(url: object) -> str:
    if not isinstance(url, str):
        return ""
    value = url.strip().strip("<>")
    if value.lower() in {"", "undefined", "null", "none"}:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    hostname = (parsed.hostname or "").lower()
    if hostname in {"undefined", "null", "none"}:
        return ""
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    placeholder_path = (parsed.path or "").strip("/").lower()
    if placeholder_path in {"undefined", "null", "none"}:
        return ""
    return urlunparse((parsed.scheme.lower(), netloc, parsed.path or "/", "", parsed.query, ""))


def source_url_key(url: object) -> str:
    normalised = normalise_source_url(url)
    if not normalised:
        return ""
    parsed = urlparse(normalised)
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme, parsed.netloc, path, "", parsed.query, ""))


def _chunk_source_url(chunk: dict) -> str:
    for candidate in (
        chunk.get("source_url"),
        chunk.get("url"),
        chunk.get("sourceUrl"),
        chunk.get("canonical_url"),
        chunk.get("page_url"),
        chunk.get("source_ref"),
    ):
        if normalised := normalise_source_url(candidate):
            return normalised

    metadata = chunk.get("metadata")
    if isinstance(metadata, dict):
        for key in ("source_url", "url", "sourceUrl", "canonical_url", "page_url", "source_ref"):
            if normalised := normalise_source_url(metadata.get(key)):
                return normalised

    source = chunk.get("source")
    if isinstance(source, dict):
        for key in ("source_url", "url", "href"):
            if normalised := normalise_source_url(source.get(key)):
                return normalised

    return ""


def _chunk_source_title(chunk: dict) -> str:
    metadata = chunk.get("metadata")
    candidates = (
        chunk.get("title"),
        metadata.get("title") if isinstance(metadata, dict) else None,
        chunk.get("source_label"),
    )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return "Source"


def citation_sources_from_chunks(chunks: list[dict]) -> list[CitationSource]:
    sources_by_key: dict[str, CitationSource] = {}
    for chunk in chunks:
        url = _chunk_source_url(chunk)
        key = source_url_key(url)
        if not url or not key:
            continue
        source = sources_by_key.get(key)
        if source is None:
            source = CitationSource(key=key, url=url, title=_chunk_source_title(chunk))
            sources_by_key[key] = source
        text = chunk.get("text")
        if isinstance(text, str) and text.strip():
            source.chunk_texts.append(text)
    return list(sources_by_key.values())


def build_citation_registry(chunks: list[dict]) -> CitationRegistry:
    """Build the deterministic source registry from trusted chunk metadata."""
    return CitationRegistry(sources=citation_sources_from_chunks(chunks))


def _looks_like_source_list_line(line: str) -> bool:
    match = _SOURCE_LIST_LINE_RE.match(line)
    if not match:
        return False
    rest = match.group(1).strip()
    if _RAW_URL_RE.search(rest):
        return True
    if "*" in rest or "http" in rest.lower():
        return True
    return bool(re.search(r"\b(?:bron|source|stichting|privacy|policy|docs?)\b", rest, re.IGNORECASE))


def strip_model_citation_artifacts(text: str, *, allowed_image_urls: set[str] | None = None) -> str:
    """Remove model-authored citations/source lists before composing our own."""
    allowed_image_urls = {
        normalised for normalised in (normalise_source_url(url) for url in (allowed_image_urls or set())) if normalised
    }
    image_placeholders: dict[str, str] = {}

    def _replace_image(match: re.Match[str]) -> str:
        url = normalise_source_url(match.group(2))
        if url and url in allowed_image_urls:
            placeholder = f"__KLAI_ALLOWED_IMAGE_{len(image_placeholders)}__"
            image_placeholders[placeholder] = match.group(0)
            return placeholder
        return match.group(1).strip() or "[image unavailable in knowledge base]"

    kept_lines: list[str] = []
    skipping_source_section = False
    for line in _MARKDOWN_IMAGE_RE.sub(_replace_image, text).splitlines():
        if _SOURCE_HEADING_RE.match(line):
            skipping_source_section = True
            continue
        if skipping_source_section:
            if not line.strip():
                skipping_source_section = False
            continue
        if _looks_like_source_list_line(line):
            continue
        kept_lines.append(line)

    cleaned = "\n".join(kept_lines)
    cleaned = _MALFORMED_NUMBER_URL_RE.sub("", cleaned)
    cleaned = _NUMERIC_MARKDOWN_CITATION_RE.sub("", cleaned)
    cleaned = _MARKDOWN_LINK_RE.sub(r"\1", cleaned)
    cleaned = _BARE_BRACKET_CITATION_RE.sub("", cleaned)
    cleaned = _PAREN_CITATION_RE.sub("", cleaned)
    cleaned = _BARE_NUMBER_RUN_RE.sub("", cleaned)
    cleaned = _RAW_URL_RE.sub("", cleaned)
    cleaned = re.sub(r"\s+\n", "\n", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"[ \t]+([.,;:!?])", r"\1", cleaned)
    cleaned = re.sub(r"\(\s*\)", "", cleaned)
    for placeholder, image_markdown in image_placeholders.items():
        cleaned = cleaned.replace(placeholder, image_markdown)
    return cleaned.strip()


def _compose_citations_from_sources(
    text: str,
    sources: list[CitationSource],
    *,
    max_sources: int | None = _DEFAULT_MAX_SOURCES,
    max_sources_per_segment: int = 2,
    allowed_image_urls: set[str] | None = None,
) -> ComposedCitations:
    """Return clean answer text plus document-level sources.

    Inline citation placement cannot be made reliable from free-form LLM text
    without asking the model to produce a claim-to-source contract. Keep this
    layer deterministic: sanitize model-authored citation artifacts, then attach
    a compact source registry built from trusted retrieval metadata.
    """
    cleaned = strip_model_citation_artifacts(text, allowed_image_urls=allowed_image_urls)
    if not cleaned or not sources:
        return ComposedCitations(content=cleaned, sources=[])

    rendered_sources = render_structured_sources(
        CitationRegistry(sources=sources),
        max_sources=max_sources,
    )
    return ComposedCitations(content=cleaned, sources=rendered_sources)


def compose_citations(
    text: str,
    chunks: list[dict],
    *,
    max_sources: int | None = _DEFAULT_MAX_SOURCES,
    max_sources_per_segment: int = 2,
    allowed_image_urls: set[str] | None = None,
) -> ComposedCitations:
    registry = build_citation_registry(chunks)
    return _compose_citations_from_sources(
        text,
        registry.sources,
        max_sources=max_sources,
        max_sources_per_segment=max_sources_per_segment,
        allowed_image_urls=allowed_image_urls,
    )


def format_sources_markdown(sources: list[dict[str, str]]) -> str:
    """Render deterministic source links for plain Markdown chat clients."""
    lines: list[str] = []
    for source in sources:
        title = (source.get("title") or "").strip() or "Source"
        url = normalise_source_url(source.get("url"))
        if not url:
            continue
        lines.append(f"- [{title}]({url})")
    return "\n".join(lines)


def render_structured_sources(
    registry: CitationRegistry,
    *,
    max_sources: int | None = _DEFAULT_MAX_SOURCES,
) -> list[dict[str, str]]:
    sources = registry.sources if max_sources is None else registry.sources[:max_sources]
    return [
        {"label": str(index), "title": source.title, "url": source.url}
        for index, source in enumerate(sources, 1)
    ]


def render_markdown_sources(
    registry: CitationRegistry,
    *,
    max_sources: int | None = _DEFAULT_MAX_SOURCES,
) -> str:
    return format_sources_markdown(render_structured_sources(registry, max_sources=max_sources))


def render_structured_answer(
    text: str,
    registry: CitationRegistry,
    *,
    allowed_image_urls: set[str] | None = None,
    max_sources: int | None = _DEFAULT_MAX_SOURCES,
) -> ComposedCitations:
    return _compose_citations_from_sources(
        text,
        registry.sources,
        max_sources=max_sources,
        allowed_image_urls=allowed_image_urls,
    )


def render_markdown_answer(
    text: str,
    registry: CitationRegistry,
    *,
    allowed_image_urls: set[str] | None = None,
    max_sources: int | None = _DEFAULT_MAX_SOURCES,
) -> ComposedCitations:
    composed = render_structured_answer(
        text,
        registry,
        allowed_image_urls=allowed_image_urls,
        max_sources=max_sources,
    )
    sources_markdown = format_sources_markdown(composed.sources)
    if not sources_markdown:
        return composed
    return ComposedCitations(content=f"{composed.content}\n\n{sources_markdown}", sources=composed.sources)


def render_markdown_answer_with_sources(
    text: str,
    chunks: list[dict],
    *,
    allowed_image_urls: set[str] | None = None,
    max_sources: int | None = _DEFAULT_MAX_SOURCES,
) -> ComposedCitations:
    """Append a deterministic Markdown source list to clean answer text.

    This is for chat clients that do not have a structured ``sources`` field.
    Widget-style clients should use :func:`compose_citations` and render
    ``ComposedCitations.sources`` separately.
    """
    registry = build_citation_registry(chunks)
    return render_markdown_answer(
        text,
        registry,
        allowed_image_urls=allowed_image_urls,
        max_sources=max_sources,
    )


__all__ = [
    "CitationRegistry",
    "CitationSource",
    "ComposedCitations",
    "build_citation_registry",
    "citation_sources_from_chunks",
    "compose_citations",
    "format_sources_markdown",
    "normalise_source_url",
    "render_markdown_answer",
    "render_markdown_answer_with_sources",
    "render_markdown_sources",
    "render_structured_answer",
    "render_structured_sources",
    "source_url_key",
    "strip_model_citation_artifacts",
]
