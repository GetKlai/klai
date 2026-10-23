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
import time
from collections.abc import Callable
from typing import Literal

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
apply_falkordb_compat(ann_candidate_search=settings.graph_ann_enabled)

logger = structlog.get_logger()

_graphiti_client: Graphiti | None = None
# Provenance is optional garnish on the graph leg: it gets its own small
# budget so it can never cost more than the search it annotates.
_PROVENANCE_TIMEOUT = 1.0

_EMPTY_FULLTEXT_QUERY_RE = re.compile(r"\(@group_id:[^)]*\)\s+\(\s*\)(?:\s|['\"]|$)")

# Circuit breaker on the graph leg. From 13 to 22 October 2026 the graph leg
# failed once in about 26,000 retrieve calls, so three failures inside a
# minute only happen when FalkorDB itself is down. On 23 October FalkorDB
# crashed every few minutes and each restart spent 99-105 s reloading its
# dump, answering nothing meanwhile. A 60 s cooldown lets at most two probes
# into one reload, each bounded by graph_search_timeout, and finds a recovered
# graph within a minute.
_CIRCUIT_FAILURES = 3
_CIRCUIT_WINDOW_S = 60.0
_CIRCUIT_COOLDOWN_S = 60.0


class _GraphCircuit:
    """Skip the graph after repeated failures; let one probe through per cooldown.

    ``allow`` admits a call as an ordinary ``"call"`` or as the cooldown's
    ``"probe"``, and the caller hands that back with its outcome. Only the
    probe can close an open circuit: a call admitted before the circuit opened
    may still finish afterwards, and its success says nothing about now.

    Single event loop, so no lock: every method runs without an await in it.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._failures: list[float] = []
        self._opened_at: float | None = None
        self._probing = False

    def allow(self) -> Literal["call", "probe"] | None:
        if self._opened_at is None:
            return "call"
        if self._probing or self._clock() - self._opened_at < _CIRCUIT_COOLDOWN_S:
            return None
        self._probing = True
        return "probe"

    def record_success(self, admitted: Literal["call", "probe"]) -> None:
        if admitted == "probe":
            self._probing = False
            self._opened_at = None
            self._failures.clear()
            logger.info("graph_circuit_closed")
        elif self._opened_at is None:
            self._failures.clear()

    def record_failure(self, admitted: Literal["call", "probe"]) -> None:
        now = self._clock()
        if admitted == "probe":
            self._probing = False
            self._opened_at = now
            logger.warning(
                "graph_circuit_open", reason="probe_failed", cooldown_s=_CIRCUIT_COOLDOWN_S
            )
            return
        if self._opened_at is not None:
            # A call that started before the circuit opened; already counted.
            return
        self._failures = [t for t in self._failures if now - t < _CIRCUIT_WINDOW_S] + [now]
        if len(self._failures) >= _CIRCUIT_FAILURES:
            self._failures.clear()
            self._opened_at = now
            logger.warning(
                "graph_circuit_open",
                reason="consecutive_failures",
                failures=_CIRCUIT_FAILURES,
                window_s=_CIRCUIT_WINDOW_S,
                cooldown_s=_CIRCUIT_COOLDOWN_S,
            )

    def release(self, admitted: Literal["call", "probe"]) -> None:
        """A cancelled probe proves nothing; the next call after it may probe."""
        if admitted == "probe":
            self._probing = False


_circuit = _GraphCircuit()


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


_graphiti_build: asyncio.Task[Graphiti] | None = None


def _build_graphiti() -> asyncio.Task[Graphiti]:
    """The one in-flight client construction, shared by concurrent cold starts.

    Building the client sends a synchronous INFO to FalkorDB (falkordb's
    cluster probe, no socket timeout). On the event loop that froze every
    request for as long as a reloading FalkorDB took to answer, so it runs in
    a thread; callers time out on a shield, never cancelling the build, and a
    failed build is retried by the next caller.
    """
    global _graphiti_build
    if _graphiti_build is None:
        _graphiti_build = asyncio.create_task(asyncio.to_thread(_get_graphiti))
        _graphiti_build.add_done_callback(_forget_graphiti_build)
    return _graphiti_build


def _forget_graphiti_build(task: asyncio.Task[Graphiti]) -> None:
    global _graphiti_build
    _graphiti_build = None
    if not task.cancelled():
        task.exception()  # retrieved here: every waiter may already have timed out


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
    admitted = _circuit.allow()
    if admitted is None:
        return []

    try:
        graphiti = _graphiti_client or await asyncio.wait_for(
            asyncio.shield(_build_graphiti()), timeout=settings.graph_search_timeout
        )
        results = await _search_with_provenance(graphiti, query, org_id, top_k)
    except asyncio.CancelledError:
        _circuit.release(admitted)
        raise
    except TimeoutError:
        _circuit.record_failure(admitted)
        logger.warning(
            "graph_search_timeout",
            org_id=org_id,
            timeout_s=settings.graph_search_timeout,
        )
        return []
    except Exception as exc:
        if _is_empty_fulltext_query_error(exc):
            # FalkorDB answered, so this is a healthy graph rejecting the query.
            _circuit.record_success(admitted)
            logger.info("graph_search_skipped_empty_query", org_id=org_id)
            return []
        _circuit.record_failure(admitted)
        # SPEC-SEC-HYGIENE-001 REQ-43.3: exc_info=True preserves the
        # traceback that the previous `error=str(exc)` dropped (TRY401).
        logger.warning("graph_search_failed", org_id=org_id, exc_info=True)
        return []
    _circuit.record_success(admitted)
    return results


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
        # Prefer a document-key episode over a legacy artifact-id one. An edge
        # accumulates episodes as later ingests re-confirm the same fact, and
        # the pre-rollout episode stays FIRST in that list — taking whichever
        # name resolves first would therefore keep picking the superseded
        # artifact_id and no existing edge would ever heal. Choosing by scheme
        # instead of by position removes the ordering assumption entirely.
        names = [
            name
            for episode_uuid in _episode_uuids(r)
            if (name := (episode_artifacts or {}).get(episode_uuid))
        ]
        document = next((parsed for name in names if (parsed := parse_episode_name(name))), None)
        source_name = None if document else next(iter(names), None)
        # A doc-key edge gets its artifact_id from the CURRENT version at
        # label time; a legacy edge carries the (possibly superseded) id it
        # was named after, which keeps it citable exactly as it is today.
        artifact_id = source_name
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
