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
import types
from dataclasses import dataclass, field
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
from klai_kb_slugs import episode_name

import knowledge_ingest.qdrant_store as qdrant_store
from knowledge_ingest import _patch_graphiti
from knowledge_ingest.build_estimate import maybe_warn_graph_scale, should_check_graph_scale
from knowledge_ingest.config import settings
from knowledge_ingest.llm_throttle import TokenBucketLimiter, shared_klai_fast_limiter

logger = structlog.get_logger()

# Rate-limit Graphiti episodes: each add_episode() makes ~5 LLM calls internally.
# Concurrency controlled by GRAPHITI_MAX_CONCURRENT env var (default: 1).
_episode_semaphore: asyncio.Semaphore | None = None


@dataclass
class EntityGraphData:
    """Entity data collected across every episode part of one document."""

    entity_uuids: list[str] = field(default_factory=list)
    entity_names: list[str] = field(default_factory=list)

    def extend(self, entity_uuids: list[str], entity_names: list[str]) -> None:
        self.entity_uuids.extend(uid for uid in entity_uuids if uid not in self.entity_uuids)
        self.entity_names.extend(name for name in entity_names if name not in self.entity_names)


# Extra rules appended to Graphiti's entity- AND edge-extraction prompts
# (graphiti_core injects this same string into extract_nodes, extract_edges and
# the combined extractor). GetKlai/klai#1148.
#
# Two defects were measured on the live Voys graph on 2026-08-21, once #1147
# made edge facts citable and therefore visible for the first time:
#
#   1. Meta-facts. Edges such as "De paginamap identificeert de Voys-app als
#      een applicatie die kan worden gebruikt..." describe the *document*, not
#      the domain. They can never answer a user question, but they still take
#      up context: the graph leg returns candidates on 29% of queries and in
#      181 of 1,704 requests all 10 of its results were served against
#      served_top_k=20 — half the model's context.
#   2. Language drift. The corpus is Dutch, yet several extracted facts came
#      back in English. The retrieval fulltext leg ORs the user's Dutch query
#      tokens against the edge text, so an English fact rarely matches a Dutch
#      question even when it is topically perfect.
#
# The examples below are the actual production strings, kept verbatim so the
# rules stay anchored to observed output rather than to an imagined failure.
#
# Reach (graphiti-core 0.29.3): this string is interpolated into the entity
# prompts (extract_message / extract_json / extract_text), extract_edges, and
# the combined extractor. It is NOT interpolated into the node-summary prompts
# (extract_summary / extract_summaries_batch), so entity summaries are out of
# scope. tests/test_graph.py pins that reach so a graphiti bump that moves the
# hook fails in CI.
_EXTRACTION_INSTRUCTIONS = """
# SOURCE-DOCUMENT RULES

These episodes are whole documents from a company knowledge base (manuals,
wiki pages, meeting notes), not chat messages. One extra rule applies.

1. **Extract facts about the world, never about the document.**
   The subject of a fact must be a thing that exists outside this text — a
   product, a person, an organisation, a procedure, a setting. A statement
   whose subject is the document, page, section, chapter, manual, index,
   table of contents, heading or layout is NOT a fact; skip it entirely
   rather than rephrasing it. This includes a document, manual or article
   being titled, named, called or having a subject; a manual describing or
   explaining a topic; and something falling under a section or category of
   the documentation.
   - BAD: "De paginamap identificeert de Voys-app als een applicatie"
     (subject is the page map, not the Voys app)
   - BAD: "De Webphone is een applicatie met een handleiding voor klanten"
     (the existence of a manual is a property of the documentation)
   - BAD: "This section explains how to configure call forwarding"
   - BAD: "Een van de documentatieartikelen voor Freedom is \
getiteld 'Freedom: Het Dashboard'." (subject is the documentation article)
   - BAD: "De handleiding 'Wachtrijstatistieken' beschrijft statistieken over \
wachtrijen binnen Freedom." (subject is the manual)
   - BAD: "Het onderwerp van de handleiding 'Statistieken' zijn statistische \
overzichten in Freedom." (subject is the manual)
   - BAD: "VoIP-bureautelefoons vallen onder de apparatuursectie in de \
Voys-documentatie." (relation is to a documentation section)
   - GOOD: "De Voys-app gebruikt de internetverbinding van de smartphone
     voor gesprekken"
   - GOOD: "Call forwarding is configured from the Freedom web interface"
   Rewrite where a real fact hides inside a meta-statement: from "De
   paginamap identificeert de Voys-app als een applicatie die op mobiel
   werkt", extract "De Voys-app werkt op mobiel". If no world-fact remains,
   drop the statement.

Language is NOT covered here. It is set once, for every LLM call graphiti
makes, by ``_LANGUAGE_POLICY`` below — stating it in two places invites the
two copies to drift apart.
"""


# ---------------------------------------------------------------------------
# Language policy (GetKlai/klai#1148)
# ---------------------------------------------------------------------------
#
# The corpus is multilingual and open-ended: mostly Dutch and English today,
# German already in use, and French/Spanish/Portuguese arriving with the
# multi-country chat-agent rollout. Two DIFFERENT things need two different
# answers, and conflating them is what produced the current graph.
#
# Fact text is what a user reads as a citation, so it stays in the language of
# the document it came from. Translating it would make the citation disagree
# with the page it links to.
#
# Entity names are the graph's join keys, so they cannot be per-language.
# graphiti resolves entities with character 3-gram shingles + MinHash/Jaccard
# over the normalised name (``dedup_helpers._shingles``). "Toestel" and
# "Device" share no 3-gram, so lexical dedup can never merge them; across six
# languages one concept becomes six unconnected nodes and the graph stops
# being a graph. A canonical name in one pivot language is what keeps a Dutch
# document and a Portuguese question anchored to the same node. English is the
# pivot because the model is strongest there and because the existing graph is
# already mostly English on entity names (Call 147 vs Gesprek 27, User 57 vs
# Gebruiker 16), so this is the direction with the least to migrate.
#
# Proper nouns are exempt from the pivot in both directions: "Belplan" has no
# English equivalent worth inventing, and inventing one would break the join
# with every document that names it.
#
# graphiti's own default says the opposite of what we need — "Otherwise,
# output English", appended AFTER any custom instruction, which is why facts
# extracted before this override came out English from Dutch pages. The
# docstring on ``get_extraction_language_instruction`` invites the override;
# ``_install_language_policy`` below performs it.
#
# This governs EXTRACTION ONLY; edges already in the graph keep the language
# they were written in. content_hash dedup means an unchanged page is never
# re-extracted, so the existing mix heals only via a deliberate migration
# (#1148). The transitional state is safe rather than merely tolerable: edges
# are extracted against ``extracted_nodes`` — the names produced by this same
# run, before resolution against existing nodes (graphiti.py:656-659) — so the
# ENTITIES list an edge must reference always holds the canonical names, and no
# relationship is dropped for naming a node the old graph spells differently.
# What a pre-existing node costs is only that resolution keeps ITS name, so
# canonicalisation of that concept waits for the migration.
_LANGUAGE_POLICY = """

# LANGUAGE POLICY

The source material is multilingual (Dutch, English, German, French, Spanish,
Portuguese and others). Apply these rules to every response.

1. **Fact text follows the source.** Any sentence describing a fact,
   relationship or summary must be written in the same language as the text it
   was extracted from. A Dutch page yields Dutch facts; a German page yields
   German facts. Never translate fact text, and never default to English.

2. **Entity names are canonical English.** The name of an entity is an
   identifier shared across every language in the corpus, so it must not vary
   by source language. Use the ordinary English term: "Device", not "Toestel"
   or "Gerät"; "Queue", not "Wachtrij" or "File d'attente".

3. **Proper nouns are never translated.** Product names, brand names, feature
   names, interface labels, error strings and technical identifiers keep their
   exact source spelling in both fact text and entity names — "Belplan",
   "Freedom", "Voys App", "Force Encryption", "sipproxy.voipgrid.nl". If a term
   is a name rather than a description, copy it verbatim.

The distinction that matters: rule 1 is about the prose a person reads, rule 2
is about the key the system joins on. A Dutch fact may therefore reference an
English entity name, and that is correct.
"""


def _install_strict_structured_output() -> None:
    """Make graphiti ask the model to OBEY its schema, not merely to see it.

    ``OpenAIGenericClient._build_response_format`` sends json_schema without
    ``strict: true``, with the comment that strict "requires the schema to meet
    OpenAI's strict subset ... So adherence is best-effort on OpenAI-proper;
    constrained-decoding servers (vLLM, llama.cpp) still enforce it."

    Mistral behind LiteLLM is neither of those. Without the flag it treats the
    schema as advice and drifts once a response carries many items, which is
    silent until pydantic rejects the result and the whole episode is lost:

        extracted_entities.26.episode_indices
          Input should be a valid list [input_value=0, input_type=int]
        extracted_entities.15.entity_type_id
          Field required [input_value={'entity_type_id_id': 0, ...}]

    Both are the same failure — the model inventing shapes at entity 15, 26, 36
    while the first dozen are fine. Measured on 2026-08-24 with graphiti's own
    ``ExtractedEntities.model_json_schema()`` through this LiteLLM: without the
    flag the #1148 rebuild lost 2 of every 4 documents; with it, 39 entities in
    one response and none malformed.

    The concern graphiti's comment raises is real but not ours to inherit: the
    schema either satisfies the provider or the provider rejects the request
    outright, which is loud. Best-effort adherence fails silently instead, and
    costs the document.
    """
    from graphiti_core.llm_client import openai_generic_client as _ogc

    client_cls = _ogc.OpenAIGenericClient
    if getattr(client_cls, "_klai_strict_schema", False):
        return
    original = client_cls._build_response_format

    def _build_response_format(self, response_model):
        payload = original(self, response_model)
        if payload.get("type") == "json_schema":
            payload["json_schema"]["strict"] = True
        return payload

    client_cls._build_response_format = _build_response_format
    client_cls._klai_strict_schema = True
    logger.info("graph_strict_structured_output_installed")


def _install_language_policy() -> None:
    """Replace graphiti's default language instruction with ``_LANGUAGE_POLICY``.

    ``get_extraction_language_instruction`` is documented as the override point
    ("Override this function to customize language extraction"), but every LLM
    client binds it with ``from .client import ...`` at import time. Rebinding
    only the definition in ``llm_client.client`` would leave each concrete
    client calling the original, so every module that holds a reference has to
    be rebound.

    Discovering those modules by attribute rather than naming them means a
    graphiti upgrade that adds a client picks the policy up automatically;
    ``tests/test_graph_language_policy.py`` fails if any binding is missed.
    """
    import graphiti_core.llm_client as _llm_pkg

    def _policy(group_id: str | None = None) -> str:  # noqa: ARG001 - graphiti's signature
        # group_id is graphiti's per-partition hook. We deliberately do not
        # branch on it: the policy is a property of the corpus, not of one
        # tenant, and a per-tenant pivot language would fragment the graph in
        # exactly the way rule 2 exists to prevent.
        return _LANGUAGE_POLICY

    patched = 0
    for _name in dir(_llm_pkg):
        module = getattr(_llm_pkg, _name, None)
        if isinstance(module, types.ModuleType) and hasattr(
            module, "get_extraction_language_instruction"
        ):
            module.get_extraction_language_instruction = _policy
            patched += 1
    logger.info("graph_language_policy_installed", bindings=patched)


class _RateLimitedTransport(httpx.AsyncBaseTransport):
    def __init__(self, wrapped: httpx.AsyncBaseTransport, limiter: TokenBucketLimiter) -> None:
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


def _graphiti_retry_delay(exc: Exception, attempt: int) -> tuple[str, float]:
    exc_str = str(exc).lower()
    if "rate limit" in exc_str or "429" in exc_str or "ratelimit" in exc_str:
        return "rate_limited", 65.0 * (2**attempt)
    if (
        "503" in exc_str
        or "service unavailable" in exc_str
        or "internal_server_error" in exc_str
        or 'code":"3800' in exc_str
        or "code': '3800" in exc_str
    ):
        return "provider_unavailable", 30.0 * (2**attempt)
    return "transient", 1.0 * (2**attempt)


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
        # (entity extraction, deduplication, embedding, etc.) via the SHARED
        # klai-fast budget (knowledge_ingest.llm_throttle), not a Graphiti-only
        # rate. This prevents Graphiti's bursts from exceeding the upstream
        # klai-fast alias budget in combination with every other klai-fast caller.
        _llm_limiter = shared_klai_fast_limiter()
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
        # Before any client is constructed: graphiti appends its own language
        # instruction to every system message, and its default ends in
        # "Otherwise, output English".
        _install_language_policy()
        _install_strict_structured_output()
        # The FalkorDB compatibility layer, applied here rather than only in
        # app.py. Its edge-search rewrite is what keeps the fulltext join from
        # scanning every RELATES_TO edge once per hit (GetKlai/klai#1214); on
        # the Voys graph that is 2.99 ms against a query that otherwise runs
        # past 140 s and dies on FalkorDB's 1 s timeout.
        #
        # app.py applies it at import, so the service was always covered. An
        # operator entry point is not: `python -m knowledge_ingest.backfill`
        # never imports app, so every episode it wrote failed. _get_graphiti is
        # the one place every path — service, worker, backfill, one-off script —
        # builds a client, which is why it belongs here and not in each caller.
        _patch_graphiti.apply()
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


async def rename_episodes_to_document_keys(org_id: str, renames: dict[str, list[str]]) -> int:
    """Point existing episodes at their DOCUMENT instead of an artifact version.

    ``renames`` maps a stable episode name (``doc:<kb_slug>:<path>``) to the
    episode uuids that should carry it.

    SPEC-RAG-GRAPH-CITE-002 makes ingest write this name, but that only helps
    documents that get ingested again — and content_hash dedup means an
    unchanged page is never re-ingested, so an existing edge would keep its
    artifact-version name forever and its citations would stay unresolvable.

    This is a metadata rename, not a re-extraction: no LLM calls, no entity
    work, nothing drawn from the shared klai-fast budget. Idempotent, so a
    partially completed run can simply be repeated.

    Several versions of one document legitimately end up sharing a name. That
    is fine — the name is a pointer, and retrieval resolves it against the
    CURRENT version in Qdrant.
    """
    if not settings.graphiti_enabled or not renames:
        return 0
    graphiti = _get_graphiti()
    driver = graphiti.driver.clone(org_id)
    renamed = 0
    for name, uuids in renames.items():
        if not uuids:
            continue
        result = await driver.execute_query(
            "MATCH (e:Episodic) WHERE e.uuid IN $uuids SET e.name = $name RETURN count(e) AS n",
            uuids=uuids,
            name=name,
        )
        if result is not None:
            records, _, _ = result
            if records:
                renamed += records[0].get("n", 0)
    logger.info("graph_episodes_renamed", org_id=org_id, documents=len(renames), episodes=renamed)
    return renamed


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
    -> FalkorDB by writing FalkorDB ``Episodic.uuid`` values into
    ``knowledge.artifacts.extra->'graphiti_episode_ids'`` while retaining the
    first value in the legacy scalar.

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
    them into ``knowledge.artifacts.extra`` (the row was already gone), so
    ``delete_kb_episodes`` cannot find them via
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


def wipe_org_graph(org_id: str) -> int:
    """Hard-delete ALL nodes in the FalkorDB graph for *org_id*.

    # @MX:ANCHOR: deprovisioning hard-delete — wipes the entire org graph.
    # @MX:REASON: Called by SPEC-INFRA-TENANT-DELETE-001 Phase 7 endpoint.
    #   This is irreversible: every node (Episodic, Entity, …) with
    #   group_id == org_id is DETACH DELETE'd.  Returns the count of nodes
    #   removed so the API can surface it to the orchestrator.

    Uses the direct ``falkordb`` Python client (same pattern as
    ``sweep_orphan_episodes_org_wide``) because the async Graphiti driver
    returns empty result_sets for COUNT queries due to shape mismatch.

    Returns 0 when graphiti is disabled or the graph is already empty.
    Synchronous — callers wrap in ``asyncio.to_thread`` when called from
    an async context if needed (but a FastAPI endpoint runs fine blocking
    on a sync FalkorDB round-trip given the small overhead).

    SPEC-INFRA-TENANT-DELETE-001 Phase 7.
    """
    if not settings.graphiti_enabled:
        logger.info("wipe_org_graph_skipped_graphiti_disabled", org_id=org_id)
        return 0
    try:
        from falkordb import FalkorDB as FalkorDBClient
    except ImportError:
        logger.warning("falkordb_client_unavailable_for_wipe", org_id=org_id)
        return 0

    client = FalkorDBClient(host=settings.falkordb_host, port=settings.falkordb_port)
    graph = client.select_graph(org_id)

    result = graph.query(
        "MATCH (n) WHERE n.group_id = $org_id "
        "WITH n, id(n) AS nid "
        "DETACH DELETE n "
        "RETURN count(nid) AS deleted",
        params={"org_id": org_id},
    )
    deleted = 0
    if result.result_set:
        deleted = int(result.result_set[0][0] or 0)
    logger.info("wipe_org_graph_complete", org_id=org_id, nodes_deleted=deleted)
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


def _episode_name(artifact_id: str, kb_slug: str, path: str) -> str:
    """Name an episode after the DOCUMENT, falling back to the artifact_id.

    SPEC-RAG-GRAPH-CITE-002. ``artifact_id`` identifies a version, not a
    document: every ingest mints a fresh uuid4 and supersedes the previous
    row, while Qdrant keeps only the current version's chunks. An episode
    named after it therefore stops resolving the moment its page is
    re-ingested, which is exactly what made graph citations render as
    truncated sentences instead of links.

    The fallback keeps in-flight Procrastinate jobs working: those were
    deferred with the old kwargs and arrive without kb_slug/path, and a task
    that has been queued for an hour must not fail on a signature change.
    """
    if kb_slug and path:
        return episode_name(kb_slug, path)
    return artifact_id


async def flush_entity_graph_data(
    artifact_id: str,
    org_id: str,
    entity_graph_data: EntityGraphData,
) -> None:
    """Write the complete document entity payload and refresh PageRank once."""
    if not entity_graph_data.entity_uuids and not entity_graph_data.entity_names:
        return
    pagerank_scores = await compute_entity_pagerank(org_id)
    await qdrant_store.set_entity_graph_data(
        artifact_id=artifact_id,
        org_id=org_id,
        entity_uuids=entity_graph_data.entity_uuids,
        pagerank_scores=pagerank_scores,
        entity_names=entity_graph_data.entity_names,
    )


async def _maybe_warn_graph_scale_from_falkordb(org_id: str) -> None:
    """Live-ingest hook for SPEC-GRAPH-SCALE-001's throttled scale warning.

    ``should_check_graph_scale`` is consulted FIRST, before any FalkorDB I/O,
    so the count query below — the only I/O this hook does — runs at most
    once per org per hour per process; every other call on the hot ingest
    path returns immediately.

    Uses the direct ``falkordb`` Python client (same pattern as
    ``sweep_orphan_episodes_org_wide`` / ``wipe_org_graph`` above: the
    graphiti async driver returns empty result sets for COUNT queries against
    FalkorDB), wrapped in ``asyncio.to_thread`` since the client is
    synchronous.

    Never raises: this is a non-fatal, best-effort warning on the ingest
    success path. Any failure (missing dependency, FalkorDB unreachable,
    malformed result) is caught and logged at warning level — it must never
    fail an episode ingest.
    """
    if not should_check_graph_scale(org_id):
        return
    try:
        from falkordb import FalkorDB as FalkorDBClient

        def _count_edges() -> int:
            # Socket deadlines: the client defaults to blocking forever.
            client = FalkorDBClient(
                host=settings.falkordb_host,
                port=settings.falkordb_port,
                socket_connect_timeout=2.0,
                socket_timeout=5.0,
            )
            graph = client.select_graph(org_id)
            result = graph.query("MATCH ()-[r:RELATES_TO]->() RETURN count(r) AS n")
            if not result.result_set:
                # A valid Cypher count() returns one row even on an empty
                # graph; an empty result_set is external drift, not zero.
                # Raising keeps it loud (fail-loudly): the except below logs
                # it instead of silently suppressing a scale warning.
                raise RuntimeError(
                    f"FalkorDB edge-count query returned no result_set for org {org_id}"
                )
            return int(result.result_set[0][0] or 0)

        # Hard deadline on the count query. The falkordb client has no default
        # socket timeout, so a FalkorDB that accepts the connection but never
        # answers would otherwise park this coroutine forever WHILE it holds
        # the episode semaphore — an optional warning must never be able to
        # block ingestion. The worker thread may linger until the socket dies,
        # which is acceptable for a once-per-org-per-hour hook.
        current_edge_count = await asyncio.wait_for(
            asyncio.to_thread(_count_edges), timeout=5.0
        )
        maybe_warn_graph_scale(org_id, current_edge_count)
    except Exception:
        logger.warning("graph_scale_warning_hook_failed", org_id=org_id, exc_info=True)


# Strong references to in-flight scale-warning tasks: asyncio only keeps weak
# references to tasks, so a bare create_task could be garbage-collected
# mid-flight. Bounded in practice by the once-per-org-per-hour throttle.
_scale_warning_tasks: set[asyncio.Task[None]] = set()


def _spawn_graph_scale_warning(org_id: str) -> None:
    """Run the scale-warning hook without coupling it to the episode result."""
    task = asyncio.create_task(_maybe_warn_graph_scale_from_falkordb(org_id))
    _scale_warning_tasks.add(task)
    task.add_done_callback(_scale_warning_tasks.discard)


async def ingest_episode(
    artifact_id: str,
    document_text: str,
    org_id: str,
    content_type: str,
    belief_time_start: int,
    kb_slug: str = "",
    path: str = "",
    entity_graph_data: EntityGraphData | None = None,
) -> str | None:
    """Ingest a document as a Graphiti episode.

    Returns the episode_id on success, or None if all retries fail.
    This function is fire-and-forget — callers must not await its result
    unless they want to block on graph enrichment.

    AC-1: group_id=org_id and reference_time=belief_time_start.
    AC-3: 3 retries with exponential backoff (1s, 2s, 4s).
    AC-13: Structured log on success.
    AC-14: LLM calls routed through LiteLLM proxy.

    Extraction is steered by ``_EXTRACTION_INSTRUCTIONS`` (GetKlai/klai#1148).
    This is the single choke point for every episode — the procrastinate task
    in ``enrichment_tasks.py``, the route helper in ``routes/ingest.py`` and
    the ``backfill.py`` script all funnel through here — so the rules cannot
    be bypassed by one call path.
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
                    name=_episode_name(artifact_id, kb_slug, path),
                    episode_body=document_text,
                    source=EpisodeType.text,
                    source_description=content_type,
                    reference_time=reference_time,
                    group_id=org_id,
                    # GetKlai/klai#1148 — suppress document-meta facts and pin
                    # the extraction language to the source language.
                    custom_extraction_instructions=_EXTRACTION_INSTRUCTIONS,
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

                # Store entity UUIDs + names + PageRank scores in Qdrant.
                # entity_uuids + entity_pagerank_max → document-level (all chunks).
                # entity_names → chunk-level: each chunk only gets names that
                # literally appear in its own text (per-chunk substring filter
                # in qdrant_store).
                entity_uuids_list = [
                    str(getattr(n, "uuid", "")) for n in nodes if getattr(n, "uuid", None)
                ]
                entity_names_list = [
                    str(getattr(n, "name", "")).strip()
                    for n in nodes
                    if getattr(n, "name", None) and str(getattr(n, "name", "")).strip()
                ]
                if entity_uuids_list or entity_names_list:
                    if entity_graph_data is not None:
                        entity_graph_data.extend(entity_uuids_list, entity_names_list)
                    else:
                        try:
                            await flush_entity_graph_data(
                                artifact_id,
                                org_id,
                                EntityGraphData(entity_uuids_list, entity_names_list),
                            )
                        except Exception:
                            logger.exception(
                                "entity_graph_data_failed",
                                artifact_id=artifact_id,
                            )

                # SPEC-GRAPH-SCALE-001 — throttled scale warning on the live
                # ingest path. Fire-and-forget: the hook catches its own
                # exceptions and has its own 5 s deadline, and it must never
                # delay the episode result — a caller deadline (backfill's
                # 600 s wait_for) landing here would lose an episode_id whose
                # episode already committed to the graph.
                _spawn_graph_scale_warning(org_id)

                break

            except Exception as exc:
                if attempt < max_attempts - 1:
                    retry_reason, wait = _graphiti_retry_delay(exc, attempt)
                    logger.warning(
                        "graphiti_ingest_retry",
                        attempt=attempt + 1,
                        max_attempts=max_attempts,
                        artifact_id=artifact_id,
                        error=str(exc),
                        wait_s=wait,
                        retry_reason=retry_reason,
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
