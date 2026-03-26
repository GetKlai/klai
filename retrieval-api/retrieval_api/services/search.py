"""Qdrant search: dense cosine search across klai_knowledge and klai_focus.

NOTE: klai_knowledge currently uses a single unnamed dense vector (1024-dim cosine).
Named vectors (vector_chunk, vector_questions, vector_sparse) and RRF hybrid search
will be enabled after collection migration. See SPEC-KB-007 follow-up.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    FieldCondition,
    Filter,
    MatchValue,
)

from retrieval_api.config import settings
from retrieval_api.models import RetrieveRequest

logger = logging.getLogger(__name__)

_client: AsyncQdrantClient | None = None


def _get_client() -> AsyncQdrantClient:
    global _client
    if _client is None:
        _client = AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key or None,
            timeout=5.0,
        )
    return _client


def _invalid_at_filter() -> Filter:
    """Build a filter that excludes chunks where invalid_at is set and in the past."""
    now_iso = datetime.now(timezone.utc).isoformat()
    return Filter(
        should=[
            FieldCondition(key="invalid_at", match=MatchValue(value=None)),
            FieldCondition(
                key="invalid_at",
                range={"gt": now_iso},
            ),
        ]
    )


def _scope_filter(request: RetrieveRequest) -> list[FieldCondition]:
    """Build scope-specific Qdrant filter conditions for klai_knowledge."""
    conditions = [
        FieldCondition(key="org_id", match=MatchValue(value=request.org_id)),
    ]
    if request.scope == "personal":
        if request.user_id:
            conditions.append(
                FieldCondition(key="user_id", match=MatchValue(value=request.user_id))
            )
    # For "org" and "both", we just filter by org_id (returns all)
    # "both" intentionally includes personal + org chunks
    return conditions


async def _search_notebook(
    query_vector: list[float],
    request: RetrieveRequest,
    candidates: int,
) -> list[dict]:
    """Dense cosine search on klai_focus collection (single unnamed vector)."""
    client = _get_client()

    must_conditions = [
        FieldCondition(key="tenant_id", match=MatchValue(value=request.org_id)),
    ]
    if request.notebook_id:
        must_conditions.append(
            FieldCondition(key="notebook_id", match=MatchValue(value=request.notebook_id))
        )

    try:
        result = await asyncio.wait_for(
            client.query_points(
                collection_name=settings.qdrant_focus_collection,
                query=query_vector,
                query_filter=Filter(must=must_conditions),
                limit=candidates,
                with_payload=True,
            ),
            timeout=5.0,
        )
    except (asyncio.TimeoutError, Exception) as exc:
        logger.error("Qdrant search failed on %s: %s", settings.qdrant_focus_collection, exc)
        raise

    return [
        {
            "chunk_id": str(r.id),
            "text": r.payload.get("content", r.payload.get("text", "")),
            "score": r.score,
            "artifact_id": r.payload.get("artifact_id"),
            "content_type": r.payload.get("content_type"),
            "context_prefix": r.payload.get("context_prefix"),
            "scope": "notebook",
            "valid_at": r.payload.get("valid_at"),
            "invalid_at": r.payload.get("invalid_at"),
        }
        for r in result.points
    ]


async def _search_knowledge(
    query_vector: list[float],
    request: RetrieveRequest,
    candidates: int,
) -> list[dict]:
    """Dense cosine search on klai_knowledge (single unnamed vector).

    TODO: Upgrade to RRF hybrid search once klai_knowledge is migrated
    to named vectors (vector_chunk, vector_questions, vector_sparse).
    """
    client = _get_client()

    scope_conditions = _scope_filter(request)
    combined_filter = Filter(must=[*scope_conditions])

    try:
        result = await asyncio.wait_for(
            client.query_points(
                collection_name=settings.qdrant_collection,
                query=query_vector,
                query_filter=combined_filter,
                limit=candidates,
                with_payload=True,
            ),
            timeout=5.0,
        )
    except (asyncio.TimeoutError, Exception) as exc:
        logger.error("Qdrant search failed on %s: %s", settings.qdrant_collection, exc)
        raise

    return [
        {
            "chunk_id": str(r.id),
            "text": r.payload.get("text", ""),
            "score": r.score,
            "artifact_id": r.payload.get("artifact_id"),
            "content_type": r.payload.get("content_type"),
            "context_prefix": r.payload.get("context_prefix"),
            "scope": r.payload.get("scope"),
            "valid_at": r.payload.get("valid_at"),
            "invalid_at": r.payload.get("invalid_at"),
        }
        for r in result.points
    ]


async def hybrid_search(
    query_vector: list[float],
    request: RetrieveRequest,
    candidates: int,
) -> list[dict]:
    """Run Qdrant search appropriate for the request scope.

    Returns raw result dicts with text, score, and payload fields.
    """
    if request.scope == "notebook":
        return await _search_notebook(query_vector, request, candidates)

    if request.scope == "broad":
        # Parallel queries on both collections, merge by score
        knowledge_task = _search_knowledge(query_vector, request, candidates)
        notebook_task = _search_notebook(query_vector, request, candidates)

        knowledge_results, notebook_results = await asyncio.gather(
            knowledge_task, notebook_task, return_exceptions=True
        )

        merged: list[dict] = []
        if not isinstance(knowledge_results, BaseException):
            merged.extend(knowledge_results)
        else:
            logger.warning("Knowledge search failed in broad scope: %s", knowledge_results)

        if not isinstance(notebook_results, BaseException):
            merged.extend(notebook_results)
        else:
            logger.warning("Notebook search failed in broad scope: %s", notebook_results)

        # Sort by score descending, take top candidates
        merged.sort(key=lambda x: x["score"], reverse=True)
        return merged[:candidates]

    # org, personal, both
    return await _search_knowledge(query_vector, request, candidates)
