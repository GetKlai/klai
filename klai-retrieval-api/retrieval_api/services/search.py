"""Qdrant search across klai_knowledge and klai_focus.

klai_knowledge uses named vectors (vector_chunk, vector_questions, vector_sparse)
with 3-leg RRF fusion. Falls back to 2-leg RRF when sparse vector is unavailable.
klai_focus uses a single unnamed dense vector.
"""

from __future__ import annotations

import asyncio
import logging
import warnings
from datetime import datetime, timezone

# Qdrant client warns about API key over HTTP; safe inside Docker network
warnings.filterwarnings("ignore", message="Api key is used with an insecure connection")

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    FieldCondition,
    Filter,
    Fusion,
    FusionQuery,
    MatchAny,
    MatchValue,
    Prefetch,
    SparseVector,
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


def _scope_filter(request: RetrieveRequest) -> list[FieldCondition | Filter]:
    """Build scope-specific Qdrant filter conditions for klai_knowledge.

    Visibility enforcement: chunks with visibility='private' are excluded from org/both
    scopes unless the requesting user owns them (matched via user_id).
    """
    conditions: list[FieldCondition | Filter] = [
        FieldCondition(key="org_id", match=MatchValue(value=request.org_id)),
    ]
    if request.scope == "personal":
        if request.user_id:
            conditions.append(
                FieldCondition(key="user_id", match=MatchValue(value=request.user_id))
            )
        # personal scope is already restricted to one user; no visibility filter needed
    else:
        # org / both: exclude private chunks that do not belong to the requesting user
        not_private = Filter(
            must_not=[FieldCondition(key="visibility", match=MatchValue(value="private"))]
        )
        visibility_should: list[Filter] = [not_private]
        if request.user_id:
            visibility_should.append(
                Filter(must=[
                    FieldCondition(key="visibility", match=MatchValue(value="private")),
                    FieldCondition(key="user_id", match=MatchValue(value=request.user_id)),
                ])
            )
        conditions.append(Filter(should=visibility_should))
    if request.kb_slugs:
        if request.scope == "both" and request.user_id:
            # kb_slugs is an org-only filter. When scope=both, personal chunks must not be
            # excluded by the slug filter — a chunk passes if it matches a slug OR belongs
            # to the requesting user (personal ownership bypass).
            conditions.append(Filter(should=[
                FieldCondition(key="kb_slug", match=MatchAny(any=request.kb_slugs)),
                FieldCondition(key="user_id", match=MatchValue(value=request.user_id)),
            ]))
        else:
            conditions.append(
                FieldCondition(key="kb_slug", match=MatchAny(any=request.kb_slugs))
            )
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
    sparse_vector: SparseVector | None = None,
) -> list[dict]:
    """3-leg RRF hybrid search on klai_knowledge (named vectors).

    Prefetch legs: vector_chunk + vector_questions (always) + vector_sparse (when available).
    Falls back to 2-leg RRF when sparse_vector is None.
    """
    client = _get_client()

    scope_conditions = _scope_filter(request)
    query_filter = Filter(must=[*scope_conditions])

    prefetch_limit = max(candidates * 4, 20)
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

    try:
        result = await asyncio.wait_for(
            client.query_points(
                collection_name=settings.qdrant_collection,
                prefetch=prefetch,
                query=FusionQuery(fusion=Fusion.RRF),
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
    sparse_vector: SparseVector | None = None,
) -> list[dict]:
    """Run Qdrant search appropriate for the request scope.

    Returns raw result dicts with text, score, and payload fields.
    sparse_vector is forwarded to _search_knowledge for 3-leg RRF.
    """
    if request.scope == "notebook":
        return await _search_notebook(query_vector, request, candidates)

    if request.scope == "broad":
        # Parallel queries on both collections, merge by score
        knowledge_task = _search_knowledge(query_vector, request, candidates, sparse_vector)
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
    return await _search_knowledge(query_vector, request, candidates, sparse_vector)
