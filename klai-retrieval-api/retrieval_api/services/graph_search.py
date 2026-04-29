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
import math

import structlog
from graphiti_core import Graphiti
from graphiti_core.driver.falkordb_driver import FalkorDriver
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient

from retrieval_api.config import settings

logger = structlog.get_logger()

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
    except TimeoutError:
        logger.warning(
            "graph_search_timeout",
            org_id=org_id,
            timeout_s=settings.graph_search_timeout,
        )
        return []
    except Exception:
        # SPEC-SEC-HYGIENE-001 REQ-43.3: exc_info=True preserves the
        # traceback that the previous `error=str(exc)` dropped (TRY401).
        logger.warning("graph_search_failed", org_id=org_id, exc_info=True)
        return []


def _convert_results(results: list, top_k: int) -> list[dict]:
    """Convert Graphiti search results to chunk-compatible format for RRF merge.

    Graphiti search returns EdgeResult / EntityEdge objects. Key fields:
    - .fact or .name: the text content
    - .score: semantic relevance from Graphiti (cosine similarity)
    - .weight: Hebbian reinforcement count (incremented per confirming episode)
    - .uuid: unique identifier

    Scoring: base semantic score boosted by log-scaled Hebbian weight.
    Results are sorted by combined score so RRF uses the correct rank ordering.
    """
    converted = []
    for i, r in enumerate(results):
        text = (
            getattr(r, "fact", None)
            or getattr(r, "name", None)
            or getattr(r, "content", None)
            or str(r)
        )
        score_val = getattr(r, "score", None)
        weight_val = getattr(r, "weight", None)

        base = float(score_val) if score_val is not None else 1.0 / (i + 1)
        if weight_val is not None and float(weight_val) > 0:
            # Hebbian boost: log scale prevents unbounded growth as weight accumulates.
            # Factor 0.1 keeps the boost modest (weight=10 → +24%, weight=100 → +46%).
            boost = 1.0 + 0.1 * math.log1p(float(weight_val))
            score = base * boost
        else:
            score = base

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

    converted.sort(key=lambda x: x["score"], reverse=True)
    return converted[:top_k]
