"""
Retrieve route:
  POST /knowledge/v1/retrieve — hybrid semantic search for LiteLLM hook
"""
import logging

from fastapi import APIRouter

from knowledge_ingest import embedder, qdrant_store
from knowledge_ingest.db import get_pool
from knowledge_ingest.models import ChunkResult, RetrieveRequest, RetrieveResponse

logger = logging.getLogger(__name__)
router = APIRouter()

_ARTIFACT_FIELDS = (
    "provenance_type", "assertion_mode", "synthesis_depth", "confidence",
    "belief_time_start", "belief_time_end",
)


@router.post("/knowledge/v1/retrieve", response_model=RetrieveResponse)
async def retrieve(req: RetrieveRequest) -> RetrieveResponse:
    query_vector = await embedder.embed_one(req.query)
    results = await qdrant_store.search(
        org_id=req.org_id,
        query_vector=query_vector,
        top_k=req.top_k,
        kb_slugs=req.kb_slugs,
        user_id=req.user_id,
    )

    # Enrich chunks with PostgreSQL artifact metadata
    artifact_ids = list({
        r["metadata"].get("artifact_id")
        for r in results
        if r["metadata"].get("artifact_id")
    })
    artifact_meta: dict = {}
    if artifact_ids:
        pool = await get_pool()
        rows = await pool.fetch(
            "SELECT id::text, provenance_type, assertion_mode, synthesis_depth, confidence, "
            "       belief_time_start, belief_time_end "
            "FROM knowledge.artifacts WHERE id::text = ANY($1::text[])",
            artifact_ids,
        )
        artifact_meta = {r["id"]: dict(r) for r in rows}

    chunks = []
    for r in results:
        aid = r["metadata"].get("artifact_id")
        meta = dict(r.get("metadata", {}))
        pg_data = artifact_meta.get(aid, {}) if aid else {}
        chunk = ChunkResult(
            text=r["text"],
            source=r["source"],
            score=r["score"],
            metadata=meta,
            artifact_id=aid,
            provenance_type=pg_data.get("provenance_type") or meta.get("provenance_type"),
            assertion_mode=pg_data.get("assertion_mode"),
            synthesis_depth=pg_data.get("synthesis_depth"),
            confidence=pg_data.get("confidence") or meta.get("confidence"),
        )
        chunks.append(chunk)

    logger.debug("Retrieved %d chunks for org %s (query len=%d)", len(chunks), req.org_id, len(req.query))
    return RetrieveResponse(chunks=chunks)
