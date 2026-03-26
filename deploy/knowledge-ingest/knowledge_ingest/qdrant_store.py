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
    MatchAny,
    MatchValue,
    PointStruct,
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
    client = get_client()
    existing = [c.name for c in (await client.get_collections()).collections]
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

    user_id filter is applied only when kb_slugs contains "personal" — it has
    no effect for org-scope or other KB slugs.
    """
    client = get_client()

    must = [FieldCondition(key="org_id", match=MatchValue(value=org_id))]
    if kb_slugs:
        must.append(FieldCondition(key="kb_slug", match=MatchAny(any=kb_slugs)))
    if user_id and kb_slugs and "personal" in kb_slugs:
        must.append(FieldCondition(key="user_id", match=MatchValue(value=user_id)))

    results = await client.search(
        COLLECTION,
        query_vector=query_vector,
        query_filter=Filter(must=must),
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
        for r in results
    ]
