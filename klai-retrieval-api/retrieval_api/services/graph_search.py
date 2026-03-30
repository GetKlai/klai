"""Graphiti graph search service for retrieval-api.

Queries FalkorDB via Graphiti for entity/edge results and converts them to
chunk-compatible dicts for RRF merging with Qdrant results.

AC-5:  parallel execution with Qdrant search for non-notebook scopes.
AC-7:  returns [] on timeout or error (graceful degradation).
AC-8:  returns [] immediately when GRAPHITI_ENABLED=false.
AC-10: group_ids=[org_id] enforces tenant isolation.
"""
from __future__ import annotations

import asyncio
import logging

from graphiti_core import Graphiti
from graphiti_core.driver.falkordb_driver import FalkorDriver
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient

from retrieval_api.config import settings

logger = logging.getLogger(__name__)

_graphiti_client: Graphiti | None = None


def _get_graphiti() -> Graphiti:
    """Return the shared read-only Graphiti client (lazy init, process-singleton)."""
    global _graphiti_client
    if _graphiti_client is None:
        api_key = settings.litellm_api_key or "dummy"
        litellm_base_url = f"{settings.litellm_url}/v1"
        llm_client = OpenAIGenericClient(
            config=LLMConfig(
                base_url=litellm_base_url,
                model=settings.graphiti_llm_model,
                api_key=api_key,
            )
        )
        embedder = OpenAIEmbedder(
            config=OpenAIEmbedderConfig(
                base_url=f"{settings.tei_url}/v1",
                api_key=api_key,
                embedding_model="bge-m3",
                embedding_dim=1024,
            )
        )
        driver = FalkorDriver(
            host=settings.falkordb_host,
            port=settings.falkordb_port,
        )
        _graphiti_client = Graphiti(
            llm_client=llm_client,
            embedder=embedder,
            graph_driver=driver,
        )
    return _graphiti_client


async def search(query: str, org_id: str, top_k: int = 20) -> list[dict]:
    """Search Graphiti for entities/edges matching query.

    Returns chunk-compatible dicts (same shape as Qdrant results) for RRF merge.
    Returns empty list on any failure — callers must not depend on graph results.
    """
    if not settings.graphiti_enabled:
        return []

    graphiti = _get_graphiti()
    try:
        results = await asyncio.wait_for(
            graphiti.search(query, group_ids=[org_id]),
            timeout=settings.graph_search_timeout,
        )
        return _convert_results(results, top_k)
    except asyncio.TimeoutError:
        logger.warning(
            "Graphiti search timed out after %.1fs for org %s",
            settings.graph_search_timeout,
            org_id,
        )
        return []
    except Exception as exc:
        logger.warning("Graphiti search failed for org %s: %s", org_id, exc)
        return []


def _convert_results(results: list, top_k: int) -> list[dict]:
    """Convert Graphiti search results to chunk-compatible format for RRF merge.

    Graphiti search returns EdgeResult / EntityEdge objects. Key fields:
    - .fact or .name: the text content
    - .score or .weight: relevance score (may be absent — use rank as fallback)
    - .uuid: unique identifier
    """
    converted = []
    for i, r in enumerate(results[:top_k]):
        text = (
            getattr(r, "fact", None)
            or getattr(r, "name", None)
            or getattr(r, "content", None)
            or str(r)
        )
        raw_score = getattr(r, "score", None) or getattr(r, "weight", None)
        score = float(raw_score) if raw_score is not None else 1.0 / (i + 1)
        uid = str(getattr(r, "uuid", i))
        converted.append(
            {
                "chunk_id": f"graph:{uid}",
                "text": text,
                "score": score,
                "artifact_id": None,
                "content_type": "graph_edge",
                "context_prefix": None,
                "scope": "org",
                "valid_at": None,
                "invalid_at": None,
            }
        )
    return converted
