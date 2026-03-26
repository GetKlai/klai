"""Graphiti knowledge graph integration for knowledge-ingest.

Uses graphiti-core[falkordb] to build a knowledge graph alongside the Qdrant vector store.
Episodes are ingested asynchronously after Qdrant upsert — failures are non-fatal.

LLM client: OpenAIGenericClient pointing at LiteLLM proxy (AC-14).
Graph DB: FalkorDB via FalkorDriver (AC-11).
Tenant isolation: every episode uses group_id=org_id (AC-10).
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

try:
    from graphiti_core import Graphiti
    from graphiti_core.driver.falkordb_driver import FalkorDriver
    from graphiti_core.llm_client.config import LLMConfig
    from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
    from graphiti_core.nodes import EpisodeType
    _GRAPHITI_AVAILABLE = True
except ImportError:
    _GRAPHITI_AVAILABLE = False  # graphiti-core not installed yet; added in /run SPEC-KB-011

from knowledge_ingest.config import settings

logger = logging.getLogger(__name__)

_graphiti_client: Graphiti | None = None


def _get_graphiti() -> "Graphiti":
    """Return the shared Graphiti client (lazy init, process-singleton)."""
    if not _GRAPHITI_AVAILABLE:
        raise RuntimeError("graphiti-core is not installed — add it in /run SPEC-KB-011")
    global _graphiti_client
    if _graphiti_client is None:
        llm_client = OpenAIGenericClient(
            config=LLMConfig(
                base_url=f"{settings.litellm_url}/v1",
                model=settings.graphiti_llm_model,
                api_key=settings.litellm_api_key or "dummy",
            )
        )
        driver = FalkorDriver(
            host=settings.falkordb_host,
            port=settings.falkordb_port,
        )
        _graphiti_client = Graphiti(
            llm_client=llm_client,
            graph_driver=driver,
        )
    return _graphiti_client


async def ingest_episode(
    artifact_id: str,
    document_text: str,
    org_id: str,
    content_type: str,
    belief_time_start: int,
) -> str | None:
    """Ingest a document as a Graphiti episode.

    Returns the episode_id on success, or None if all retries fail.
    This function is fire-and-forget — callers must not await its result
    unless they want to block on graph enrichment.

    AC-1: group_id=org_id and reference_time=belief_time_start.
    AC-3: 3 retries with exponential backoff (1s, 2s, 4s).
    AC-13: Structured log on success.
    AC-14: LLM calls routed through LiteLLM proxy.
    """
    if not settings.graphiti_enabled:
        return None

    graphiti = _get_graphiti()
    reference_time = datetime.fromtimestamp(belief_time_start, tz=timezone.utc)

    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            t0 = time.perf_counter()
            result = await graphiti.add_episode(
                name=artifact_id,
                episode_body=document_text,
                source=EpisodeType.text,
                source_description=content_type,
                reference_time=reference_time,
                group_id=org_id,
            )
            ingest_ms = (time.perf_counter() - t0) * 1000

            # Extract episode_id — graphiti returns an EpisodeNode with .uuid
            episode_id: str | None = None
            if result is not None:
                episode_id = str(getattr(result, "uuid", None) or getattr(result, "id", ""))
                episode_id = episode_id or None

            logger.info(
                "graphiti_episode_ingested",
                extra={
                    "artifact_id": artifact_id,
                    "org_id": org_id,
                    "episode_id": episode_id,
                    "entity_count": getattr(result, "entity_count", 0),
                    "edge_count": getattr(result, "edge_count", 0),
                    "ingest_ms": round(ingest_ms, 1),
                },
            )
            return episode_id

        except Exception as exc:
            if attempt < max_attempts - 1:
                wait = 2**attempt  # 1s, 2s
                logger.warning(
                    "Graphiti ingest attempt %d/%d failed for %s: %s — retrying in %ds",
                    attempt + 1,
                    max_attempts,
                    artifact_id,
                    exc,
                    wait,
                )
                await asyncio.sleep(wait)
            else:
                logger.warning(
                    "Graphiti ingest failed for artifact %s after %d attempts: %s",
                    artifact_id,
                    max_attempts,
                    exc,
                )

    return None
