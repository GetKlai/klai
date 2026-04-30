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


if _GRAPHITI_AVAILABLE:
    from collections.abc import Iterable

    from graphiti_core.embedder.client import EmbedderClient

    class _BatchSplittingEmbedder(EmbedderClient):
        """Wraps an OpenAIEmbedder to split large batches into sub-batches.

        Graphiti's OpenAIEmbedder.create_batch() sends all items in a single API
        call. TEI enforces --max-client-batch-size (default 32). As the FalkorDB
        graph grows, entity resolution batches exceed this limit.

        This wrapper queries TEI's /info endpoint once to discover the actual limit,
        then splits create_batch() calls into sub-batches. Falls back to per-item
        embedding if a sub-batch fails (following graphiti_core's Gemini pattern).
        """

        def __init__(
            self,
            inner: OpenAIEmbedder,
            tei_base_url: str,
            default_batch_size: int = 32,
        ) -> None:
            self._inner = inner
            self.config = inner.config
            self._tei_base_url = tei_base_url.rstrip("/").removesuffix("/v1")
            self._batch_size = default_batch_size
            self._resolved = False

        async def _resolve_batch_size(self) -> int:
            if self._resolved:
                return self._batch_size
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(f"{self._tei_base_url}/info")
                    resp.raise_for_status()
                    info = resp.json()
                    server_max = info.get("max_client_batch_size")
                    if server_max and isinstance(server_max, int) and server_max > 0:
                        self._batch_size = server_max
                        self._resolved = True
                        logger.info(
                            "tei_batch_size_resolved",
                            max_client_batch_size=self._batch_size,
                        )
            except Exception as exc:
                logger.warning(
                    "tei_info_query_failed",
                    error=str(exc),
                    default_batch_size=self._batch_size,
                )
            return self._batch_size

        async def create(
            self,
            input_data: str | list[str] | Iterable[int] | Iterable[Iterable[int]],
        ) -> list[float]:
            return await self._inner.create(input_data)

        async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
            if not input_data_list:
                return []

            batch_size = await self._resolve_batch_size()

            if len(input_data_list) <= batch_size:
                return await self._inner.create_batch(input_data_list)

            logger.info(
                "embedding_batch_splitting",
                total=len(input_data_list),
                batch_size=batch_size,
                sub_batches=(len(input_data_list) + batch_size - 1) // batch_size,
            )

            all_embeddings: list[list[float]] = []
            for i in range(0, len(input_data_list), batch_size):
                sub_batch = input_data_list[i : i + batch_size]
                try:
                    result = await self._inner.create_batch(sub_batch)
                    all_embeddings.extend(result)
                except Exception as exc:
                    logger.warning(
                        "embedding_sub_batch_failed",
                        sub_batch_index=i // batch_size,
                        sub_batch_size=len(sub_batch),
                        error=str(exc),
                    )
                    for item in sub_batch:
                        embedding = await self._inner.create(item)
                        all_embeddings.append(embedding)

            return all_embeddings


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
        embedder = _BatchSplittingEmbedder(
            inner=OpenAIEmbedder(
                config=OpenAIEmbedderConfig(
                    base_url=f"{settings.tei_url}/v1",
                    api_key=api_key,
                    embedding_model="bge-m3",
                    embedding_dim=1024,
                )
            ),
            tei_base_url=settings.tei_url,
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


async def sweep_orphan_episodes_org_wide(org_id: str, alive_episode_uuids: set[str]) -> int:
    """ORG-WIDE sweep of FalkorDB episodes whose ``uuid`` is no longer
    referenced by any artifact in postgres.

    Graphiti's Episodic node-schema has ``uuid``, ``name``, ``group_id``,
    ``source``, ``source_description``, ``valid_at``, ``created_at`` —
    but NO ``artifact_id`` property. The ingest pipeline links postgres
    -> FalkorDB by writing the FalkorDB ``Episodic.uuid`` into
    ``knowledge.artifacts.extra->>'graphiti_episode_id'``.

    Implementation uses the direct ``falkordb`` Python client (the same
    pattern as ``routes/stats.py::get_graph_stats``). The earlier
    attempt via ``graphiti.driver.execute_query`` returned an empty
    result_set silently because the FalkorDB driver and the Neo4j
    driver have different return shapes — proven on live e2e:
    ``alive_episode_count: 0`` while FalkorDB clearly held 31 episodes.

    Lists every Episodic uuid in the org graph, intersects with the
    alive set, DETACH DELETEs the difference, then sweeps Entities
    that lost all incident episodes.

    Returns count of episodes deleted. No-op when graphiti is disabled.
    """
    if not settings.graphiti_enabled:
        return 0
    try:
        from falkordb import FalkorDB as FalkorDBClient
    except ImportError:
        logger.warning("falkordb_client_unavailable_for_sweep", org_id=org_id)
        return 0

    client = FalkorDBClient(host=settings.falkordb_host, port=settings.falkordb_port)
    graph = client.select_graph(org_id)

    list_res = graph.query("MATCH (e:Episodic) RETURN e.uuid AS uuid")
    falkor_uuids: set[str] = set()
    for row in list_res.result_set or []:
        uid = row[0] if row else None
        if uid:
            falkor_uuids.add(str(uid))

    orphan_uuids = falkor_uuids - alive_episode_uuids
    if not orphan_uuids:
        logger.info(
            "graph_orphan_sweep_clean",
            org_id=org_id,
            falkor_episodes=len(falkor_uuids),
            alive=len(alive_episode_uuids),
        )
        return 0

    del_res = graph.query(
        "MATCH (e:Episodic) WHERE e.uuid IN $uuids "
        "WITH e, e.uuid AS uuid "
        "DETACH DELETE e "
        "RETURN count(uuid) AS deleted",
        params={"uuids": list(orphan_uuids)},
    )
    deleted = 0
    if del_res.result_set:
        deleted = int(del_res.result_set[0][0] or 0)
    if deleted:
        graph.query("MATCH (n:Entity) WHERE NOT ((:Episodic)--(n)) DETACH DELETE n")
    logger.info(
        "graph_orphan_episodes_swept",
        org_id=org_id,
        scanned=len(falkor_uuids),
        alive=len(alive_episode_uuids),
        orphan_uuids=len(orphan_uuids),
        episodes_deleted=deleted,
    )
    return deleted


async def delete_orphan_episodes_for_artifact_ids(org_id: str, artifact_ids: list[str]) -> int:
    """Janitor: drop FalkorDB episodes whose ``artifact_id`` is in the given list.

    SPEC-CONNECTOR-DELETE-LIFECYCLE-001 follow-up. Some Graphiti tasks
    do synchronous LLM calls that don't honour ``asyncio.CancelledError``
    — they keep running after the procrastinate cancel and write a fresh
    episode for an already-deleted artifact. Those episodes never made
    it into ``knowledge.artifacts.extra->>graphiti_episode_id`` (the row
    was already gone), so ``delete_kb_episodes`` cannot find them via
    the normal path.

    The orchestrator runs this AFTER ``delete_connector_artifacts`` with
    the artifact-id snapshot taken BEFORE the delete: any episode in
    FalkorDB referring to those artifact-ids is by definition orphan
    (the artifact does not exist in postgres anymore).

    Also cleans Entity nodes that lose all incident Episodic edges
    after the delete — same pattern as ``delete_kb_episodes``.

    Returns the count of Episodic nodes deleted. No-op when graphiti is
    disabled or ``artifact_ids`` is empty.
    """
    if not settings.graphiti_enabled or not artifact_ids:
        return 0
    graphiti = _get_graphiti()
    driver = graphiti.driver.clone(org_id)
    result = await driver.execute_query(
        "MATCH (e:Episodic) WHERE e.artifact_id IN $artifact_ids "
        "WITH e, e.uuid AS uuid "
        "DETACH DELETE e "
        "RETURN count(uuid) AS deleted",
        artifact_ids=artifact_ids,
    )
    deleted = 0
    if result is not None:
        records, _, _ = result
        if records:
            deleted = int(records[0].get("deleted", 0) or 0)
    if deleted:
        # Entities now potentially orphaned by the episode-delete above.
        await driver.execute_query(
            "MATCH (n:Entity) WHERE NOT ((:Episodic)--(n)) DETACH DELETE n",
        )
    logger.info(
        "graph_orphan_episodes_deleted",
        org_id=org_id,
        artifact_count=len(artifact_ids),
        episodes_deleted=deleted,
    )
    return deleted


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
                    str(getattr(n, "uuid", "")) for n in nodes if getattr(n, "uuid", None)
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
                is_rate_limit = (
                    "rate limit" in exc_str or "429" in exc_str or "ratelimit" in exc_str
                )
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
