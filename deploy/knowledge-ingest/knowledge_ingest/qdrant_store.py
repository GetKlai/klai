"""
Qdrant operations for the knowledge graph.

Single collection: klai_knowledge
  - vector_chunk (dense): enriched chunk text embedding
  - vector_questions (dense): HyPE question embedding (depth-dependent)
  - vector_sparse (sparse): BM25-style lexical matching via BGE-M3
Tenant isolation via org_id payload filter.
"""
import logging
import time
import uuid

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
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

from knowledge_ingest.config import settings
from knowledge_ingest.embedder import EMBED_DIM

logger = logging.getLogger(__name__)

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
        for field in ("org_id", "kb_slug", "artifact_id", "content_type"):
            await client.create_payload_index(
                COLLECTION, field_name=field, field_schema="keyword",
            )
        logger.info("Created Qdrant collection %s with named + sparse vectors", COLLECTION)
    else:
        logger.info("Qdrant collection %s already exists", COLLECTION)


async def upsert_chunks(
    org_id: str,
    kb_slug: str,
    path: str,
    chunks: list[str],
    vectors: list[list[float]],
    artifact_id: str,
    extra_payload: dict | None = None,
    user_id: str | None = None,
) -> None:
    """Upsert raw chunks (before enrichment). Uses vector_chunk named vector.
    Backward compatible: called by the ingest pipeline before enrichment runs."""
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
    }
    if user_id:
        base_payload["user_id"] = user_id
    if extra_payload:
        base_payload.update(extra_payload)

    points = [
        PointStruct(
            id=str(uuid.uuid4()),
            vector={"vector_chunk": vector},
            payload={**base_payload, "text": chunk, "chunk_index": i},
        )
        for i, (chunk, vector) in enumerate(zip(chunks, vectors))
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
    }
    if belief_time_start is not None:
        base_payload["valid_from"] = belief_time_start
    if belief_time_end is not None:
        base_payload["valid_until"] = belief_time_end
    if user_id:
        base_payload["user_id"] = user_id
    if extra_payload:
        base_payload.update(extra_payload)

    # Default sparse_vectors to all None if not provided
    if sparse_vectors is None:
        sparse_vectors = [None] * len(enriched_chunks)

    points = []
    for i, (ec, chunk_vec, q_vec, sparse_vec) in enumerate(
        zip(enriched_chunks, chunk_vectors, question_vectors, sparse_vectors)
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
    logger.info("Deleted all chunks for KB %s/%s", org_id, kb_slug)


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
    logger.info("Updated visibility to %s for KB %s/%s", visibility, org_id, kb_slug)


_ALLOWED_METADATA_FIELDS = frozenset({
    "title", "kb_slug", "chunk_index", "created_at",
    "source_type", "visibility", "tags", "provenance_type", "confidence",
    "artifact_id", "content_type", "valid_from", "valid_until", "ingested_at",
})


async def search(
    org_id: str,
    query_vector: list[float],
    top_k: int = 5,
    kb_slugs: list[str] | None = None,
    user_id: str | None = None,
    sparse_vector: SparseVector | None = None,
    content_type_filter: str | None = None,
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
            "source": f"{p.payload.get('kb_slug', '')}/{p.payload.get('path', '')}" if p.payload else "",
            "score": p.score,
            "metadata": {
                k: v
                for k, v in (p.payload or {}).items()
                if k in _ALLOWED_METADATA_FIELDS
            },
        }
        for p in points
    ]
