"""
Qdrant operations for the knowledge graph.

Single collection: klai_knowledge
  - vector_chunk (dense): enriched chunk text embedding
  - vector_questions (dense): HyPE question embedding (depth-dependent)
  - vector_sparse (sparse): BM25-style lexical matching via BGE-M3
Tenant isolation via org_id payload filter.
"""

import asyncio
import time
import uuid
import warnings

import structlog

# Qdrant client warns about API key over HTTP; safe inside Docker network
warnings.filterwarnings("ignore", message="Api key is used with an insecure connection")

from qdrant_client import AsyncQdrantClient  # noqa: E402
from qdrant_client.models import (  # noqa: E402
    Distance,
    FieldCondition,
    Filter,
    Fusion,
    FusionQuery,
    MatchAny,
    MatchValue,
    PointStruct,
    Prefetch,
    SparseIndexParams,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

from knowledge_ingest.config import settings  # noqa: E402
from knowledge_ingest.embedder import EMBED_DIM  # noqa: E402

logger = structlog.get_logger()

COLLECTION = "klai_knowledge"

_client: AsyncQdrantClient | None = None


def get_client() -> AsyncQdrantClient:
    global _client
    if _client is None:
        _client = AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key or None,
        )
    return _client


async def ensure_collection() -> None:
    """Ensure the klai_knowledge collection exists with named + sparse vectors."""
    client = get_client()
    existing = [c.name for c in (await client.get_collections()).collections]

    if COLLECTION not in existing:
        await client.create_collection(
            COLLECTION,
            vectors_config={
                "vector_chunk": VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
                "vector_questions": VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
            },
            sparse_vectors_config={
                "vector_sparse": SparseVectorParams(
                    index=SparseIndexParams(on_disk=False),
                ),
            },
        )
        logger.info("qdrant_collection_created", collection=COLLECTION)

    # Always ensure payload indexes exist — idempotent on existing collections.
    # This handles newly added fields (e.g. user_id) on pre-existing collections.
    collection_info = await client.get_collection(COLLECTION)
    indexed_fields = set((collection_info.payload_schema or {}).keys())
    for field in (
        "org_id",
        "kb_slug",
        "artifact_id",
        "content_type",
        "user_id",
        "entity_uuids",
        "entity_names",
        "taxonomy_node_id",
        "source_connector_id",
        "taxonomy_node_ids",
        "tags",
        "content_label",
        "source_label",
        "chunk_type",
        "heading_path",
    ):
        if field not in indexed_fields:
            await client.create_payload_index(
                COLLECTION,
                field_name=field,
                field_schema="keyword",
            )
            logger.info("qdrant_payload_index_created", field=field, collection=COLLECTION)

    # source_url: keyword index for payload-filter-based chunk lookup (SPEC-CRAWLER-003)
    if "source_url" not in indexed_fields:
        await client.create_payload_index(
            COLLECTION,
            field_name="source_url",
            field_schema="keyword",
        )
        logger.info("qdrant_payload_index_created", field="source_url", collection=COLLECTION)

    # incoming_link_count: integer index for authority boost queries (SPEC-CRAWLER-003)
    if "incoming_link_count" not in indexed_fields:
        await client.create_payload_index(
            COLLECTION,
            field_name="incoming_link_count",
            field_schema="integer",
        )
        logger.info(
            "qdrant_payload_index_created",
            field="incoming_link_count",
            collection=COLLECTION,
        )


# Audit 2026-05-06 finding 4: deny-list of extra_payload keys that the
# pipeline carries through Procrastinate task args + PG `artifacts.extra`
# but that should NOT land in Qdrant per-chunk payload. Each of these is
# either huge (full document body) or only useful at processing time
# (cache hits across Phase-2 retries). All are excluded from the
# read-side filter `_ALLOWED_METADATA_FIELDS` below; storing them in
# Qdrant is dead weight.
#
# - document_text:     ~100 KB raw body x N chunks = MB per document
# - document_summary:  ~1-2 KB Anthropic contextual-retrieval summary
# - document_language: 2-3 char ISO code, but lives in extra_payload
#                      only for Phase-2 cache parity with summary
#
# The fields stay in PG (`artifacts.extra->>'document_text'`, used by
# rebuild_kb) and in the Procrastinate task args (used by
# `_enrich_document` for cache hits across retries). Stripping them at
# the Qdrant boundary keeps both consumers working.
_QDRANT_PAYLOAD_DENY_LIST: frozenset[str] = frozenset(
    {"document_text", "document_summary", "document_language"}
)


def _extra_payload_for_qdrant(extra_payload: dict | None) -> dict:
    """Return ``extra_payload`` minus keys that should not be persisted in
    Qdrant chunk-payload. Returns an empty dict for None input.
    """
    if not extra_payload:
        return {}
    return {k: v for k, v in extra_payload.items() if k not in _QDRANT_PAYLOAD_DENY_LIST}


async def upsert_chunks(
    org_id: str,
    kb_slug: str,
    path: str,
    chunks: list[str],
    vectors: list[list[float]],
    artifact_id: str,
    extra_payload: dict | None = None,
    user_id: str | None = None,
    taxonomy_node_ids: list[int] | None = None,
    tags: list[str] | None = None,
    has_taxonomy: bool = False,
    content_label: list[str] | None = None,
) -> None:
    """Upsert raw chunks (before enrichment). Uses vector_chunk named vector.
    Backward compatible: called by the ingest pipeline before enrichment runs.

    taxonomy_node_ids: list of matched node ids, [] = no match, absent field = no taxonomy on KB.
    tags: list of free-form tags to store on chunks.
    has_taxonomy: True when the KB has taxonomy nodes (field is stored even when empty).
    content_label: blind keyword list generated before taxonomy (SPEC-KB-023).
        None = labeler not called (old callers); [] = labeler ran but failed/returned empty.
        Both [] and non-empty lists are stored when not None.
    """
    client = get_client()

    # Delete existing points for this document
    await client.delete(
        COLLECTION,
        points_selector=Filter(
            must=[
                FieldCondition(key="org_id", match=MatchValue(value=org_id)),
                FieldCondition(key="kb_slug", match=MatchValue(value=kb_slug)),
                FieldCondition(key="path", match=MatchValue(value=path)),
            ]
        ),
    )

    if not chunks:
        return

    base_payload = {
        "org_id": org_id,
        "kb_slug": kb_slug,
        "path": path,
        "artifact_id": artifact_id,
        "quality_score": 0.5,
        "feedback_count": 0,
    }
    if user_id:
        base_payload["user_id"] = user_id
    # Store taxonomy_node_ids only when the KB has taxonomy nodes (R1: absent = no taxonomy on KB)
    if has_taxonomy:
        base_payload["taxonomy_node_ids"] = (
            taxonomy_node_ids if taxonomy_node_ids is not None else []
        )
    if tags:
        base_payload["tags"] = tags
    # Store content_label when not None — includes [] (labeler ran but failed)
    if content_label is not None:
        base_payload["content_label"] = content_label
    base_payload.update(_extra_payload_for_qdrant(extra_payload))

    points = [
        PointStruct(
            id=str(uuid.uuid4()),
            vector={"vector_chunk": vector},
            payload={**base_payload, "text": chunk, "chunk_index": i},
        )
        for i, (chunk, vector) in enumerate(zip(chunks, vectors, strict=False))
    ]
    await client.upsert(COLLECTION, points=points)


async def upsert_enriched_chunks(
    org_id: str,
    kb_slug: str,
    path: str,
    enriched_chunks: list,  # list[enrichment.EnrichedChunk]
    chunk_vectors: list[list[float]],
    question_vectors: list[list[float] | None],
    sparse_vectors: list[SparseVector | None] | None = None,
    artifact_id: str = "",
    extra_payload: dict | None = None,
    user_id: str | None = None,
    content_type: str = "unknown",
    belief_time_start: int | None = None,
    belief_time_end: int | None = None,
    parent_chunk_ids: list[int | None] | None = None,
) -> None:
    """
    Upsert enriched chunks with named + sparse vectors.
    Deletes existing points for this path first.
    vector_chunk is always populated; vector_questions is profile-dependent;
    vector_sparse is populated when the sparse sidecar is available.

    Metadata fields (taxonomy_node_ids, tags, content_label, visibility, etc.)
    are passed entirely via extra_payload — no separate parameters needed here.
    """
    client = get_client()

    await client.delete(
        COLLECTION,
        points_selector=Filter(
            must=[
                FieldCondition(key="org_id", match=MatchValue(value=org_id)),
                FieldCondition(key="kb_slug", match=MatchValue(value=kb_slug)),
                FieldCondition(key="path", match=MatchValue(value=path)),
            ]
        ),
    )

    if not enriched_chunks:
        return

    base_payload: dict = {
        "org_id": org_id,
        "kb_slug": kb_slug,
        "path": path,
        "artifact_id": artifact_id,
        "content_type": content_type,
        "ingested_at": int(time.time()),
        "quality_score": 0.5,
        "feedback_count": 0,
    }
    if belief_time_start is not None:
        base_payload["valid_from"] = belief_time_start
    if belief_time_end is not None:
        base_payload["valid_until"] = belief_time_end
    if user_id:
        base_payload["user_id"] = user_id
    base_payload.update(_extra_payload_for_qdrant(extra_payload))

    # Default sparse_vectors to all None if not provided
    if sparse_vectors is None:
        sparse_vectors = [None] * len(enriched_chunks)

    points = []
    for i, (ec, chunk_vec, q_vec, sparse_vec) in enumerate(
        zip(enriched_chunks, chunk_vectors, question_vectors, sparse_vectors, strict=False)
    ):
        vectors: dict = {"vector_chunk": chunk_vec}
        if q_vec is not None:
            vectors["vector_questions"] = q_vec
        if sparse_vec is not None:
            vectors["vector_sparse"] = sparse_vec

        # @MX:NOTE: chunk_type (SPEC-KB-021) is the LLM-classified per-chunk
        #   label (procedural/conceptual/reference/warning/example). Only set
        #   when the enrichment LLM produced a value — absence means the
        #   chunk went through the pre-enrichment fast path.
        # @MX:REASON: Must not collide with base_payload['content_type'] which
        #   carries the document-level type (kb_article/pdf_document/...) used
        #   by retrieval_api's evidence_tier scoring.
        chunk_payload = {
            **base_payload,
            "text": ec.original_text,
            "text_enriched": ec.enriched_text,
            "context_prefix": ec.context_prefix,
            "questions": ec.questions,
            "chunk_index": i,
        }
        if getattr(ec, "chunk_type", ""):
            chunk_payload["chunk_type"] = ec.chunk_type
        if getattr(ec, "heading_path", ""):
            chunk_payload["heading_path"] = ec.heading_path

        # SPEC-RAG-PARENT-CHILD-001: thread the parent_chunks.id into each
        # child's payload so retrieval-api can fetch the parent text and
        # swap it in. None for legacy ingests that didn't run through the
        # parent-child chunker — retrieval-api falls through to chunk text.
        if parent_chunk_ids is not None and i < len(parent_chunk_ids):
            pid = parent_chunk_ids[i]
            if pid is not None:
                chunk_payload["parent_chunk_id"] = int(pid)

        points.append(
            PointStruct(
                id=str(uuid.uuid4()),
                vector=vectors,
                payload=chunk_payload,
            )
        )

    await client.upsert(COLLECTION, points=points)


async def delete_document(org_id: str, kb_slug: str, path: str) -> None:
    client = get_client()
    await client.delete(
        COLLECTION,
        points_selector=Filter(
            must=[
                FieldCondition(key="org_id", match=MatchValue(value=org_id)),
                FieldCondition(key="kb_slug", match=MatchValue(value=kb_slug)),
                FieldCondition(key="path", match=MatchValue(value=path)),
            ]
        ),
    )


async def delete_kb(org_id: str, kb_slug: str) -> None:
    """Delete all Qdrant chunks for an entire knowledge base."""
    client = get_client()
    await client.delete(
        COLLECTION,
        points_selector=Filter(
            must=[
                FieldCondition(key="org_id", match=MatchValue(value=org_id)),
                FieldCondition(key="kb_slug", match=MatchValue(value=kb_slug)),
            ]
        ),
    )
    logger.info("kb_chunks_deleted", org_id=org_id, kb_slug=kb_slug)


async def delete_connector(org_id: str, kb_slug: str, connector_id: str) -> None:
    """Delete all Qdrant chunks for a specific connector (by source_connector_id payload field)."""
    client = get_client()
    await client.delete(
        COLLECTION,
        points_selector=Filter(
            must=[
                FieldCondition(key="org_id", match=MatchValue(value=org_id)),
                FieldCondition(key="kb_slug", match=MatchValue(value=kb_slug)),
                FieldCondition(key="source_connector_id", match=MatchValue(value=connector_id)),
            ]
        ),
    )
    logger.info(
        "connector_chunks_deleted",
        org_id=org_id,
        kb_slug=kb_slug,
        connector_id=connector_id,
    )


async def update_kb_visibility(org_id: str, kb_slug: str, visibility: str) -> None:
    """Update the visibility payload field for all chunks in a knowledge base."""
    client = get_client()
    await client.set_payload(
        COLLECTION,
        payload={"visibility": visibility},
        points=Filter(
            must=[
                FieldCondition(key="org_id", match=MatchValue(value=org_id)),
                FieldCondition(key="kb_slug", match=MatchValue(value=kb_slug)),
            ]
        ),
    )
    logger.info("kb_visibility_updated", org_id=org_id, kb_slug=kb_slug, visibility=visibility)


_ALLOWED_METADATA_FIELDS = frozenset(
    {
        "title",
        "kb_slug",
        "chunk_index",
        "created_at",
        "source_type",
        "source_connector_id",
        "source_ref",
        "visibility",
        "tags",
        "provenance_type",
        "confidence",
        "artifact_id",
        "content_type",
        "valid_from",
        "valid_until",
        "ingested_at",
        "assertion_mode",
        "heading_path",
    }
)


async def search(
    org_id: str,
    query_vector: list[float],
    top_k: int = 5,
    kb_slugs: list[str] | None = None,
    user_id: str | None = None,
    sparse_vector: SparseVector | None = None,
    content_type_filter: str | None = None,
    sparse_weight: float | None = None,
) -> list[dict]:
    """Search for chunks matching the query vector.

    Uses 3-leg RRF fusion (vector_chunk + vector_questions + vector_sparse)
    when a sparse query vector is provided. Falls back to 2-leg RRF otherwise.

    user_id filter is applied when any kb_slug starts with "personal-".

    # @MX:TODO: sparse_weight is parked — parameter is plumbed through but has
    #   no behavioral effect until weighted RRF is activated (AC-7 / D4).
    # @MX:SPEC: SPEC-KB-007 AC-7
    # @MX:REASON: SPEC explicitly defers weighted-RRF activation until ≥200
    #   labeled queries / feedback signals are collected (gate D4). Removing
    #   this parameter would break the SPEC contract; wiring it in now would
    #   ship unvalidated behavior. Keep as-is, Fase 6 dead-code audit
    #   confirmed this is a deferred feature, not a regression.
    """
    client = get_client()

    must = [FieldCondition(key="org_id", match=MatchValue(value=org_id))]
    if kb_slugs:
        must.append(FieldCondition(key="kb_slug", match=MatchAny(any=kb_slugs)))
    if user_id and kb_slugs and any(s.startswith("personal-") for s in kb_slugs):
        must.append(FieldCondition(key="user_id", match=MatchValue(value=user_id)))
    if content_type_filter:
        must.append(FieldCondition(key="content_type", match=MatchValue(value=content_type_filter)))

    query_filter = Filter(must=must)

    prefetch_limit = max(top_k * 4, 20)
    prefetch = [
        Prefetch(
            query=query_vector,
            using="vector_chunk",
            limit=prefetch_limit,
            filter=query_filter,
        ),
        Prefetch(
            query=query_vector,
            using="vector_questions",
            limit=prefetch_limit,
            filter=query_filter,
        ),
    ]
    if sparse_vector is not None:
        prefetch.append(
            Prefetch(
                query=sparse_vector,
                using="vector_sparse",
                limit=prefetch_limit,
                filter=query_filter,
            )
        )

    results = await client.query_points(
        collection_name=COLLECTION,
        prefetch=prefetch,
        query=FusionQuery(fusion=Fusion.RRF),
        limit=top_k,
        with_payload=True,
    )
    points = results.points
    return [
        {
            "text": p.payload.get("text", "") if p.payload else "",
            "source": f"{p.payload.get('kb_slug', '')}/{p.payload.get('path', '')}"
            if p.payload
            else "",
            "score": p.score,
            "metadata": {
                k: v for k, v in (p.payload or {}).items() if k in _ALLOWED_METADATA_FIELDS
            },
            "heading_path": p.payload.get("heading_path") if p.payload else None,
        }
        for p in points
    ]


# Minimum entity-name length for chunk-level substring matching. Two-character
# names produce false positives ("AI" inside "fail", "stair") that pollute BM25.
# Three is the smallest safe threshold for brand/product names while still
# catching common short ones (CRM, ERP, SSO).
_ENTITY_NAME_MIN_LEN = 3


def filter_entity_names_for_chunk(
    chunk_text: str,
    doc_entity_names: list[str],
) -> list[str]:
    """Return the subset of doc_entity_names that literally appear in chunk_text.

    Case-insensitive substring match. Names shorter than _ENTITY_NAME_MIN_LEN
    are skipped to suppress false-positive matches inside unrelated words.
    Duplicate names (e.g. Graphiti emitted both "Voys" and "voys") collapse to
    a single canonical form (lowercased preferred when both casings appear).

    Pure function — kept top-level for testability.
    """
    if not doc_entity_names or not chunk_text:
        return []
    chunk_lower = chunk_text.lower()
    seen: set[str] = set()
    result: list[str] = []
    for name in doc_entity_names:
        if not name or not isinstance(name, str):
            continue
        cleaned = name.strip()
        if len(cleaned) < _ENTITY_NAME_MIN_LEN:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        if key in chunk_lower:
            seen.add(key)
            result.append(cleaned)
    return result


async def set_entity_graph_data(
    artifact_id: str,
    org_id: str,
    entity_uuids: list[str],
    pagerank_scores: dict[str, float],
    entity_names: list[str] | None = None,
) -> None:
    """Set entity UUIDs, entity names, and max PageRank score on chunks of an artifact.

    Called after Graphiti episode ingestion completes.

    entity_uuids + entity_pagerank_max are document-level (set on all chunks of
    the artifact via a single artifact_id-scoped set_payload).

    entity_names is filtered per-chunk: each chunk only carries the subset of
    document-level names that literally appear in its own text. This keeps
    BM25/sparse from being polluted by entity names that belong to a different
    section of the same long document. When entity_names is None or empty, only
    the document-level fields are written (back-compat with callers that don't
    yet provide names).
    """
    if not entity_uuids and not entity_names:
        return

    client = get_client()
    scores = [pagerank_scores.get(uid, 0.0) for uid in entity_uuids] if entity_uuids else []
    pagerank_max = max(scores) if scores else 0.0

    # Document-level write: same payload across every chunk of the artifact.
    if entity_uuids:
        await client.set_payload(
            COLLECTION,
            payload={
                "entity_uuids": entity_uuids,
                "entity_pagerank_max": pagerank_max,
            },
            points=Filter(
                must=[
                    FieldCondition(key="artifact_id", match=MatchValue(value=artifact_id)),
                    FieldCondition(key="org_id", match=MatchValue(value=org_id)),
                ]
            ),
        )

    # Chunk-level write: per-chunk substring filter against chunk text.
    chunks_with_names = 0
    chunks_total = 0
    if entity_names:
        chunks_with_names, chunks_total = await _set_per_chunk_entity_names(
            client=client,
            artifact_id=artifact_id,
            org_id=org_id,
            doc_entity_names=entity_names,
        )

    # chunks_total + chunks_with_names lets Grafana compute the per-tenant
    # coverage rate from VictoriaLogs: stats by(org_id) sum(chunks_with_names)
    # / sum(chunks_total) over event:entity_graph_data_set.
    logger.info(
        "entity_graph_data_set",
        artifact_id=artifact_id,
        org_id=org_id,
        entity_count=len(entity_uuids),
        entity_name_count=len(entity_names) if entity_names else 0,
        chunks_total=chunks_total,
        chunks_with_names=chunks_with_names,
        pagerank_max=round(pagerank_max, 6),
    )


_ENTITY_NAMES_CHUNK_BATCH = 100


async def _set_per_chunk_entity_names(
    client: AsyncQdrantClient,
    artifact_id: str,
    org_id: str,
    doc_entity_names: list[str],
) -> tuple[int, int]:
    """Scroll all chunks of an artifact and write the per-chunk entity_names
    subset. Returns (chunks_with_names, chunks_total) — the coverage ratio
    consumer can compute the per-artifact filter-acceptance rate.
    """
    artifact_filter = Filter(
        must=[
            FieldCondition(key="artifact_id", match=MatchValue(value=artifact_id)),
            FieldCondition(key="org_id", match=MatchValue(value=org_id)),
        ]
    )

    offset = None
    chunks_with_names = 0
    chunks_total = 0
    while True:
        try:
            points, next_offset = await client.scroll(
                collection_name=COLLECTION,
                scroll_filter=artifact_filter,
                limit=_ENTITY_NAMES_CHUNK_BATCH,
                offset=offset,
                with_payload=["text"],
                with_vectors=False,
            )
        except Exception:
            logger.exception(
                "entity_names_scroll_failed",
                artifact_id=artifact_id,
                org_id=org_id,
            )
            return chunks_with_names, chunks_total

        if not points:
            break

        for point in points:
            chunks_total += 1
            chunk_text = (point.payload or {}).get("text", "")
            if not isinstance(chunk_text, str):
                continue
            names = filter_entity_names_for_chunk(chunk_text, doc_entity_names)
            if not names:
                # Qdrant strips empty-list keys on upsert; absent == empty.
                continue
            try:
                await client.set_payload(
                    COLLECTION,
                    payload={"entity_names": names},
                    points=[point.id],
                )
                chunks_with_names += 1
            except Exception:
                logger.exception(
                    "entity_names_set_payload_failed",
                    artifact_id=artifact_id,
                    org_id=org_id,
                    chunk_id=str(point.id),
                )

        if next_offset is None:
            break
        offset = next_offset

    return chunks_with_names, chunks_total


_LINK_COUNT_CONCURRENCY = 20  # max parallel set_payload calls per bulk crawl


# @MX:WARN: [AUTO] update_link_counts — deprecated post-crawl band-aid
# @MX:REASON: Deprecated band-aid — re-wiring into the crawl path creates a race
#   with enrichment. The two-phase pipeline (SPEC-CRAWLER-005) makes
#   incoming_link_count correct at first write via get_incoming_count() per-page.
async def update_link_counts(
    org_id: str,
    kb_slug: str,
    url_to_count: dict[str, int],
) -> None:
    """DEPRECATED (SPEC-CRAWLER-005 REQ-05.1): No production caller. The two-phase
    crawl pipeline populates incoming_link_count correctly at first write via
    link_graph.get_incoming_count() per-page. Calling this function after a
    crawl is now a no-op band-aid with a race-condition risk — do NOT re-wire
    it into run_crawl_job. Kept for potential admin-only repair scripts.

    Update incoming_link_count for all chunks of each URL in the dict.
    Uses set_payload() with a source_url filter -- same pattern as set_entity_graph_data().
    Concurrency bounded by _LINK_COUNT_CONCURRENCY to avoid Qdrant overload.
    """
    if not url_to_count:
        return

    client = get_client()
    sem = asyncio.Semaphore(_LINK_COUNT_CONCURRENCY)

    async def _update_one(url: str, count: int) -> None:
        async with sem:
            try:
                await asyncio.wait_for(
                    client.set_payload(
                        COLLECTION,
                        payload={"incoming_link_count": count},
                        points=Filter(
                            must=[
                                FieldCondition(key="source_url", match=MatchValue(value=url)),
                                FieldCondition(key="org_id", match=MatchValue(value=org_id)),
                                FieldCondition(key="kb_slug", match=MatchValue(value=kb_slug)),
                            ]
                        ),
                    ),
                    timeout=5.0,
                )
            except TimeoutError:
                logger.warning("link_count_update_timeout", url=url, org_id=org_id, kb_slug=kb_slug)

    t0 = time.time()
    await asyncio.gather(*(_update_one(url, count) for url, count in url_to_count.items()))
    logger.info(
        "link_counts_updated",
        org_id=org_id,
        kb_slug=kb_slug,
        url_count=len(url_to_count),
        duration_ms=int((time.time() - t0) * 1000),
    )
