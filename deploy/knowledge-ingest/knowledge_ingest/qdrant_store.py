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
        logger.info("Created Qdrant collection %s", COLLECTION)
    else:
        logger.info("Qdrant collection %s already exists", COLLECTION)


async def upsert_chunks(
    org_id: str,
    kb_slug: str,
    path: str,
    chunks: list[str],
    vectors: list[list[float]],
    extra_payload: dict | None = None,
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

    base_payload = {"org_id": org_id, "kb_slug": kb_slug, "path": path}
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


async def search(
    org_id: str,
    query_vector: list[float],
    top_k: int = 5,
    kb_slugs: list[str] | None = None,
) -> list[dict]:
    client = get_client()

    must = [FieldCondition(key="org_id", match=MatchValue(value=org_id))]
    if kb_slugs:
        must.append(FieldCondition(key="kb_slug", match=MatchAny(any=kb_slugs)))

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
                if k not in ("text", "org_id", "chunk_index")
            },
        }
        for r in results
    ]
