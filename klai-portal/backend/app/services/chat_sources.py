"""Source URLs, titles and context text taken from retrieved chunks, and the trusted-source filter."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse, urlunparse

PageContext = dict[str, Any]

_PAGE_CONTEXT_MAX_CHARS = {
    "url": 2048,
    "path": 512,
    "title": 512,
    "referrer": 2048,
    "excerpt": 2000,
}


def _normalise_guard_url(url: object) -> str:
    if not isinstance(url, str):
        return ""
    value = url.strip().strip("<>")
    if value.lower() in {"", "undefined", "null", "none"}:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    hostname = (parsed.hostname or "").lower()
    if hostname in {"undefined", "null", "none"}:
        return ""
    placeholder_path = (parsed.path or "").strip("/").lower()
    if placeholder_path in {"undefined", "null", "none"}:
        return ""
    return urlunparse((parsed.scheme.lower(), netloc, parsed.path or "/", "", parsed.query, ""))


def _chunk_source_url(chunk: dict) -> str:
    candidates = (
        chunk.get("source_url"),
        chunk.get("url"),
        chunk.get("sourceUrl"),
        chunk.get("canonical_url"),
        chunk.get("page_url"),
        chunk.get("source_ref"),
    )
    for candidate in candidates:
        normalised = _normalise_guard_url(candidate)
        if normalised:
            return normalised

    metadata = chunk.get("metadata")
    if isinstance(metadata, dict):
        for key in ("source_url", "url", "sourceUrl", "canonical_url", "page_url", "source_ref"):
            normalised = _normalise_guard_url(metadata.get(key))
            if normalised:
                return normalised

    source = chunk.get("source")
    if isinstance(source, dict):
        for key in ("url", "source_url", "href"):
            normalised = _normalise_guard_url(source.get(key))
            if normalised:
                return normalised

    return ""


def _source_urls_from_chunks(chunks: list[dict]) -> set[str]:
    return {normalised for normalised in (_chunk_source_url(chunk) for chunk in chunks) if normalised}


def _context_text_from_page_context(page_context: PageContext | None) -> str:
    if not page_context:
        return ""
    return "\n".join(str(page_context[key]) for key in _PAGE_CONTEXT_MAX_CHARS if key in page_context)


def _context_text_from_chunk(chunk: dict) -> str:
    values: list[str] = []
    for key in ("title", "heading_path", "source_label", "text"):
        value = chunk.get(key)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(item for item in value if isinstance(item, str))
    return "\n".join(values)


def _filter_trusted_sources_for_chunks(
    trusted_sources: list[dict[str, Any]],
    chunks: list[dict],
) -> list[dict[str, Any]]:
    if not trusted_sources or not chunks:
        return []

    safe_evidence_ids = {
        evidence_id
        for evidence_id in (chunk.get("evidence_id") for chunk in chunks)
        if isinstance(evidence_id, str) and evidence_id
    }
    safe_source_urls = _source_urls_from_chunks(chunks)
    if not safe_evidence_ids and not safe_source_urls:
        return []

    filtered: list[dict[str, Any]] = []
    for source in trusted_sources:
        evidence_ids = source.get("evidence_ids")
        source_evidence_ids = (
            {evidence_id for evidence_id in evidence_ids if isinstance(evidence_id, str)}
            if isinstance(evidence_ids, list)
            else set()
        )
        source_url = _normalise_guard_url(source.get("url"))
        if source_evidence_ids.intersection(safe_evidence_ids) or source_url in safe_source_urls:
            filtered.append(source)
    return filtered


def _chunk_source_title(chunk: dict) -> str:
    candidates = (
        chunk.get("title"),
        (chunk.get("metadata") or {}).get("title") if isinstance(chunk.get("metadata"), dict) else None,
        chunk.get("source_label"),
    )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return "Source"
