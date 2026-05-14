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


_RAW_URL_RE = re.compile(r"https?://[^\s<>)]+")
_NUMERIC_MARKDOWN_CITATION_RE = re.compile(r"\[\s*\d{1,3}\s*\]\([^)]*\)")
_BARE_BRACKET_CITATION_RE = re.compile(r"(?<!!)\[\s*\d{1,3}\s*\]")
_PAREN_CITATION_RE = re.compile(r"\(\s*\d{1,3}(?:\s*[,;]\s*\d{1,3})*\s*\)")
_MALFORMED_NUMBER_URL_RE = re.compile(r"\b\d{1,3}\(https?://[^)\s]+\)")
_BARE_NUMBER_RUN_RE = re.compile(r"(?<![\w/])\b\d{1,3}(?:\s*[,;]\s*\d{1,3})+\b(?=(?:[.!?])?(?:\s|$))")
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_-]{2,}", re.IGNORECASE)
_SOURCE_HEADING_RE = re.compile(r"^\s*(?:bronnen?|sources?|references?)\s*:?\s*$", re.IGNORECASE)
_SOURCE_LIST_LINE_RE = re.compile(r"^\s*(?:\(\s*\d{1,3}\s*\)|\[\s*\d{1,3}\s*\]|\d{1,3}[.)])\s*(.+)$")
_TRAILING_PUNCT_RE = re.compile(r"([.!?:;])(\s*)$")


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
    ):
        if normalised := normalise_source_url(candidate):
            return normalised

    metadata = chunk.get("metadata")
    if isinstance(metadata, dict):
        for key in ("source_url", "url", "sourceUrl", "canonical_url", "page_url"):
            if normalised := normalise_source_url(metadata.get(key)):
                return normalised

    source = chunk.get("source")
    if isinstance(source, dict):
        if normalised := normalise_source_url(source.get("url") or source.get("source_url")):
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


def strip_model_citation_artifacts(text: str) -> str:
    """Remove model-authored citations/source lists before composing our own."""
    kept_lines: list[str] = []
    skipping_source_section = False
    for line in text.splitlines():
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
    cleaned = _BARE_BRACKET_CITATION_RE.sub("", cleaned)
    cleaned = _PAREN_CITATION_RE.sub("", cleaned)
    cleaned = _BARE_NUMBER_RUN_RE.sub("", cleaned)
    cleaned = _RAW_URL_RE.sub("", cleaned)
    cleaned = re.sub(r"\s+\n", "\n", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"[ \t]+([.,;:!?])", r"\1", cleaned)
    cleaned = re.sub(r"\(\s*\)", "", cleaned)
    return cleaned.strip()


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN_RE.findall(text)}


def _segment_score(segment_tokens: set[str], source: CitationSource) -> int:
    if not segment_tokens:
        return 0
    source_tokens = _tokens(" ".join([source.title, *source.chunk_texts]))
    return len(segment_tokens & source_tokens)


def _add_marker(segment: str, labels: list[str]) -> str:
    marker = f" ({','.join(labels)})"
    match = _TRAILING_PUNCT_RE.search(segment)
    if match:
        return f"{segment[: match.start(1)].rstrip()}{marker}{match.group(1)}{match.group(2)}"
    return f"{segment.rstrip()}{marker}"


def compose_citations(
    text: str,
    chunks: list[dict],
    *,
    max_sources: int = 3,
    max_sources_per_segment: int = 2,
) -> ComposedCitations:
    cleaned = strip_model_citation_artifacts(text)
    sources = citation_sources_from_chunks(chunks)
    if not cleaned or not sources:
        return ComposedCitations(content=cleaned, sources=[])

    used_labels_by_key: dict[str, str] = {}
    used_order: list[CitationSource] = []
    output_lines: list[str] = []
    has_cited_any_segment = False

    for line in cleaned.splitlines():
        stripped = line.strip()
        if not stripped:
            output_lines.append(line)
            continue

        segment_tokens = _tokens(stripped)
        scored = [
            (score, source)
            for source in sources
            if source.key not in used_labels_by_key and (score := _segment_score(segment_tokens, source)) > 0
        ]
        scored.sort(key=lambda item: item[0], reverse=True)
        candidates = [source for _, source in scored[:max_sources_per_segment]]

        if not candidates and not has_cited_any_segment:
            candidates = [source for source in sources if source.key not in used_labels_by_key][:1]

        labels: list[str] = []
        for source in candidates:
            if len(used_order) >= max_sources:
                break
            label = str(len(used_order) + 1)
            used_labels_by_key[source.key] = label
            used_order.append(source)
            labels.append(label)

        if labels:
            output_lines.append(_add_marker(line, labels))
            has_cited_any_segment = True
        else:
            output_lines.append(line)

    rendered_sources = [
        {"label": str(index), "title": source.title, "url": source.url} for index, source in enumerate(used_order, 1)
    ]
    return ComposedCitations(content="\n".join(output_lines), sources=rendered_sources)
