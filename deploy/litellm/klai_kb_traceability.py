"""Knowledge-base traceability helpers for LiteLLM KB chat metadata."""

from __future__ import annotations

from typing import Any, Iterable

from klai_kb_urls import chunk_source_url, normalise_guard_url


def non_empty_string(value: object) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def dedupe_strings(values: Iterable[object]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = non_empty_string(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def chunk_kb_label(chunk: dict[str, Any]) -> str:
    """Best-effort KB label for traceability; never used as a citation source."""
    metadata = chunk.get("metadata")
    candidates: list[object] = [
        chunk.get("kb_slug"),
        chunk.get("knowledge_base_slug"),
        chunk.get("knowledge_base_name"),
        chunk.get("collection_slug"),
        chunk.get("collection_name"),
    ]
    if isinstance(metadata, dict):
        candidates.extend(
            [
                metadata.get("kb_slug"),
                metadata.get("knowledge_base_slug"),
                metadata.get("knowledge_base_name"),
                metadata.get("collection_slug"),
                metadata.get("collection_name"),
            ]
        )
    for candidate in candidates:
        label = non_empty_string(candidate)
        if label:
            return label
    source_label = non_empty_string(chunk.get("source_label"))
    if source_label.startswith("personal-"):
        return source_label
    return ""


def kb_labels_from_chunks(chunks: object) -> list[str]:
    if not isinstance(chunks, list):
        return []
    return dedupe_strings(
        chunk_kb_label(chunk) for chunk in chunks if isinstance(chunk, dict)
    )


def kb_scope_mode(*, scope: str, kb_slugs_for_request: list[str] | None) -> str:
    if scope == "personal":
        return "personal"
    if kb_slugs_for_request:
        return "explicit_org_and_personal" if scope == "both" else "explicit_org"
    if scope == "both":
        return "all_org_and_personal"
    if scope == "org":
        return "all_org"
    return scope


def _source_key(url: object) -> str:
    normalised = normalise_guard_url(url)
    return normalised.rstrip("/") or normalised


def kb_labels_used_by_sources(
    trusted_sources: list[dict[str, Any]],
    evidence_pack: object,
    raw_chunks: list[dict[str, Any]],
) -> list[str]:
    if not trusted_sources or not raw_chunks:
        return []

    chunks = [chunk for chunk in raw_chunks if isinstance(chunk, dict)]
    chunk_by_id = {
        str(chunk_id): chunk
        for chunk in chunks
        if isinstance((chunk_id := chunk.get("chunk_id")), str | int)
        and str(chunk_id)
    }
    evidence_item_by_id: dict[str, dict[str, Any]] = {}
    if isinstance(evidence_pack, dict):
        items = evidence_pack.get("items")
        if isinstance(items, list):
            evidence_item_by_id = {
                str(evidence_id): item
                for item in items
                if isinstance(item, dict)
                and isinstance((evidence_id := item.get("evidence_id")), str | int)
                and str(evidence_id)
            }

    labels: list[str] = []
    for source in trusted_sources:
        source_labels: list[str] = []
        evidence_ids = source.get("evidence_ids")
        source_evidence_ids = (
            [
                str(evidence_id)
                for evidence_id in evidence_ids
                if isinstance(evidence_id, str | int) and str(evidence_id)
            ]
            if isinstance(evidence_ids, list)
            else []
        )
        if source_evidence_ids:
            for evidence_id in source_evidence_ids:
                item = evidence_item_by_id.get(str(evidence_id))
                if not item:
                    continue
                chunk = chunk_by_id.get(str(item.get("chunk_id")))
                if chunk:
                    source_labels.append(chunk_kb_label(chunk))
            labels.extend(source_labels)
            continue
        source_key = _source_key(source.get("url") or source.get("source_url"))
        if not source_key:
            continue
        for chunk in chunks:
            if _source_key(chunk_source_url(chunk)) == source_key:
                labels.append(chunk_kb_label(chunk))

    return dedupe_strings(labels)
