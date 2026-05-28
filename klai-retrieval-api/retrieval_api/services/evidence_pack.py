"""Build the deterministic evidence contract used for KB citations."""

from __future__ import annotations

from typing import Any

from klai_citations import normalise_source_url, source_url_key

from retrieval_api.models import ChunkResult, EvidenceItem, EvidencePack, EvidenceSource

_DEFAULT_MAX_SOURCES = 3


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


def _relevance_score(chunk: ChunkResult | dict[str, Any]) -> float | None:
    for key in ("final_score", "reranker_score"):
        value = _chunk_value(chunk, key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


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


def build_evidence_pack(
    chunks: list[ChunkResult | dict[str, Any]],
    *,
    query: str | None = None,
    max_sources: int = _DEFAULT_MAX_SOURCES,
    min_relevance_score: float | None = None,
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
    below_threshold_count = 0
    for chunk in chunks:
        source_url = _source_url(chunk)
        # Derive a stable source_key. PUBLIC sources (web crawl, MS-Docs,
        # Notion) carry a real URL — use the URL's normalised key. PRIVATE
        # uploads (PDFs, plain-text snippets a user pastes into their
        # personal KB) have NO public URL; their chunks land in qdrant
        # with ``source_url=None``. Before 2026-05-27 those chunks were
        # filtered out entirely below ("if not source_url: continue"),
        # which meant a user's CV / contract / handbook could be
        # retrieved by the reranker but disappeared from the evidence
        # pack — and therefore from the citation list — leaving the
        # model answering "Dat staat niet in de kennisbank" from chunks
        # that explicitly mention the requested content. Fall back to
        # the chunk's ``artifact_id`` (always present for uploads): all
        # chunks from the same PDF share one artifact and therefore
        # collapse into one source in the citations panel.
        artifact_id = _string_value(_chunk_value(chunk, "artifact_id"))
        if source_url:
            source_key = source_url_key(source_url)
        elif artifact_id:
            source_key = f"artifact:{artifact_id}"
        else:
            source_key = ""
        title = _title(chunk, source_url)
        if not source_key:
            continue
        relevance_score = _relevance_score(chunk)
        if (
            min_relevance_score is not None
            and relevance_score is not None
            and relevance_score < min_relevance_score
        ):
            below_threshold_count += 1
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
        if below_threshold_count:
            reason = "below_relevance_threshold"
        else:
            reason = "no_citable_sources"
        return EvidencePack(no_citable_reason=reason)

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


def evidence_pack_sources_payload(pack: EvidencePack) -> list[dict[str, object]]:
    """Return the structured source shape used by chat/portal clients.

    Includes sources without a ``source_url`` (uploaded documents — PDFs,
    pasted text — that have no public address). The chat client decides
    how to render a URL-less source: typically as a non-clickable label
    showing the filename / artifact title, with the option to open it via
    the portal's KB browse view. Filtering URL-less sources out here would
    re-introduce the 2026-05-27 bug where a user's uploaded CV was
    retrieved but never appeared in the citations panel.
    """
    payload: list[dict[str, object]] = []
    for index, source in enumerate(pack.sources, 1):
        if not source.source_url and not source.artifact_id:
            # No URL and no artifact: nothing addressable to cite.
            continue
        payload.append(
            {
                "label": str(index),
                "title": source.title,
                "url": source.source_url,
                "source_id": source.source_id,
                "evidence_ids": source.evidence_ids,
                "artifact_id": source.artifact_id,
                "source_label": source.source_label,
                "relevance_score": source.relevance_score,
            }
        )
    return payload


def evidence_pack_items_as_chunks(pack: EvidencePack) -> list[dict[str, Any]]:
    """Adapt EvidencePack items to the existing evidence-context renderer."""
    return [
        {
            "chunk_id": item.chunk_id,
            "artifact_id": item.artifact_id,
            "content_type": item.content_type,
            "text": item.text,
            "title": item.title,
            "heading_path": item.heading_path,
            "source_url": item.source_url,
            "source_label": item.source_label,
            "score": item.score,
            "reranker_score": item.reranker_score,
            "final_score": item.final_score,
            "scope": item.scope,
            "image_urls": item.image_urls,
            "is_parent_text": item.is_parent_text,
        }
        for item in pack.items
    ]
