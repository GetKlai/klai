"""Deterministic source citation composition for RAG answers.

The LLM may write prose, but it must not be the authority for source URLs or
visible citation labels. This module turns retrieved chunk provenance into a
small structured citation contract that clients can render safely.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
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


@dataclass
class EvidenceChunk:
    evidence_id: str
    title: str
    content: str
    source_url: str = ""
    source_key: str = ""
    section_path: list[str] = field(default_factory=list)
    scope: str = ""
    chunk_type: str = ""
    starts_mid_list: bool = False
    image_urls: list[str] = field(default_factory=list)


_RAW_URL_RE = re.compile(r"https?://[^\s<>)]+")
_MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\((\S+?)(?:\s+['\"][^'\"]*['\"])?\)")
_MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\([^)]*\)")
_MARKDOWN_HEADING_RE = re.compile(r"^\s*#{1,6}\s+(.+?)\s*#*\s*$")
_ORDERED_LIST_ITEM_RE = re.compile(r"^\s*(\d+)[.)]\s+")
_ORDERED_LIST_LINE_RE = re.compile(r"^(\s*)(\d+)([.)])(\s+.+)$")
_NUMERIC_MARKDOWN_CITATION_RE = re.compile(r"\[\s*\d{1,3}\s*\]\([^)]*\)")
_BARE_BRACKET_CITATION_RE = re.compile(r"(?<!!)\[\s*\d{1,3}\s*\]")
_PAREN_CITATION_RE = re.compile(r"\(\s*\d{1,3}(?:\s*[,;]\s*\d{1,3})*\s*\)")
_MALFORMED_NUMBER_URL_RE = re.compile(r"\b\d{1,3}\(https?://[^)\s]+\)")
_BARE_NUMBER_RUN_RE = re.compile(r"(?<![\w/])\b\d{1,3}(?:\s*[,;]\s*\d{1,3})+\b(?=(?:[.!?])?(?:\s|$))")
_TOKEN_RE = re.compile(r"[a-z0-9À-ÿ][a-z0-9À-ÿ_-]{2,}", re.IGNORECASE)
_SOURCE_HEADING_RE = re.compile(r"^\s*(?:bronnen?|sources?|references?)\s*:?\s*$", re.IGNORECASE)
_SOURCE_LIST_LINE_RE = re.compile(r"^\s*(?:\(\s*\d{1,3}\s*\)|\[\s*\d{1,3}\s*\]|\d{1,3}[.)])\s*(.+)$")
_DEFAULT_MAX_SOURCES = 3
_SOURCE_RELEVANCE_STOPWORDS = {
    "aan",
    "als",
    "and",
    "are",
    "bij",
    "but",
    "dat",
    "een",
    "for",
    "klai",
    "het",
    "met",
    "not",
    "the",
    "tot",
    "van",
    "voor",
    "with",
    "you",
}


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


def _metadata(chunk: dict) -> dict:
    metadata = chunk.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _string_value(value: object) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _split_section_path(value: object) -> list[str]:
    if isinstance(value, list):
        return [part.strip() for part in value if isinstance(part, str) and part.strip()]
    if not isinstance(value, str) or not value.strip():
        return []
    return [part.strip() for part in re.split(r"\s*>\s*|\s*/\s*", value) if part.strip()]


def _chunk_section_path(chunk: dict) -> list[str]:
    metadata = _metadata(chunk)
    for value in (
        chunk.get("section_path"),
        metadata.get("section_path"),
        chunk.get("heading_path"),
        metadata.get("heading_path"),
    ):
        section_path = _split_section_path(value)
        if section_path:
            return section_path
    return []


def _looks_like_injected_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped or len(stripped) > 180:
        return False
    if _ORDERED_LIST_ITEM_RE.match(stripped):
        return False
    if stripped[-1:] in ".!?:;":
        return False
    words = re.findall(r"[A-Za-zÀ-ÿ0-9_-]+", stripped)
    return bool(words) and len(words) <= 12


def _extract_section_and_content(text: str, section_path: list[str]) -> tuple[list[str], str]:
    stripped = (text or "").strip()
    if not stripped:
        return section_path, ""

    lines = stripped.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return section_path, ""

    first = lines[0].strip()
    heading_match = _MARKDOWN_HEADING_RE.match(first)
    if heading_match:
        if not section_path:
            section_path = _split_section_path(heading_match.group(1))
        return section_path, "\n".join(lines[1:]).strip()

    # The ingest chunker historically prepended "Heading > Subheading" as
    # plain text before a blank line. Preserve that as metadata and keep the
    # prompt content body-only so the model cannot echo it as answer Markdown.
    if len(lines) > 2 and not lines[1].strip():
        inferred_section_path = _split_section_path(first)
        matches_known_section = bool(section_path and inferred_section_path == section_path)
        looks_like_heading_prefix = ">" in first or _looks_like_injected_heading(first)
        if matches_known_section or (not section_path and looks_like_heading_prefix):
            if not section_path:
                section_path = inferred_section_path
            return section_path, "\n".join(lines[2:]).strip()

    return section_path, stripped


def _starts_mid_ordered_list(text: str) -> bool:
    for line in text.splitlines():
        if not line.strip():
            continue
        match = _ORDERED_LIST_ITEM_RE.match(line)
        return bool(match and int(match.group(1)) > 1)
    return False


def evidence_chunks_from_chunks(chunks: list[dict]) -> list[EvidenceChunk]:
    evidence: list[EvidenceChunk] = []
    for index, chunk in enumerate(chunks, 1):
        raw_text = chunk.get("text", "")
        if not isinstance(raw_text, str) or not raw_text.strip():
            continue
        section_path, content = _extract_section_and_content(raw_text, _chunk_section_path(chunk))
        if not content:
            continue
        source_url = _chunk_source_url(chunk)
        image_urls = [url for url in chunk.get("image_urls") or [] if isinstance(url, str) and url.strip()]
        evidence.append(
            EvidenceChunk(
                evidence_id=f"E{index}",
                title=_chunk_source_title(chunk),
                content=content,
                source_url=source_url,
                source_key=source_url_key(source_url),
                section_path=section_path,
                scope=_string_value(chunk.get("scope")),
                chunk_type=_string_value(chunk.get("chunk_type") or _metadata(chunk).get("chunk_type")),
                starts_mid_list=_starts_mid_ordered_list(content),
                image_urls=image_urls,
            )
        )
    return evidence


def render_evidence_context(
    chunks: list[dict],
    *,
    include_source_urls: bool = False,
    max_chars: int | None = None,
) -> str:
    parts: list[str] = []
    total_chars = 0
    for item in evidence_chunks_from_chunks(chunks):
        lines = [f"Evidence {item.evidence_id}", f"Source title: {item.title}"]
        if item.scope:
            lines.append(f"Scope: [{item.scope}]")
        if item.section_path:
            lines.append(f"Section path: {' > '.join(item.section_path)}")
        if item.chunk_type:
            lines.append(f"Chunk type: {item.chunk_type}")
        if item.starts_mid_list:
            lines.append(
                "List note: this excerpt starts mid ordered-list; do not preserve the original numbering in the answer."
            )
        if include_source_urls and item.source_url:
            lines.append(f"source_url: {item.source_url}")
        lines.append("Content:")
        lines.append(item.content)
        entry = "\n".join(lines)
        if max_chars is not None and total_chars + len(entry) > max_chars:
            break
        parts.append(entry)
        total_chars += len(entry)
    return "\n\n".join(parts)


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
        for item in evidence_chunks_from_chunks([chunk]):
            source.chunk_texts.append(item.content)
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


def _renumber_ordered_list_runs(text: str) -> str:
    """Renumber copied mid-document ordered-list excerpts to clean local lists."""
    lines = text.splitlines()
    output: list[str] = []
    run: list[str] = []

    def flush_run() -> None:
        nonlocal run
        if not run:
            return
        numbers = [int(match.group(2)) for line in run if (match := _ORDERED_LIST_LINE_RE.match(line))]
        expected = list(range(1, len(run) + 1))
        if len(run) == 1 and numbers and numbers[0] != 1:
            match = _ORDERED_LIST_LINE_RE.match(run[0])
            if match:
                output.append(f"{match.group(1)}{match.group(4).lstrip()}")
            else:
                output.extend(run)
        elif numbers and numbers != expected:
            for index, line in enumerate(run, 1):
                match = _ORDERED_LIST_LINE_RE.match(line)
                if match:
                    output.append(f"{match.group(1)}{index}{match.group(3)}{match.group(4)}")
                else:
                    output.append(line)
        else:
            output.extend(run)
        run = []

    for line in lines:
        if _ORDERED_LIST_LINE_RE.match(line):
            run.append(line)
            continue
        flush_run()
        output.append(line)
    flush_run()
    return "\n".join(output)


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
    cleaned = _renumber_ordered_list_runs(cleaned)
    for placeholder, image_markdown in image_placeholders.items():
        cleaned = cleaned.replace(placeholder, image_markdown)
    return cleaned.strip()


def _tokens(text: str) -> set[str]:
    return {
        token.lower()
        for token in _TOKEN_RE.findall(text)
        if not token.isdigit() and token.lower() not in _SOURCE_RELEVANCE_STOPWORDS
    }


def _source_relevance_score(answer_tokens: set[str], source: CitationSource) -> int:
    if not answer_tokens:
        return 0
    title_score = len(answer_tokens & _tokens(source.title)) * 2
    chunk_score = max(
        (len(answer_tokens & _tokens(chunk_text)) for chunk_text in source.chunk_texts),
        default=0,
    )
    return title_score + chunk_score


def _select_document_sources(
    cleaned_answer: str,
    sources: list[CitationSource],
    *,
    max_sources: int | None,
) -> list[CitationSource]:
    if max_sources is None:
        return sources
    if max_sources <= 0:
        return []

    answer_tokens = _tokens(cleaned_answer)
    scored = [
        (score, index, source)
        for index, source in enumerate(sources)
        if (score := _source_relevance_score(answer_tokens, source)) > 0
    ]
    if not scored:
        return sources[:1] if len(sources) == 1 else []

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [source for _, _, source in scored[:max_sources]]


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

    selected_sources = _select_document_sources(
        cleaned,
        sources,
        max_sources=max_sources,
    )
    rendered_sources = render_structured_sources(CitationRegistry(sources=selected_sources), max_sources=None)
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


def trusted_sources_from_evidence_pack(evidence_pack: object) -> list[dict[str, Any]]:
    """Return UI-renderable sources from an EvidencePack-like dict.

    This is the shared adapter for app surfaces that render document-level
    citations. It deliberately reads only ``evidence_pack.sources``; callers
    must not reconstruct visible citations from raw chunks when this list is
    empty or absent.
    """
    if not isinstance(evidence_pack, dict):
        return []
    sources = evidence_pack.get("sources")
    if not isinstance(sources, list):
        return []
    rendered: list[dict[str, Any]] = []
    for index, source in enumerate(sources, 1):
        if not isinstance(source, dict):
            continue
        url = normalise_source_url(source.get("source_url") or source.get("url"))
        if not url:
            continue
        rendered.append(
            {
                "label": str(index),
                "title": source.get("title") or "Source",
                "url": url,
                "source_id": source.get("source_id"),
                "evidence_ids": source.get("evidence_ids") or [],
                "artifact_id": source.get("artifact_id"),
                "source_label": source.get("source_label"),
                "relevance_score": source.get("relevance_score"),
            }
        )
    return rendered


def evidence_pack_items_as_chunks(evidence_pack: object) -> list[dict[str, Any]]:
    """Adapt EvidencePack items to the existing evidence-context renderer."""
    if not isinstance(evidence_pack, dict):
        return []
    items = evidence_pack.get("items")
    if not isinstance(items, list):
        return []
    chunks: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        chunks.append(
            {
                "chunk_id": item.get("chunk_id"),
                "artifact_id": item.get("artifact_id"),
                "content_type": item.get("content_type"),
                "text": item.get("text"),
                "title": item.get("title"),
                "heading_path": item.get("heading_path"),
                "source_url": item.get("source_url"),
                "source_label": item.get("source_label"),
                "score": item.get("score"),
                "reranker_score": item.get("reranker_score"),
                "final_score": item.get("final_score"),
                "scope": item.get("scope"),
                "image_urls": item.get("image_urls"),
                "is_parent_text": item.get("is_parent_text"),
            }
        )
    return chunks


def compose_answer_with_trusted_sources(
    text: str,
    trusted_sources: list[dict[str, Any]],
    *,
    allowed_image_urls: set[str] | None = None,
) -> ComposedCitations:
    """Clean model text and attach already-selected document sources.

    Unlike ``compose_citations()``, this function never chooses sources from
    answer overlap or raw chunks. Source selection must already have happened
    in the EvidencePack contract.
    """
    cleaned = strip_model_citation_artifacts(text, allowed_image_urls=allowed_image_urls)
    sources = [
        {"label": str(index), "title": title, "url": url}
        for index, source in enumerate(trusted_sources, 1)
        if (url := normalise_source_url(source.get("url")))
        for title in [str(source.get("title") or "Source")]
    ]
    return ComposedCitations(content=cleaned, sources=sources)


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
    "EvidenceChunk",
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
