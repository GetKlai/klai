"""Graphiti-against-real-FalkorDB contract fixtures.

Every other graphiti test in this repo uses a fake driver, so a graphiti-core
bump (0.31 added an exact-name query that segfaults FalkorDB 4.x on stopword
names) stayed green in CI and crash-looped production. These tests run
graphiti's real write and read paths against a real FalkorDB instead.

Opt in by pointing ``GRAPH_CONTRACT_FALKORDB_PORT`` (and optionally
``GRAPH_CONTRACT_FALKORDB_HOST``, default 127.0.0.1) at a disposable FalkorDB.
Without it the tests skip, so the normal unit suite never needs the container.
``GRAPH_CONTRACT_REQUIRED=1`` (set by the graph-contract CI job) turns an
unreachable server into a failure, so a broken service container cannot make the
job pass by skipping everything.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest
import pytest_asyncio
import redis
import redis.asyncio
from graphiti_core import Graphiti
from graphiti_core.driver.falkordb import STOPWORDS
from graphiti_core.driver.falkordb_driver import FalkorDriver
from graphiti_core.edges import EntityEdge
from graphiti_core.errors import GroupsEdgesNotFoundError
from graphiti_core.nodes import EntityNode, EpisodeType

from knowledge_ingest import _patch_graphiti
from knowledge_ingest.config import settings
from tests.graph_contract.corpus import normalise
from tests.graph_contract.fakes import FakeCrossEncoder, FakeEmbedder, FakeLLM

_HOST = os.environ.get("GRAPH_CONTRACT_FALKORDB_HOST", "127.0.0.1")
_PORT = os.environ.get("GRAPH_CONTRACT_FALKORDB_PORT")
_REQUIRED = os.environ.get("GRAPH_CONTRACT_REQUIRED") == "1"
_PING_TIMEOUT_S = 10.0
_EMBEDDING_DIM = 1024
_MAX_QUERY_WORDS = 128  # graphiti.driver.falkordb.fulltext.MAX_QUERY_LENGTH
STOPWORDS_LOWER = {word.lower() for word in STOPWORDS}

# run_id of the server the session started against; a crash-and-restart keeps
# PING working but changes it, and loses every graph.
_server_run_id: str | None = None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    global _server_run_id
    ours = [item for item in items if "graph_contract" in item.nodeid]
    if not ours:
        return
    for item in ours:
        item.add_marker(pytest.mark.graph_contract)

    problem: str | None = None
    if _PORT is None:
        problem = "GRAPH_CONTRACT_FALKORDB_PORT is not set"
    else:
        try:
            client = redis.Redis(host=_HOST, port=int(_PORT), socket_connect_timeout=3)
            _server_run_id = client.info("server")["run_id"]
        except redis.RedisError as exc:
            problem = f"no FalkorDB reachable at {_HOST}:{_PORT} ({exc})"
    if problem is None:
        return
    if _REQUIRED:
        raise pytest.UsageError(f"graph-contract tests are required but {problem}")
    skip = pytest.mark.skip(reason=f"graph contract needs a disposable FalkorDB: {problem}")
    for item in ours:
        item.add_marker(skip)


@pytest.fixture(scope="session", autouse=True)
def _apply_ingest_patches() -> None:
    """Patch graphiti exactly as knowledge_ingest.graph._get_graphiti does.

    ``settings.graph_ann_enabled`` is true in the production compose file, so the
    ANN candidate search and vector index DDL are part of the contract too.
    """
    if _PORT is None or _server_run_id is None:
        return
    previous = settings.graph_ann_enabled
    settings.graph_ann_enabled = True
    try:
        _patch_graphiti.apply()
    finally:
        settings.graph_ann_enabled = previous


class Contract:
    """One tenant graph plus the checks every step must pass."""

    def __init__(
        self,
        graphiti: Graphiti,
        llm: FakeLLM,
        embedder: FakeEmbedder,
        group_id: str,
        probe: Any,
        groups: list[str],
    ) -> None:
        self.graphiti = graphiti
        self.llm = llm
        self.embedder = embedder
        self.group_id = group_id
        self._probe = probe
        self._groups = groups
        self._episode = 0
        groups.append(group_id)

    def other_tenant(self) -> Contract:
        """A second tenant graph on the same server, cleaned up with this one."""
        group_id = f"{self.group_id[:-8]}{uuid.uuid4().hex[:8]}"
        return Contract(self.graphiti, self.llm, self.embedder, group_id, self._probe, self._groups)

    async def assert_alive(self, step: str, cause: BaseException | None = None) -> None:
        try:
            info = await asyncio.wait_for(self._probe.info("server"), _PING_TIMEOUT_S)
            await asyncio.wait_for(self._probe.ping(), _PING_TIMEOUT_S)
        except Exception as exc:
            pytest.fail(
                f"FalkorDB stopped answering during step {step!r} "
                f"({type(exc).__name__}: {exc}); the last query killed or hung the server"
                + (f". Step error: {type(cause).__name__}: {cause}" if cause else ""),
                pytrace=False,
            )
        if info["run_id"] != _server_run_id:
            pytest.fail(
                f"FalkorDB restarted during step {step!r}: run_id changed, so the server "
                "crashed and every graph it held is gone",
                pytrace=False,
            )

    async def step(self, name: str, work: Any) -> Any:
        """Run ``work`` (a coroutine), then require a live server."""
        try:
            result = await work
        except Exception as exc:
            await self.assert_alive(name, cause=exc)
            raise AssertionError(
                f"step {name!r} failed with the server still up: {type(exc).__name__}: {exc}"
            ) from exc
        await self.assert_alive(name)
        return result

    async def ingest(self, step: str, names: list[str]) -> None:
        self._episode += 1
        self.llm.entities = names
        await self.step(
            step,
            self.graphiti.add_episode(
                name=f"fixture-episode-{self._episode}",
                episode_body="Synthetic fixture episode for the graph contract.",
                source=EpisodeType.text,
                source_description="graph-contract",
                reference_time=datetime(2026, 1, 1, tzinfo=UTC),
                group_id=self.group_id,
                previous_episode_uuids=[],
            ),
        )

    async def stored_nodes(self) -> list[EntityNode]:
        driver = self.graphiti.driver.clone(database=self.group_id)
        return await EntityNode.get_by_group_ids(driver, [self.group_id])

    async def stored_edges(self) -> list[EntityEdge]:
        driver = self.graphiti.driver.clone(database=self.group_id)
        try:
            return await EntityEdge.get_by_group_ids(driver, [self.group_id])
        except GroupsEdgesNotFoundError:
            return []

    async def expect_search_hit(self, step: str, query: str) -> None:
        """``search`` and ``search_`` must each return this tenant's stored data, and only that.

        ``query`` is a stored entity name: ``search_`` must return that node, and ``search``
        (edges only) must return an edge when the node has one.
        """
        stored_nodes = await self.step(f"{step}: read nodes", self.stored_nodes())
        stored_edges = await self.step(f"{step}: read edges", self.stored_edges())
        nodes = {node.uuid for node in stored_nodes}
        edges = {edge.uuid for edge in stored_edges}
        named = {node.uuid for node in stored_nodes if normalise(node.name) == normalise(query)}
        # Edges are found by full-text or fact similarity, and graphiti's full-text query is
        # empty for stopword-only text and for 128 words or more, so a fake embedder whose
        # vectors only match equal text leaves edge search nothing to find for those names.
        words = normalise(query).split()
        edge_findable = (
            any(w not in STOPWORDS_LOWER for w in words) and len(words) < _MAX_QUERY_WORDS
        )
        has_edge = edge_findable and any(
            edge.source_node_uuid in named or edge.target_node_uuid in named
            for edge in stored_edges
        )
        found_edges = await self.step(
            f"{step}: search {query[:40]!r}", self.graphiti.search(query, group_ids=[self.group_id])
        )
        combined = await self.step(
            f"{step}: search_ {query[:40]!r}",
            self.graphiti.search_(query, group_ids=[self.group_id]),
        )
        search_edges = {edge.uuid for edge in found_edges}
        search_nodes = {node.uuid for node in combined.nodes}
        search_node_edges = {edge.uuid for edge in combined.edges}
        assert search_nodes & named, (
            f"{step}: search_ {query[:40]!r} did not return the stored node"
        )
        if has_edge:
            assert search_edges, f"{step}: search {query[:40]!r} returned no edge"
            assert search_node_edges, f"{step}: search_ {query[:40]!r} returned no edge"
        assert search_nodes <= nodes, f"{step}: search_ {query[:40]!r} returned a foreign node"
        assert search_edges <= edges, f"{step}: search {query[:40]!r} returned a foreign edge"
        assert search_node_edges <= edges, f"{step}: search_ {query[:40]!r} returned a foreign edge"

    async def expect_entities(self, step: str, names: list[str]) -> None:
        stored = {normalise(node.name) for node in await self.step(step, self.stored_nodes())}
        missing = [n for n in names if normalise(n) and normalise(n) not in stored]
        assert not missing, f"step {step!r}: entities not queryable after ingest: {missing!r}"


@pytest_asyncio.fixture
async def contract(request: pytest.FixtureRequest) -> AsyncIterator[Contract]:
    assert _PORT is not None
    monkeypatch = pytest.MonkeyPatch()
    # graphiti reports usage to PostHog unless told not to; the contract is offline.
    monkeypatch.setenv("GRAPHITI_TELEMETRY_ENABLED", "false")

    probe = redis.asyncio.Redis(host=_HOST, port=int(_PORT))
    llm = FakeLLM()
    embedder = FakeEmbedder(_EMBEDDING_DIM)
    graphiti = Graphiti(
        graph_driver=FalkorDriver(host=_HOST, port=int(_PORT)),
        llm_client=llm,
        embedder=embedder,
        cross_encoder=FakeCrossEncoder(),
    )
    label = "".join(ch for ch in request.node.name.lower() if ch.isalnum())[:30]
    group_id = f"contract{label}{uuid.uuid4().hex[:8]}"
    groups: list[str] = []
    helper = Contract(graphiti, llm, embedder, group_id, probe, groups)
    await helper.assert_alive(f"start of {request.node.name}")
    try:
        yield helper
    finally:
        for group in groups:
            try:
                await probe.execute_command("GRAPH.DELETE", group)
            except redis.RedisError:
                pass  # graph never created, or the server is already gone and reported above
        await graphiti.close()
        await probe.aclose()
        monkeypatch.undo()


@pytest.fixture
def graph_helpers(contract: Contract, monkeypatch: pytest.MonkeyPatch) -> Contract:
    """``contract``, with knowledge_ingest.graph's helpers pointed at its tenant graph."""
    from knowledge_ingest import graph as graph_module

    monkeypatch.setattr(settings, "graphiti_enabled", True)
    monkeypatch.setattr(graph_module, "_get_graphiti", lambda: contract.graphiti)
    return contract


async def seed_graph(contract: Contract, episodes: list[str], edges: dict[str, list[str]]) -> None:
    """Store episodes, and per ``edges`` key one fact edge citing the listed episodes.

    Every edge gets its own two entities, each mentioned by every existing
    episode the edge cites: graphiti's shape, where an extracted fact's
    endpoints are mentioned by the episode it came from. A cited uuid without
    an Episodic node is how an edge outlives its episode.
    """
    driver = contract.graphiti.driver.clone(database=contract.group_id)
    await driver.execute_query(
        "UNWIND $uuids AS u CREATE (:Episodic {uuid: u, group_id: $g})",
        uuids=episodes,
        g=contract.group_id,
    )
    for edge, cited in edges.items():
        await driver.execute_query(
            "CREATE (a:Entity {uuid: $a, group_id: $g, name: $a})"
            "-[:RELATES_TO {uuid: $edge, group_id: $g, episodes: $cited, fact: $edge}]->"
            "(b:Entity {uuid: $b, group_id: $g, name: $b}) "
            "WITH a, b UNWIND $cited AS c MATCH (e:Episodic {uuid: c}) "
            "CREATE (e)-[:MENTIONS]->(a), (e)-[:MENTIONS]->(b)",
            a=f"{edge}-source",
            b=f"{edge}-target",
            edge=edge,
            cited=cited,
            g=contract.group_id,
        )


async def stored_graph(contract: Contract) -> tuple[set[str], dict[str, list[str]], set[str]]:
    """(episode uuids, edge uuid -> cited episodes, entity uuids) as stored."""
    driver = contract.graphiti.driver.clone(database=contract.group_id)
    records, _, _ = await driver.execute_query("MATCH (e:Episodic) RETURN collect(e.uuid) AS uuids")
    episodes = set(records[0]["uuids"])
    records, _, _ = await driver.execute_query(
        "MATCH ()-[r:RELATES_TO]->() RETURN r.uuid AS uuid, r.episodes AS episodes"
    )
    edges = {row["uuid"]: sorted(row["episodes"] or []) for row in records}
    records, _, _ = await driver.execute_query("MATCH (n:Entity) RETURN collect(n.uuid) AS uuids")
    return episodes, edges, set(records[0]["uuids"])


_PG_DSN = os.environ.get("GRAPH_REFRESH_BUDGET_TEST_DSN")


@pytest_asyncio.fixture
async def tenant_rows(graph_helpers: Contract):
    """A real Postgres connection scoped to the contract's org, plus a row inserter.

    Tests that combine the graph with the artifact rows need both stores; the
    graph-contract CI job provides Postgres through ``GRAPH_REFRESH_BUDGET_TEST_DSN``.
    """
    if not _PG_DSN:
        if _REQUIRED:
            pytest.fail("GRAPH_REFRESH_BUDGET_TEST_DSN is required with GRAPH_CONTRACT_REQUIRED=1")
        pytest.skip("GRAPH_REFRESH_BUDGET_TEST_DSN is not configured")
    org_id = graph_helpers.group_id
    conn = await asyncpg.connect(_PG_DSN)
    await conn.execute("SELECT set_config('app.current_org_id', $1, false)", org_id)

    async def insert(
        extra: dict,
        *,
        active: bool,
        superseded_by: str | None = None,
        path: str = "page",
        source_ref: str | None = None,
    ) -> str:
        artifact_id = str(uuid.uuid4())
        if source_ref:
            extra = {**extra, "source_connector_id": "connector-1", "source_ref": source_ref}
        await conn.execute(
            """
            INSERT INTO knowledge.artifacts (
                id, org_id, provenance_type, assertion_mode, belief_time_start,
                belief_time_end, superseded_by, created_at, kb_slug, path, extra
            ) VALUES ($1, $2, 'observed', 'factual', 1, $3, $4, 1, 'docs', $5, $6::jsonb)
            """,
            artifact_id,
            org_id,
            253402300800 if active else 2,
            superseded_by,
            path,
            json.dumps(extra),
        )
        return artifact_id

    try:
        yield conn, insert
    finally:
        await conn.execute(
            "UPDATE knowledge.artifacts SET superseded_by = NULL WHERE org_id = $1", org_id
        )
        await conn.execute("DELETE FROM knowledge.artifacts WHERE org_id = $1", org_id)
        await conn.close()
