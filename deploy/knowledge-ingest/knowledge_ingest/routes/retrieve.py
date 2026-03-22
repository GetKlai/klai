"""
Retrieve route:
  POST /knowledge/v1/retrieve — hybrid semantic search for LiteLLM hook
"""
import logging

from fastapi import APIRouter

from knowledge_ingest import embedder, qdrant_store
from knowledge_ingest.models import ChunkResult, RetrieveRequest, RetrieveResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/knowledge/v1/retrieve", response_model=RetrieveResponse)
async def retrieve(req: RetrieveRequest) -> RetrieveResponse:
    query_vector = await embedder.embed_one(req.query)
    results = await qdrant_store.search(
        org_id=req.org_id,
        query_vector=query_vector,
        top_k=req.top_k,
        kb_slugs=req.kb_slugs,
    )
    chunks = [
        ChunkResult(
            text=r["text"],
            source=r["source"],
            score=r["score"],
            metadata=r.get("metadata", {}),
        )
        for r in results
    ]
    logger.debug("Retrieved %d chunks for org %s (query len=%d)", len(chunks), req.org_id, len(req.query))
    return RetrieveResponse(chunks=chunks)
