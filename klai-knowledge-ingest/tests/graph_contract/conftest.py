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
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
import redis
import redis.asyncio
from graphiti_core import Graphiti
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
        """``search`` and ``search_`` must return this tenant's stored data, and only that."""
        nodes = {node.uuid for node in await self.step(f"{step}: read nodes", self.stored_nodes())}
        edges = {edge.uuid for edge in await self.step(f"{step}: read edges", self.stored_edges())}
        found_edges = await self.step(
            f"{step}: search {query[:40]!r}", self.graphiti.search(query, group_ids=[self.group_id])
        )
        combined = await self.step(
            f"{step}: search_ {query[:40]!r}",
            self.graphiti.search_(query, group_ids=[self.group_id]),
        )
        returned_nodes = {node.uuid for node in combined.nodes}
        returned_edges = {edge.uuid for edge in combined.edges} | {
            edge.uuid for edge in found_edges
        }
        assert returned_nodes | returned_edges, f"{step}: search for {query[:40]!r} found nothing"
        assert returned_nodes <= nodes, f"{step}: search_ {query[:40]!r} returned a foreign node"
        assert returned_edges <= edges, f"{step}: search {query[:40]!r} returned a foreign edge"

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
