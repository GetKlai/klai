"""Evidence-pack / trusted-source safety filtering for path-A KB chat.

Extracted verbatim from ``klai_knowledge.py`` (behaviour-preserving). These are
pure functions over retrieval data — no env reads, no logger, no module-load
side effects — so they live cleanly on their own and are independently
testable. ``klai_knowledge`` re-imports them under their underscore-prefixed
names, so existing call sites are unchanged.

When the LLM-safety layer drops one or more context chunks (prompt-injection /
jailbreak content), the trusted-source list and the EvidencePack must be
re-narrowed to the surviving chunks so a dropped chunk can never still surface
as a citation.
"""

from __future__ import annotations

from typing import Any

from klai_kb_urls import (
    chunk_source_url as _chunk_source_url,
    normalise_guard_url as _normalise_guard_url,
)


def filter_trusted_sources_for_chunks(
    trusted_sources: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not trusted_sources or not chunks:
        return []

    safe_evidence_ids = {
        str(evidence_id)
        for evidence_id in (chunk.get("evidence_id") for chunk in chunks)
        if isinstance(evidence_id, str | int) and str(evidence_id)
    }
    safe_source_keys = {_source_key(_chunk_source_url(chunk)) for chunk in chunks}
    safe_source_keys.discard("")
    if not safe_evidence_ids and not safe_source_keys:
        return []

    filtered: list[dict[str, Any]] = []
    for source in trusted_sources:
        evidence_ids = source.get("evidence_ids")
        source_evidence_ids = (
            {
                str(evidence_id)
                for evidence_id in evidence_ids
                if isinstance(evidence_id, str | int)
            }
            if isinstance(evidence_ids, list)
            else set()
        )
        source_key = _source_key(source.get("url") or source.get("source_url"))
        if (
            source_evidence_ids.intersection(safe_evidence_ids)
            or source_key in safe_source_keys
        ):
            filtered.append(source)
    return filtered


def _source_key(url: object) -> str:
    normalised = _normalise_guard_url(url)
    return normalised.rstrip("/") or normalised


def filter_evidence_pack_for_chunks(
    evidence_pack: object,
    chunks: list[dict[str, Any]],
) -> object:
    if not isinstance(evidence_pack, dict):
        return evidence_pack

    safe_evidence_ids = {
        str(evidence_id)
        for evidence_id in (chunk.get("evidence_id") for chunk in chunks)
        if isinstance(evidence_id, str | int) and str(evidence_id)
    }
    safe_chunk_ids = {
        str(chunk_id)
        for chunk_id in (chunk.get("chunk_id") for chunk in chunks)
        if isinstance(chunk_id, str | int) and str(chunk_id)
    }
    safe_source_keys = {_source_key(_chunk_source_url(chunk)) for chunk in chunks}
    safe_source_keys.discard("")

    filtered = dict(evidence_pack)
    items = evidence_pack.get("items")
    if isinstance(items, list):
        filtered["items"] = [
            item
            for item in items
            if isinstance(item, dict)
            and (
                str(item.get("evidence_id")) in safe_evidence_ids
                or str(item.get("chunk_id")) in safe_chunk_ids
            )
        ]

    sources = evidence_pack.get("sources")
    if isinstance(sources, list):
        filtered["sources"] = [
            source
            for source in sources
            if isinstance(source, dict)
            and (
                {
                    str(evidence_id)
                    for evidence_id in (source.get("evidence_ids") or [])
                    if isinstance(evidence_id, str | int)
                }.intersection(safe_evidence_ids)
                or _source_key(source.get("source_url") or source.get("url"))
                in safe_source_keys
            )
        ]
        if not filtered["sources"] and filtered.get("no_citable_reason") is None:
            filtered["no_citable_reason"] = "safety_filtered_all_sources"

    return filtered
