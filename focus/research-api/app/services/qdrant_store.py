"""
Qdrant vector store for klai-focus research-api.
Manages the klai_focus collection: creation, upsert, search, and deletion.
"""
import logging
import uuid
from datetime import datetime, timezone

from qdrant_client import QdrantClient
from qdrant_client.http import models as qdrant_models
from qdrant_client.http.exceptions import UnexpectedResponse

from app.core.config import settings

logger = logging.getLogger(__name__)

_client: QdrantClient | None = None


def get_client() -> QdrantClient:
    global _client
    if _client is None:
        _client = QdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key or None,
        )
    return _client


def ensure_collection() -> None:
    """
    Create klai_focus collection if it does not exist.
    Creates payload indexes for tenant_id, notebook_id, source_id.
    Idempotent -- safe to call on every startup.
    """
    client = get_client()
    collection_name = settings.qdrant_collection

    try:
        client.get_collection(collection_name)
        logger.info("Qdrant collection '%s' already exists", collection_name)
    except (UnexpectedResponse, Exception) as e:
        if "Not found" in str(e) or "doesn't exist" in str(e) or "404" in str(e):
            client.create_collection(
                collection_name=collection_name,
                vectors_config=qdrant_models.VectorParams(
                    size=1024,
                    distance=qdrant_models.Distance.COSINE,
                ),
            )
            logger.info("Created Qdrant collection '%s'", collection_name)
        else:
            raise

    # Create payload indexes (idempotent)
    for field_name in ("tenant_id", "notebook_id", "source_id"):
        try:
            client.create_payload_index(
                collection_name=collection_name,
                field_name=field_name,
                field_schema=qdrant_models.PayloadSchemaType.KEYWORD,
            )
        except Exception:
            pass  # Index may already exist


def upsert_chunks(
    chunks: list[dict],
    embeddings: list[list[float]],
) -> None:
    """
    Upsert chunk vectors into the klai_focus collection.
    Point ID is a deterministic UUID derived from chunk_id via uuid5.
    """
    client = get_client()
    points = []
    for chunk_data, embedding in zip(chunks, embeddings):
        chunk_id = chunk_data["chunk_id"]
        point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, chunk_id))
        points.append(
            qdrant_models.PointStruct(
                id=point_id,
                vector=embedding,
                payload={
                    "chunk_id": chunk_id,
                    "tenant_id": chunk_data["tenant_id"],
                    "notebook_id": chunk_data["notebook_id"],
                    "source_id": chunk_data["source_id"],
                    "content": chunk_data["content"],
                    "chunk_index": chunk_data.get("chunk_index", 0),
                    "metadata": chunk_data.get("metadata") or {},
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        )
    if points:
        client.upsert(collection_name=settings.qdrant_collection, points=points)


def search_chunks(
    query_vector: list[float],
    tenant_id: str,
    notebook_id: str,
    source_ids: list[str],
    top_k: int = 8,
) -> list[dict]:
    """
    Search klai_focus collection with mandatory tenant_id + notebook_id filter.
    source_ids restricts to chunks from sources with status='ready'.
    Returns list of {chunk_id, source_id, content, metadata, score}.
    """
    client = get_client()

    must_filters = [
        qdrant_models.FieldCondition(
            key="tenant_id",
            match=qdrant_models.MatchValue(value=tenant_id),
        ),
        qdrant_models.FieldCondition(
            key="notebook_id",
            match=qdrant_models.MatchValue(value=notebook_id),
        ),
    ]

    if source_ids:
        must_filters.append(
            qdrant_models.FieldCondition(
                key="source_id",
                match=qdrant_models.MatchAny(any=source_ids),
            )
        )

    results = client.search(
        collection_name=settings.qdrant_collection,
        query_vector=query_vector,
        query_filter=qdrant_models.Filter(must=must_filters),
        limit=top_k,
        with_payload=True,
    )

    return [
        {
            "chunk_id": hit.payload.get("chunk_id", str(hit.id)),
            "source_id": hit.payload.get("source_id", ""),
            "content": hit.payload.get("content", ""),
            "metadata": hit.payload.get("metadata") or {},
            "score": hit.score,
        }
        for hit in results
    ]


def delete_by_source(source_id: str) -> None:
    """Delete all vectors for a given source_id."""
    client = get_client()
    client.delete(
        collection_name=settings.qdrant_collection,
        points_selector=qdrant_models.FilterSelector(
            filter=qdrant_models.Filter(
                must=[
                    qdrant_models.FieldCondition(
                        key="source_id",
                        match=qdrant_models.MatchValue(value=source_id),
                    )
                ]
            )
        ),
    )


def delete_by_notebook(notebook_id: str, tenant_id: str) -> None:
    """Delete all vectors for a given notebook_id + tenant_id pair."""
    client = get_client()
    client.delete(
        collection_name=settings.qdrant_collection,
        points_selector=qdrant_models.FilterSelector(
            filter=qdrant_models.Filter(
                must=[
                    qdrant_models.FieldCondition(
                        key="notebook_id",
                        match=qdrant_models.MatchValue(value=notebook_id),
                    ),
                    qdrant_models.FieldCondition(
                        key="tenant_id",
                        match=qdrant_models.MatchValue(value=tenant_id),
                    ),
                ]
            )
        ),
    )
