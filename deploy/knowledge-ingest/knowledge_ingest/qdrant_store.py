"""
Qdrant operations for the knowledge graph.

Single collection: klai_knowledge
Tenant isolation via org_id payload filter.
"""
import logging
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
    VectorParams,
)

from knowledge_ingest.config import settings
from knowledge_ingest.embedder import EMBED_DIM

logger = logging.getLogger(__name__)
# Legacy collection name (single default vector). Kept for backward compatibility.
COLLECTION = "klai_knowledge"
# v2 collection with named vectors (vector_chunk + vector_questions).
COLLECTION_V2 = "klai_knowledge_v2"

_client: AsyncQdrantClient | None = None


def get_client() -> AsyncQdrantClient:
    global _client
    if _client is None:
        _client = AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key or None,
        )
    return _client


def _active_collection() -> str:
    """Return the active collection name from config (supports migration switchover)."""
    return settings.qdrant_collection


async def ensure_collection() -> None:
    client = get_client()
    existing = [c.name for c in (await client.get_collections()).collections]

    # Legacy collection (default vector) — always ensure it exists for backward compat
    if COLLECTION not in existing:
        await client.create_collection(
            COLLECTION,
            vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
        )
        await client.create_payload_index(COLLECTION, field_name="org_id", field_schema="keyword")
        await client.create_payload_index(COLLECTION, field_name="kb_slug", field_schema="keyword")
        await client.create_payload_index(COLLECTION, field_name="artifact_id", field_schema="keyword")
        logger.info("Created Qdrant collection %s", COLLECTION)
    else:
        logger.info("Qdrant collection %s already exists", COLLECTION)

    # v2 collection (named vectors: vector_chunk + vector_questions)
    if COLLECTION_V2 not in existing:
        await client.create_collection(
            COLLECTION_V2,
            vectors_config={
                "vector_chunk": VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
                "vector_questions": VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
            },
        )
        await client.create_payload_index(COLLECTION_V2, field_name="org_id", field_schema="keyword")
        await client.create_payload_index(COLLECTION_V2, field_name="kb_slug", field_schema="keyword")
        await client.create_payload_index(COLLECTION_V2, field_name="artifact_id", field_schema="keyword")
        logger.info("Created Qdrant collection %s with named vectors", COLLECTION_V2)
    else:
        logger.info("Qdrant collection %s already exists", COLLECTION_V2)


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
    """Upsert chunks for a document. Deletes old points for same path first."""
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

    base_payload = {"org_id": org_id, "kb_slug": kb_slug, "path": path, "artifact_id": artifact_id}
    if user_id:
        base_payload["user_id"] = user_id
    if extra_payload:
        base_payload.update(extra_payload)

    points = [
        PointStruct(
            id=str(uuid.uuid4()),
            vector=vector,
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
    artifact_id: str,
    extra_payload: dict | None = None,
    user_id: str | None = None,
) -> None:
    """
    Upsert enriched chunks into the v2 collection with named vectors.
    Deletes existing points for this path first (same path key as raw upsert).
    vector_chunk is always populated; vector_questions is populated only for depth 0-1 chunks.
    """
    client = get_client()
    collection = COLLECTION_V2

    await client.delete(
        collection,
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

    base_payload = {"org_id": org_id, "kb_slug": kb_slug, "path": path, "artifact_id": artifact_id}
    if user_id:
        base_payload["user_id"] = user_id
    if extra_payload:
        base_payload.update(extra_payload)

    points = []
    for i, (ec, chunk_vec, q_vec) in enumerate(zip(enriched_chunks, chunk_vectors, question_vectors)):
        vectors: dict[str, list[float]] = {"vector_chunk": chunk_vec}
        if q_vec is not None:
            vectors["vector_questions"] = q_vec

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

    await client.upsert(collection, points=points)


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
    "artifact_id",
})


async def search(
    org_id: str,
    query_vector: list[float],
    top_k: int = 5,
    kb_slugs: list[str] | None = None,
    user_id: str | None = None,
) -> list[dict]:
    """Search for chunks matching the query vector.

    Uses Dual-Index Fusion (RRF) via Qdrant prefetch when the active collection
    supports named vectors (v2). Falls back to legacy search on the v1 collection.

    user_id filter is applied only when kb_slugs contains "personal".
    """
    client = get_client()
    collection = _active_collection()

    must = [FieldCondition(key="org_id", match=MatchValue(value=org_id))]
    if kb_slugs:
        must.append(FieldCondition(key="kb_slug", match=MatchAny(any=kb_slugs)))
    if user_id and kb_slugs and "personal" in kb_slugs:
        must.append(FieldCondition(key="user_id", match=MatchValue(value=user_id)))

    query_filter = Filter(must=must)

    if collection == COLLECTION_V2:
        # Dual-Index Fusion: search both named vectors, fuse with RRF
        prefetch_limit = max(top_k * 4, 20)
        results = await client.query_points(
            collection_name=collection,
            prefetch=[
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
            ],
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

    # Legacy: single default vector search (v1 collection)
    legacy_results = await client.search(
        collection,
        query_vector=query_vector,
        query_filter=query_filter,
        limit=top_k,
        with_payload=True,
    )
    return [
        {
            "text": r.payload.get("text", ""),
            "source": f"{r.payload.get('kb_slug', '')}/{r.payload.get('path', '')}",
            "score": r.score,
            "metadata": {
                k: v
                for k, v in r.payload.items()
                if k in _ALLOWED_METADATA_FIELDS
            },
        }
        for r in legacy_results
    ]
