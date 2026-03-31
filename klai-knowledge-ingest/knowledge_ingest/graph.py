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
from datetime import UTC, datetime

import httpx

try:
    from graphiti_core import Graphiti
    from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
    from graphiti_core.driver.falkordb_driver import FalkorDriver
    from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
    from graphiti_core.llm_client.config import LLMConfig
    from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
    from graphiti_core.nodes import EpisodeType
    from openai import AsyncOpenAI
    _GRAPHITI_AVAILABLE = True
except ImportError:
    _GRAPHITI_AVAILABLE = False  # graphiti-core not installed yet; added in /run SPEC-KB-011

import structlog

import knowledge_ingest.qdrant_store as qdrant_store
from knowledge_ingest.config import settings

logger = structlog.get_logger()

# Rate-limit Graphiti episodes: each add_episode() makes ~5 LLM calls internally.
# Concurrency controlled by GRAPHITI_MAX_CONCURRENT env var (default: 1).
_episode_semaphore: asyncio.Semaphore | None = None


class _TokenBucketLimiter:
    """Token bucket: enforces at most `rate` HTTP calls per second, no burst.

    Applied to the AsyncOpenAI httpx transport so every LLM call Graphiti makes
    internally (entity extraction, deduplication, etc.) is throttled — regardless
    of how fast the upstream API responds.
    """

    def __init__(self, rate: float) -> None:
        self._min_interval = 1.0 / rate
        self._lock = asyncio.Lock()
        self._next_allowed: float = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            loop = asyncio.get_event_loop()
            now = loop.time()
            wait = self._next_allowed - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._next_allowed = loop.time() + self._min_interval


class _RateLimitedTransport(httpx.AsyncBaseTransport):
    def __init__(self, wrapped: httpx.AsyncBaseTransport, limiter: _TokenBucketLimiter) -> None:
        self._wrapped = wrapped
        self._limiter = limiter

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await self._limiter.acquire()
        return await self._wrapped.handle_async_request(request)


def _get_semaphore() -> asyncio.Semaphore:
    global _episode_semaphore
    if _episode_semaphore is None:
        _episode_semaphore = asyncio.Semaphore(settings.graphiti_max_concurrent)
    return _episode_semaphore

_graphiti_client: Graphiti | None = None


def _get_graphiti() -> Graphiti:
    """Return the shared Graphiti client (lazy init, process-singleton)."""
    if not _GRAPHITI_AVAILABLE:
        raise RuntimeError("graphiti-core is not installed — add it in /run SPEC-KB-011")
    global _graphiti_client
    if _graphiti_client is None:
        api_key = settings.litellm_api_key or "dummy"
        litellm_base_url = f"{settings.litellm_url}/v1"
        logger.info(
            "graphiti_client_init",
            llm_base_url=litellm_base_url,
            model=settings.graphiti_llm_model,
            embedder_url=f"{settings.tei_url}/v1",
        )
        llm_config = LLMConfig(
            base_url=litellm_base_url,
            model=settings.graphiti_llm_model,
            api_key=api_key,
        )
        # max_retries=0: 429s surface immediately to our ingest_episode() retry loop
        # instead of being silently swallowed by the openai client for minutes.
        # Token bucket transport: throttles every HTTP call Graphiti makes internally
        # (entity extraction, deduplication, embedding, etc.) to graphiti_llm_rps req/s.
        # This prevents bursts that would exceed the upstream Mistral 1 req/s org limit.
        _llm_limiter = _TokenBucketLimiter(rate=settings.graphiti_llm_rps)
        openai_client = AsyncOpenAI(
            api_key=api_key,
            base_url=litellm_base_url,
            max_retries=0,
            http_client=httpx.AsyncClient(
                transport=_RateLimitedTransport(
                    wrapped=httpx.AsyncHTTPTransport(),
                    limiter=_llm_limiter,
                )
            ),
        )
        llm_client = OpenAIGenericClient(config=llm_config, client=openai_client)
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



async def _update_edge_weights(
    nodes: list,
    org_id: str,
) -> int:
    """Increment weight on RELATES_TO edges between entities from this episode.

    Hebbian-style reinforcement: edges confirmed by more episodes get higher
    weight, making them rank higher in search results.
    """
    entity_uuids = [str(getattr(n, "uuid", "")) for n in nodes if getattr(n, "uuid", None)]
    if len(entity_uuids) < 2:
        return 0

    graphiti = _get_graphiti()
    driver = graphiti.driver.clone(org_id)
    result = await driver.execute_query(
        "MATCH (a:Entity)-[r:RELATES_TO]->(b:Entity) "
        "WHERE a.uuid IN $uuids AND b.uuid IN $uuids AND a <> b "
        "SET r.weight = COALESCE(r.weight, 0) + 1 "
        "RETURN count(r) AS updated",
        uuids=entity_uuids,
    )
    updated = 0
    # execute_query returns (records: list[dict], header, summary)
    if result is not None:
        records, _, _ = result
        if records:
            updated = records[0].get("updated", 0)
    return updated

async def delete_kb_episodes(org_id: str, episode_ids: list[str]) -> None:
    """Delete FalkorDB nodes for a set of episodes within an org's graph.

    Deletes:
    1. Episodic nodes whose uuid matches any episode_id (DETACH DELETE removes incident edges).
    2. Entity nodes that are no longer connected to any remaining Episodic node.

    No-op when graphiti is disabled or episode_ids is empty.
    """
    if not settings.graphiti_enabled or not episode_ids:
        return
    graphiti = _get_graphiti()
    driver = graphiti.driver.clone(org_id)
    await driver.execute_query(
        "MATCH (e:Episodic) WHERE e.uuid IN $uuids DETACH DELETE e",
        uuids=episode_ids,
    )
    # Delete Entity nodes no longer referenced by any Episodic node.
    await driver.execute_query(
        "MATCH (n:Entity) WHERE NOT ((:Episodic)--(n)) DETACH DELETE n",
    )
    logger.info("graph_kb_episodes_deleted", org_id=org_id, count=len(episode_ids))


async def compute_entity_pagerank(org_id: str) -> dict[str, float]:
    """Compute PageRank scores for all Entity nodes in the org's graph.

    Uses FalkorDB's native pagerank.stream() algorithm — no external library needed.
    Returns {entity_uuid: score}. Returns empty dict when graph is too small or on error.
    """
    if not settings.graphiti_enabled:
        return {}

    graphiti = _get_graphiti()
    driver = graphiti.driver.clone(org_id)
    try:
        result = await driver.execute_query(
            "CALL algo.pageRank('Entity', 'RELATES_TO') "
            "YIELD node, score "
            "RETURN node.uuid AS uuid, score",
        )
        if result is None:
            return {}
        records, _, _ = result
        return {r["uuid"]: float(r["score"]) for r in records if r.get("uuid")}
    except Exception as exc:
        logger.warning("pagerank_compute_failed", org_id=org_id, error=str(exc))
        return {}


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
    reference_time = datetime.fromtimestamp(belief_time_start, tz=UTC)

    max_attempts = 3
    episode_result: str | None = None

    async with _get_semaphore():
        for attempt in range(max_attempts):
            try:
                logger.info(
                    "graphiti_episode_start",
                    artifact_id=artifact_id,
                    attempt=attempt + 1,
                    model=settings.graphiti_llm_model,
                    litellm_url=settings.litellm_url,
                )
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

                # Extract episode_id — add_episode returns AddEpisodeResults
                # with .episode (EpisodicNode), .nodes, .edges
                episode_id: str | None = None
                if result is not None:
                    ep_node = getattr(result, "episode", None)
                    if ep_node is not None:
                        episode_id = str(getattr(ep_node, "uuid", "")) or None
                    nodes = getattr(result, "nodes", [])
                    edges = getattr(result, "edges", [])
                else:
                    nodes = []
                    edges = []

                logger.info(
                    "graphiti_episode_ingested",
                    artifact_id=artifact_id,
                    org_id=org_id,
                    episode_id=episode_id,
                    entity_count=len(nodes),
                    edge_count=len(edges),
                    ingest_ms=round(ingest_ms, 1),
                )
                episode_result = episode_id

                # Hebbian reinforcement: increment weight on edges between
                # entities co-mentioned in this episode
                if len(nodes) >= 2:
                    try:
                        wt_count = await _update_edge_weights(nodes, org_id)
                        if wt_count:
                            logger.debug(
                                "graphiti_edge_weights_updated",
                                artifact_id=artifact_id,
                                edges_updated=wt_count,
                            )
                    except Exception as wt_exc:
                        logger.warning(
                            "graphiti_edge_weights_failed",
                            artifact_id=artifact_id,
                            error=str(wt_exc),
                        )

                # Store entity UUIDs + PageRank scores in Qdrant for retrieval boosting
                entity_uuids_list = [
                    str(getattr(n, "uuid", ""))
                    for n in nodes
                    if getattr(n, "uuid", None)
                ]
                if entity_uuids_list:
                    try:
                        pagerank_scores = await compute_entity_pagerank(org_id)
                        await qdrant_store.set_entity_graph_data(
                            artifact_id=artifact_id,
                            org_id=org_id,
                            entity_uuids=entity_uuids_list,
                            pagerank_scores=pagerank_scores,
                        )
                    except Exception as eg_exc:
                        logger.warning(
                            "entity_graph_data_failed",
                            artifact_id=artifact_id,
                            error=str(eg_exc),
                        )

                break

            except Exception as exc:
                exc_str = str(exc).lower()
                is_rate_limit = "rate limit" in exc_str or "429" in exc_str or "ratelimit" in exc_str  # noqa: E501
                if attempt < max_attempts - 1:
                    # Rate limit: back off long enough for Mistral's sliding window to reset.
                    # Other errors: short exponential backoff (1s, 2s).
                    wait = 30 * (2**attempt) if is_rate_limit else 2**attempt  # 30s/60s or 1s/2s
                    logger.warning(
                        "graphiti_ingest_retry",
                        attempt=attempt + 1,
                        max_attempts=max_attempts,
                        artifact_id=artifact_id,
                        error=str(exc),
                        wait_s=wait,
                        rate_limited=is_rate_limit,
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.warning(
                        "graphiti_ingest_failed",
                        artifact_id=artifact_id,
                        attempts=max_attempts,
                        error=str(exc),
                    )

        # Delay INSIDE semaphore — small breathing room between episodes.
        # Intra-episode rate limiting is handled by _RateLimitedTransport.
        await asyncio.sleep(settings.graphiti_episode_delay)

    return episode_result
