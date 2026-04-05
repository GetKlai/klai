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
    for field in ("org_id", "kb_slug", "artifact_id", "content_type", "user_id", "entity_uuids", "taxonomy_node_id", "source_connector_id"):
        if field not in indexed_fields:
            await client.create_payload_index(
                COLLECTION, field_name=field, field_schema="keyword",
            )
            logger.info("qdrant_payload_index_created", field=field, collection=COLLECTION)

    # source_url: keyword index for payload-filter-based chunk lookup (SPEC-CRAWLER-003)
    if "source_url" not in indexed_fields:
        await client.create_payload_index(
            COLLECTION, field_name="source_url", field_schema="keyword",
        )
        logger.info("qdrant_payload_index_created", field="source_url", collection=COLLECTION)

    # incoming_link_count: integer index for authority boost queries (SPEC-CRAWLER-003)
    if "incoming_link_count" not in indexed_fields:
        await client.create_payload_index(
            COLLECTION, field_name="incoming_link_count", field_schema="integer",
        )
        logger.info(
            "qdrant_payload_index_created",
            field="incoming_link_count",
            collection=COLLECTION,
        )


async def upsert_chunks(
    org_id: str,
    kb_slug: str,
    path: str,
    chunks: list[str],
    vectors: list[list[float]],
    artifact_id: str,
    extra_payload: dict | None = None,
    user_id: str | None = None,
    taxonomy_node_id: int | None = None,
    has_taxonomy: bool = False,
) -> None:
    """Upsert raw chunks (before enrichment). Uses vector_chunk named vector.
    Backward compatible: called by the ingest pipeline before enrichment runs.

    taxonomy_node_id: matched node id (int), None = no match, absent field = no taxonomy on KB.
    has_taxonomy: True when the KB has taxonomy nodes (field is stored even when node_id=None).
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
    # Store taxonomy_node_id only when the KB has taxonomy nodes (R1: absent = no taxonomy on KB)
    if has_taxonomy:
        base_payload["taxonomy_node_id"] = taxonomy_node_id
    if extra_payload:
        base_payload.update(extra_payload)

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
    taxonomy_node_id: int | None = None,
    has_taxonomy: bool = False,
) -> None:
    """
    Upsert enriched chunks with named + sparse vectors.
    Deletes existing points for this path first.
    vector_chunk is always populated; vector_questions is profile-dependent;
    vector_sparse is populated when the sparse sidecar is available.
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
    # Store taxonomy_node_id only when the KB has taxonomy nodes (R1: absent = no taxonomy on KB)
    if has_taxonomy:
        base_payload["taxonomy_node_id"] = taxonomy_node_id
    if extra_payload:
        base_payload.update(extra_payload)

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

        points.append(
            PointStruct(
                id=str(uuid.uuid4()),
                vector=vectors,
                payload={
                    **base_payload,
                    "text": ec.original_text,
                    "text_enriched": ec.enriched_text,
                    "context_prefix": ec.context_prefix,
                    "questions": ec.questions,
                    "chunk_index": i,
                },
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
        org_id=org_id, kb_slug=kb_slug, connector_id=connector_id,
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


_ALLOWED_METADATA_FIELDS = frozenset({
    "title", "kb_slug", "chunk_index", "created_at",
    "source_type", "source_connector_id", "source_ref", "visibility", "tags", "provenance_type", "confidence",
    "artifact_id", "content_type", "valid_from", "valid_until", "ingested_at",
    "assertion_mode",
})


async def search(
    org_id: str,
    query_vector: list[float],
    top_k: int = 5,
    kb_slugs: list[str] | None = None,
    user_id: str | None = None,
    sparse_vector: SparseVector | None = None,
    content_type_filter: str | None = None,
    sparse_weight: float | None = None,  # AC-7: reserved for weighted convex combination; no behavioral effect yet  # noqa: E501
) -> list[dict]:
    """Search for chunks matching the query vector.

    Uses 3-leg RRF fusion (vector_chunk + vector_questions + vector_sparse)
    when a sparse query vector is provided. Falls back to 2-leg RRF otherwise.

    user_id filter is applied only when kb_slugs contains "personal".
    """
    client = get_client()

    must = [FieldCondition(key="org_id", match=MatchValue(value=org_id))]
    if kb_slugs:
        must.append(FieldCondition(key="kb_slug", match=MatchAny(any=kb_slugs)))
    if user_id and kb_slugs and "personal" in kb_slugs:
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
            "source": f"{p.payload.get('kb_slug', '')}/{p.payload.get('path', '')}" if p.payload else "",  # noqa: E501
            "score": p.score,
            "metadata": {
                k: v
                for k, v in (p.payload or {}).items()
                if k in _ALLOWED_METADATA_FIELDS
            },
        }
        for p in points
    ]


async def set_entity_graph_data(
    artifact_id: str,
    org_id: str,
    entity_uuids: list[str],
    pagerank_scores: dict[str, float],
) -> None:
    """Set entity UUIDs and max PageRank score on all chunks of an artifact.

    Called after Graphiti episode ingestion completes. All chunks of the same
    artifact get the same entity list (extracted at document level).
    entity_pagerank_max is the highest PageRank score among this artifact's entities.
    """
    if not entity_uuids:
        return

    client = get_client()
    scores = [pagerank_scores.get(uid, 0.0) for uid in entity_uuids]
    pagerank_max = max(scores) if scores else 0.0

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
    logger.info(
        "entity_graph_data_set",
        artifact_id=artifact_id,
        org_id=org_id,
        entity_count=len(entity_uuids),
        pagerank_max=round(pagerank_max, 6),
    )


_LINK_COUNT_CONCURRENCY = 20  # max parallel set_payload calls per bulk crawl


async def update_link_counts(
    org_id: str,
    kb_slug: str,
    url_to_count: dict[str, int],
) -> None:
    """Update incoming_link_count for all chunks of each URL in the dict.

    Called after a bulk crawl run to refresh the count for all pages in the KB.
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
                logger.warning(
                    "link_count_update_timeout", url=url, org_id=org_id, kb_slug=kb_slug
                )

    t0 = time.time()
    await asyncio.gather(*(_update_one(url, count) for url, count in url_to_count.items()))
    logger.info(
        "link_counts_updated",
        org_id=org_id,
        kb_slug=kb_slug,
        url_count=len(url_to_count),
        duration_ms=int((time.time() - t0) * 1000),
    )
