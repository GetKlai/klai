"""Graphiti knowledge graph integration for knowledge-ingest.

Uses graphiti-core[falkordb] to build a knowledge graph alongside the Qdrant vector store.
Episodes are ingested asynchronously after Qdrant upsert — failures are non-fatal.

LLM client: OpenAIGenericClient pointing at LiteLLM proxy (AC-14).
Graph DB: FalkorDB via FalkorDriver (AC-11).
Tenant isolation: every episode uses group_id=org_id (AC-10).
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

try:
    from graphiti_core import Graphiti
    from graphiti_core.driver.falkordb_driver import FalkorDriver
    from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
    from graphiti_core.llm_client.config import LLMConfig
    from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
    from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
    from graphiti_core.nodes import EpisodeType
    _GRAPHITI_AVAILABLE = True
except ImportError:
    _GRAPHITI_AVAILABLE = False  # graphiti-core not installed yet; added in /run SPEC-KB-011

import structlog

from knowledge_ingest.config import settings

logger = structlog.get_logger()

# Rate-limit Graphiti episodes: each add_episode() makes ~5 LLM calls internally.
# With Mistral rate limits (60 req/min for klai-large), concurrent episodes cause timeouts.
_episode_semaphore = asyncio.Semaphore(1)
EPISODE_DELAY = 5  # seconds between episodes — prevents LLM rate-limit bursts

_graphiti_client: Graphiti | None = None


def _get_graphiti() -> "Graphiti":
    """Return the shared Graphiti client (lazy init, process-singleton)."""
    if not _GRAPHITI_AVAILABLE:
        raise RuntimeError("graphiti-core is not installed — add it in /run SPEC-KB-011")
    global _graphiti_client
    if _graphiti_client is None:
        api_key = settings.litellm_api_key or "dummy"
        litellm_base_url = f"{settings.litellm_url}/v1"
        llm_config = LLMConfig(
            base_url=litellm_base_url,
            model=settings.graphiti_llm_model,
            api_key=api_key,
        )
        llm_client = OpenAIGenericClient(config=llm_config)
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
            cross_encoder=OpenAIRerankerClient(client=llm_client, config=llm_config),
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
    episode_result: str | None = None

    async with _episode_semaphore:
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
                    artifact_id=artifact_id,
                    org_id=org_id,
                    episode_id=episode_id,
                    entity_count=getattr(result, "entity_count", 0),
                    edge_count=getattr(result, "edge_count", 0),
                    ingest_ms=round(ingest_ms, 1),
                )
                episode_result = episode_id
                break

            except Exception as exc:
                if attempt < max_attempts - 1:
                    wait = 2**attempt  # 1s, 2s
                    logger.warning(
                        "graphiti_ingest_retry",
                        attempt=attempt + 1,
                        max_attempts=max_attempts,
                        artifact_id=artifact_id,
                        error=str(exc),
                        wait_s=wait,
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.warning(
                        "graphiti_ingest_failed",
                        artifact_id=artifact_id,
                        attempts=max_attempts,
                        error=str(exc),
                    )

    # Delay after releasing semaphore to space out LLM calls across episodes
    await asyncio.sleep(EPISODE_DELAY)
    return episode_result
