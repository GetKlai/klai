"""Shared policy for deciding when LLM enrichment should run."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from knowledge_ingest.config import settings

_DEFAULT_MAX_CHUNKS = 200


def _configured_max_chunks() -> int:
    value = getattr(settings, "enrichment_max_chunks", _DEFAULT_MAX_CHUNKS)
    return value if isinstance(value, int) else _DEFAULT_MAX_CHUNKS


def enrichment_skip_reason(
    *,
    chunk_count: int,
    extra_payload: Mapping[str, Any] | None,
) -> str | None:
    """Return a machine-readable reason when LLM enrichment should be skipped."""
    extra = extra_payload or {}
    max_chunks = _configured_max_chunks()
    if extra.get("document_text_truncated"):
        docling_chunk_count = extra.get("docling_chunk_count")
        if docling_chunk_count is None:
            return "document_text_truncated"
        try:
            truncated_chunk_count = int(docling_chunk_count)
        except (TypeError, ValueError):
            return "document_text_truncated"
        if max_chunks > 0 and truncated_chunk_count > max_chunks:
            return "document_text_truncated"

    if max_chunks > 0 and chunk_count > max_chunks:
        return "too_many_chunks"

    return None
