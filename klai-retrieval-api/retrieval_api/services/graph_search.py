"""Graphiti graph search service for retrieval-api.

Queries FalkorDB via Graphiti for entity/edge results and converts them to
chunk-compatible dicts for RRF merging with Qdrant results.

AC-5:  parallel execution with Qdrant search.
AC-7:  returns [] on timeout or error (graceful degradation).
AC-8:  returns [] immediately when GRAPHITI_ENABLED=false.
AC-10: group_ids=[org_id] enforces tenant isolation.
"""

from __future__ import annotations

import asyncio
import math
import re

import structlog
from graphiti_core import Graphiti
from graphiti_core.driver.falkordb_driver import FalkorDriver
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
from graphiti_core.nodes import EpisodicNode
from klai_graphiti_compat import apply_falkordb_compat
from klai_kb_slugs import parse_episode_name

from retrieval_api.config import settings

# Patch the live Graphiti helper references before creating any client.
apply_falkordb_compat()

logger = structlog.get_logger()

_graphiti_client: Graphiti | None = None
# Provenance is optional garnish on the graph leg: it gets its own small
# budget so it can never cost more than the search it annotates.
_PROVENANCE_TIMEOUT = 1.0

_EMPTY_FULLTEXT_QUERY_RE = re.compile(r"\(@group_id:[^)]*\)\s+\(\s*\)(?:\s|['\"]|$)")


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


async def close() -> None:
    """Close and clear the lazy Graphiti client during service shutdown."""
    global _graphiti_client
    client = _graphiti_client
    _graphiti_client = None
    if client is not None:
        await client.close()


async def search(query: str, org_id: str, top_k: int = 20) -> list[dict]:
    """Search Graphiti for entities/edges matching query.

    Returns chunk-compatible dicts (same shape as Qdrant results) for RRF merge.
    Returns empty list on any failure — callers must not depend on graph results.
    """
    if not settings.graphiti_enabled:
        return []
    if not _has_searchable_text(query):
        return []

    graphiti = _get_graphiti()
    try:
        return await _search_with_provenance(graphiti, query, org_id, top_k)
    except TimeoutError:
        logger.warning(
            "graph_search_timeout",
            org_id=org_id,
            timeout_s=settings.graph_search_timeout,
        )
        return []
    except Exception as exc:
        if _is_empty_fulltext_query_error(exc):
            logger.info("graph_search_skipped_empty_query", org_id=org_id)
            return []
        # SPEC-SEC-HYGIENE-001 REQ-43.3: exc_info=True preserves the
        # traceback that the previous `error=str(exc)` dropped (TRY401).
        logger.warning("graph_search_failed", org_id=org_id, exc_info=True)
        return []


async def _search_with_provenance(
    graphiti: Graphiti, query: str, org_id: str, top_k: int
) -> list[dict]:
    """Search, then resolve each edge back to the artifact it came from.

    The two steps have SEPARATE budgets on purpose. Sharing one would mean a
    slow provenance lookup gets the whole coroutine cancelled by the outer
    ``wait_for``, and ``asyncio.CancelledError`` inherits from BaseException,
    so ``_resolve_episode_artifacts``'s ``except Exception`` cannot turn that
    back into "no provenance". Results already in hand would be thrown away by
    an optional lookup — worse than the behaviour before citations existed.

    Worst case is therefore ``graph_search_timeout + _PROVENANCE_TIMEOUT``,
    both bounded and explicit.
    """
    results = await asyncio.wait_for(
        graphiti.search(query, group_ids=[org_id]),
        timeout=settings.graph_search_timeout,
    )
    episode_names = await _resolve_episode_names(graphiti, results, org_id)
    return _convert_results(results, top_k, episode_names)


def _episode_uuids(edge: object) -> list[str]:
    """Episode uuids on an edge, or [] when the shape is not what we expect."""
    episodes = getattr(edge, "episodes", None)
    if not isinstance(episodes, list):
        return []
    return [uuid for uuid in episodes if isinstance(uuid, str) and uuid]


async def _resolve_episode_names(graphiti: Graphiti, results: list, org_id: str) -> dict[str, str]:
    """Map episode uuid -> episode name for the edges in ``results``.

    The name is what ties a derived fact back to a document. Two schemes are
    in the graph at once and both must keep working:

    - ``doc:<kb_slug>:<path>`` (SPEC-RAG-GRAPH-CITE-002) — the identity
      ingest dedups on, stable across re-ingest.
    - a bare ``artifact_id`` — every episode written before that change.
      artifact_id identifies a VERSION, so it stops resolving once the page
      is re-ingested; those edges heal on their next ingest.

    TENANT ISOLATION. ``EpisodicNode.get_by_uuids`` matches on uuid alone —
    it applies no group_id filter of its own. Scoping therefore rests on the
    driver being cloned to ``database=<org_id>``, the same per-tenant graph
    boundary ``graphiti.search()`` gets from ``handle_multiple_group_ids``.
    The ``group_id`` assertion below is deliberate defence in depth, not
    redundancy: it keeps the read fail-closed if a future Graphiti change
    alters how a driver resolves its database.

    Fail-open on error: returning ``{}`` costs citations, raising would cost
    the whole graph leg.
    """
    uuids = sorted({uuid for edge in results for uuid in _episode_uuids(edge)})
    if not uuids:
        return {}

    try:
        driver = graphiti.clients.driver.clone(database=org_id)
        episodes = await asyncio.wait_for(
            EpisodicNode.get_by_uuids(driver, uuids), timeout=_PROVENANCE_TIMEOUT
        )
    except TimeoutError:
        logger.warning("graph_episode_lookup_timeout", org_id=org_id, uuid_count=len(uuids))
        return {}
    except Exception:
        logger.warning("graph_episode_lookup_failed", org_id=org_id, exc_info=True)
        return {}

    resolved: dict[str, str] = {}
    foreign = 0
    for episode in episodes:
        if getattr(episode, "group_id", None) != org_id:
            foreign += 1
            continue
        name = getattr(episode, "name", None)
        uuid = getattr(episode, "uuid", None)
        if isinstance(name, str) and name and isinstance(uuid, str):
            resolved[uuid] = name
    if foreign:
        logger.warning("graph_episode_foreign_group_id_dropped", org_id=org_id, count=foreign)
    return resolved


def _iso(value: object) -> str | None:
    """Serialise Graphiti's datetimes to the ISO strings ChunkResult expects."""
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return str(isoformat())
    return str(value) if isinstance(value, str) else None


def _has_searchable_text(query: str) -> bool:
    return any(ch.isalnum() for ch in query)


def _is_empty_fulltext_query_error(exc: Exception) -> bool:
    message = str(exc)
    return "syntax" in message.lower() and _EMPTY_FULLTEXT_QUERY_RE.search(message) is not None


def _convert_results(
    results: list, top_k: int, episode_artifacts: dict[str, str] | None = None
) -> list[dict]:
    """Convert Graphiti search results to chunk-compatible format for RRF merge.

    Graphiti search returns EdgeResult / EntityEdge objects. Key fields:
    - .fact or .name: the text content
    - .score: semantic relevance from Graphiti (cosine similarity)
    - .weight: Hebbian reinforcement count (incremented per confirming episode)
    - .uuid: unique identifier
    - .episodes: uuids of the episodes the fact was extracted from
    - .valid_at / .invalid_at: Graphiti's bi-temporal validity window

    ``episode_artifacts`` maps episode uuid -> artifact_id (see
    ``_resolve_episode_artifacts``). An edge can be supported by several
    episodes; the first one that resolves is used, which is enough for a
    citation because any supporting document lets the reader verify the
    claim. Multi-source attribution belongs with knowledge.derivations.

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
        source_name = None
        for episode_uuid in _episode_uuids(r):
            source_name = (episode_artifacts or {}).get(episode_uuid)
            if source_name:
                break
        document = parse_episode_name(source_name or "")
        # A doc-key edge gets its artifact_id from the CURRENT version at
        # label time; a legacy edge carries the (possibly superseded) id it
        # was named after, which keeps it citable exactly as it is today.
        artifact_id = None if document else source_name
        converted.append(
            {
                "chunk_id": f"graph:{uid}",
                "text": text,
                "score": score,
                "artifact_id": artifact_id,
                "graph_kb_slug": document[0] if document else None,
                "graph_path": document[1] if document else None,
                "content_type": "graph_edge",
                "context_prefix": None,
                "scope": "org",
                "valid_at": _iso(getattr(r, "valid_at", None)),
                "invalid_at": _iso(getattr(r, "invalid_at", None)),
            }
        )

    converted.sort(key=lambda x: x["score"], reverse=True)
    return converted[:top_k]
