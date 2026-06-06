"""Shared URL guards for LiteLLM KB prompt and citation paths."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse, urlunparse

_SENTINEL_URLS = {"undefined", "null", "none", "n/a", "na", "-", "#"}


def normalise_guard_url(url: object) -> str:
    if not isinstance(url, str):
        return ""
    value = url.strip().strip("<>")
    if not value or value.lower() in _SENTINEL_URLS:
        return ""
    if value.startswith("/"):
        return value

    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""

    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parsed.path or "/"
    return urlunparse((parsed.scheme.lower(), netloc, path, "", parsed.query, ""))


def chunk_source_url(chunk: dict[str, Any]) -> str:
    candidates: list[object] = [
        chunk.get("source_url"),
        chunk.get("sourceUrl"),
        chunk.get("canonical_url"),
        chunk.get("page_url"),
        chunk.get("url"),
    ]
    metadata = chunk.get("metadata")
    if isinstance(metadata, dict):
        candidates.extend(
            [
                metadata.get("source_url"),
                metadata.get("sourceUrl"),
                metadata.get("canonical_url"),
                metadata.get("page_url"),
                metadata.get("url"),
            ]
        )
    source = chunk.get("source")
    if isinstance(source, dict):
        candidates.extend(
            [
                source.get("source_url"),
                source.get("url"),
                source.get("href"),
            ]
        )

    for candidate in candidates:
        normalised = normalise_guard_url(candidate)
        if normalised and not normalised.startswith("/"):
            return normalised
    return ""


def absolute_image_url(url: object, *, images_base_url: str) -> str:
    normalised = normalise_guard_url(url)
    if not normalised:
        return ""
    return f"{images_base_url}{normalised}" if normalised.startswith("/") else normalised
