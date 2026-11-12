"""Build the deterministic evidence contract used for KB citations."""

from __future__ import annotations

from typing import Any

from klai_citations import normalise_source_url, source_url_key

from retrieval_api.models import ChunkResult, EvidenceItem, EvidencePack, EvidenceSource

DEFAULT_MAX_SOURCES = 3
MULTI_KB_MAX_SOURCES = 5


def _chunk_value(chunk: ChunkResult | dict[str, Any], key: str) -> Any:
    if isinstance(chunk, ChunkResult):
        return getattr(chunk, key, None)
    value = chunk.get(key)
    if value is not None:
        return value
    metadata = chunk.get("metadata")
    if isinstance(metadata, dict):
        return metadata.get(key)
    return None


def _string_value(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _source_url(chunk: ChunkResult | dict[str, Any]) -> str | None:
    candidates = [
        _chunk_value(chunk, "source_url"),
        _chunk_value(chunk, "sourceUrl"),
        _chunk_value(chunk, "canonical_url"),
        _chunk_value(chunk, "page_url"),
        _chunk_value(chunk, "url"),
    ]
    if isinstance(chunk, dict):
        source = chunk.get("source")
        if isinstance(source, dict):
            candidates.extend([source.get("source_url"), source.get("url"), source.get("href")])
    for candidate in candidates:
        normalised = normalise_source_url(candidate)
        if normalised:
            return normalised
    return None


def _title(chunk: ChunkResult | dict[str, Any], source_url: str | None) -> str:
    for key in ("original_filename", "filename", "title", "source_label", "context_prefix"):
        value = _string_value(_chunk_value(chunk, key))
        if value and not value.startswith("file:sha256:"):
            return value[:160]
    if source_url:
        return source_url
    text = _string_value(_chunk_value(chunk, "text"))
    if text:
        return text[:80]
    return "Source"


def _score(chunk: ChunkResult | dict[str, Any]) -> float:
    for key in ("final_score", "reranker_score", "score"):
        value = _chunk_value(chunk, key)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def _optional_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _image_urls(chunk: ChunkResult | dict[str, Any]) -> list[str] | None:
    value = _chunk_value(chunk, "image_urls")
    if not isinstance(value, list):
        return None
    urls = [url for url in value if isinstance(url, str) and url.strip()]
    return urls or None


def _make_item(
    chunk: ChunkResult | dict[str, Any],
    *,
    evidence_id: str,
    source_url: str | None,
    title: str,
) -> EvidenceItem | None:
    text = _string_value(_chunk_value(chunk, "text"))
    chunk_id = _string_value(_chunk_value(chunk, "chunk_id"))
    if not text or not chunk_id:
        return None
    return EvidenceItem(
        evidence_id=evidence_id,
        chunk_id=chunk_id,
        artifact_id=_string_value(_chunk_value(chunk, "artifact_id")),
        content_type=_string_value(_chunk_value(chunk, "content_type")),
        text=text,
        title=title,
        heading_path=_string_value(_chunk_value(chunk, "heading_path")),
        source_url=source_url,
        source_label=_string_value(_chunk_value(chunk, "source_label")),
        score=float(_chunk_value(chunk, "score") or 0.0),
        reranker_score=_optional_float(_chunk_value(chunk, "reranker_score")),
        final_score=_optional_float(_chunk_value(chunk, "final_score")),
        scope=_string_value(_chunk_value(chunk, "scope")),
        image_urls=_image_urls(chunk),
        is_parent_text=bool(_chunk_value(chunk, "is_parent_text")),
    )


def chunk_source_key(chunk: ChunkResult | dict[str, Any]) -> str:
    """Stable dedup key for a chunk's source.

    PUBLIC sources (web crawl, MS-Docs, Notion) key on the normalised URL;
    PRIVATE uploads (no public URL) fall back to ``artifact:<id>`` so all
    chunks of one document collapse into one source. This is THE key
    ``build_evidence_pack`` assigns source slots with — external callers
    (e.g. the ranking-contract shadow snapshot in retrieve.py) reuse it so
    their "first N distinct sources" projection can never drift from the
    pack's own assembly.
    """
    source_url = _source_url(chunk)
    if source_url:
        return source_url_key(source_url)
    artifact_id = _string_value(_chunk_value(chunk, "artifact_id"))
    if artifact_id:
        return f"artifact:{artifact_id}"
    return ""


def build_evidence_pack(
    chunks: list[ChunkResult | dict[str, Any]],
    *,
    query: str | None = None,
    max_sources: int = DEFAULT_MAX_SOURCES,
) -> EvidencePack:
    """Return the citable evidence and source set for a retrieval result.

    This is a projection of retrieval output, not a second relevance ranker.
    Retrieval/reranking decide which chunks are relevant; EvidencePack only
    normalizes citable source metadata, deduplicates URLs, and limits the
    source list rendered to users.
    """
    if not chunks:
        return EvidencePack(no_citable_reason="no_evidence")

    candidates: list[tuple[EvidenceItem, str, str | None, float]] = []
    for chunk in chunks:
        source_url = _source_url(chunk)
        # Stable source_key via chunk_source_key: URL key for public
        # sources, ``artifact:<id>`` fallback for private uploads. The
        # fallback exists because before 2026-05-27 URL-less chunks were
        # filtered out entirely ("if not source_url: continue"), which
        # meant a user's CV / contract / handbook could be retrieved by
        # the reranker but disappeared from the evidence pack — and
        # therefore from the citation list — leaving the model answering
        # "Dat staat niet in de kennisbank" from chunks that explicitly
        # mention the requested content.
        source_key = chunk_source_key(chunk)
        title = _title(chunk, source_url)
        if not source_key:
            continue
        item = _make_item(
            chunk,
            evidence_id=f"E{len(candidates) + 1}",
            source_url=source_url,
            title=title,
        )
        if item is None:
            continue
        candidates.append((item, source_key, source_url, _score(chunk)))

    if not candidates:
        return EvidencePack(no_citable_reason="no_citable_sources")

    source_order: list[str] = []
    source_meta: dict[str, dict[str, Any]] = {}
    for item, source_key, source_url, score in candidates:
        meta = source_meta.get(source_key)
        if meta is None:
            if len(source_order) >= max_sources:
                continue
            source_order.append(source_key)
            meta = {
                "source_id": f"S{len(source_order)}",
                "title": item.title or "Source",
                "source_url": source_url,
                "artifact_id": item.artifact_id,
                "source_label": item.source_label,
                "evidence_ids": [],
                "relevance_score": score,
            }
            source_meta[source_key] = meta
        meta["evidence_ids"].append(item.evidence_id)
        meta["relevance_score"] = max(float(meta["relevance_score"]), score)

    selected_keys = set(source_order)
    selected_items = [
        item
        for item, source_key, _source_url, _score_value in candidates
        if source_key in selected_keys
    ]
    sources = [
        EvidenceSource(
            source_id=meta["source_id"],
            title=meta["title"],
            source_url=meta["source_url"],
            artifact_id=meta["artifact_id"],
            source_label=meta["source_label"],
            evidence_ids=meta["evidence_ids"],
            relevance_score=meta["relevance_score"],
        )
        for key in source_order
        if (meta := source_meta.get(key)) is not None
    ]
    no_citable_reason = None if sources else "no_citable_sources"
    return EvidencePack(items=selected_items, sources=sources, no_citable_reason=no_citable_reason)


def merge_evidence_packs(
    indexed_packs: list[tuple[int, EvidencePack | None]],
) -> EvidencePack:
    """Merge per-sub-question evidence packs into one referentially-intact pack.

    Evidence and source ids are namespaced per sub-question (``E1`` of
    sub-question 2 becomes ``Q2E1``) so ids never collide across packs, and
    every item is tagged with its ``sub_query_index`` so prompt builders can
    group evidence per question. Sources pointing at the same document (same
    ``source_url`` / artifact key) are deduplicated across sub-questions with
    their evidence ids combined.
    """
    merged_items: list[EvidenceItem] = []
    merged_sources: list[EvidenceSource] = []
    source_by_key: dict[str, EvidenceSource] = {}

    for index, pack in indexed_packs:
        if pack is None:
            continue
        id_map: dict[str, str] = {}
        for item in pack.items:
            namespaced = f"Q{index}{item.evidence_id}"
            id_map[item.evidence_id] = namespaced
            merged_items.append(
                item.model_copy(update={"evidence_id": namespaced, "sub_query_index": index})
            )
        for source in pack.sources:
            mapped_ids = [
                id_map[evidence_id] for evidence_id in source.evidence_ids if evidence_id in id_map
            ]
            if not mapped_ids:
                continue
            key = source.source_url or (
                f"artifact:{source.artifact_id}" if source.artifact_id else source.title
            )
            existing = source_by_key.get(key)
            if existing is None:
                merged = source.model_copy(
                    update={
                        "source_id": f"Q{index}{source.source_id}",
                        "evidence_ids": mapped_ids,
                    }
                )
                source_by_key[key] = merged
                merged_sources.append(merged)
            else:
                existing.evidence_ids.extend(
                    evidence_id
                    for evidence_id in mapped_ids
                    if evidence_id not in existing.evidence_ids
                )
                existing.relevance_score = max(existing.relevance_score, source.relevance_score)

    if not merged_items:
        return EvidencePack(no_citable_reason="no_evidence")
    return EvidencePack(
        items=merged_items,
        sources=merged_sources,
        no_citable_reason=None if merged_sources else "no_citable_sources",
    )
