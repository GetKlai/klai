"""Behavior tests for the temporary graphiti-core 0.29 FalkorDB fixes."""

from __future__ import annotations

import asyncio
from types import MethodType
from unittest.mock import AsyncMock

import pytest
from graphiti_core.driver.driver import GraphProvider
from graphiti_core.driver.falkordb_driver import FalkorDriver
from graphiti_core.search import search_utils
from graphiti_core.search.search_filters import SearchFilters

from klai_graphiti_compat import _wrap_database_initialization, apply_falkordb_compat

_UPSTREAM_EDGE_BFS_SEARCH = search_utils.edge_bfs_search


def test_apply_is_idempotent() -> None:
    apply_falkordb_compat()
    patched = search_utils.edge_fulltext_search

    apply_falkordb_compat()

    assert search_utils.edge_fulltext_search is patched


def test_apply_leaves_upstream_bfs_search_unchanged() -> None:
    apply_falkordb_compat()

    assert search_utils.edge_bfs_search is _UPSTREAM_EDGE_BFS_SEARCH


@pytest.mark.asyncio
async def test_edge_fulltext_search_uses_endpoints_without_relationship_rematch() -> None:
    apply_falkordb_compat()

    class Driver:
        provider = GraphProvider.FALKORDB
        search_interface = None
        captured_query = ""

        def build_fulltext_query(self, query, _group_ids, _max_query_length):
            return query

        async def execute_query(self, cypher, **_kwargs):
            self.captured_query = cypher
            return [], [], None

    driver = Driver()
    await search_utils.edge_fulltext_search(
        driver,
        "sip 404",
        SearchFilters(),
        group_ids=["org-1"],
        limit=10,
    )

    assert "startNode(e) AS n" in driver.captured_query
    assert "endNode(e) AS m" in driver.captured_query
    assert "MATCH (n:Entity)-[e:RELATES_TO" not in driver.captured_query


def test_clone_is_shallow_and_does_not_call_constructor(monkeypatch) -> None:
    apply_falkordb_compat()
    driver = object.__new__(FalkorDriver)
    driver._database = "default_db"
    driver.client = object()
    driver._klai_database_init_tasks = {}

    def fail_init(*_args, **_kwargs):
        raise AssertionError("clone must not construct a FalkorDriver")

    monkeypatch.setattr(FalkorDriver, "__init__", fail_init)

    first = driver.clone("org-1")
    second = driver.clone("org-1")
    default = driver.clone(driver.default_group_id)

    assert first is not driver
    assert second is not driver
    assert first.client is driver.client
    assert second.client is driver.client
    assert first._klai_database_init_tasks is driver._klai_database_init_tasks
    assert default._database == "default_db"


@pytest.mark.asyncio
async def test_add_episode_initializes_tenant_database_before_write() -> None:
    events: list[str] = []

    async def add_episode(_self, *, group_id=None):
        events.append(f"write:{group_id}")
        return "written"

    class TenantDriver:
        provider = GraphProvider.FALKORDB
        _database = "default_db"

        def clone(self, database):
            events.append(f"clone:{database}")
            return self

        async def ensure_database_initialized(self):
            events.append("initialized")

    graphiti = type("Graphiti", (), {})()
    graphiti.clients = type("Clients", (), {"driver": TenantDriver()})()
    wrapped = _wrap_database_initialization(add_episode)

    result = await wrapped(graphiti, group_id="org-1")

    assert result == "written"
    assert events == ["clone:org-1", "initialized", "write:org-1"]


@pytest.mark.asyncio
async def test_add_episode_initializes_default_database_when_group_is_omitted() -> None:
    events: list[str] = []

    async def add_episode(_self, *, group_id=None):
        events.append(f"write:{group_id}")

    class DefaultDriver:
        provider = GraphProvider.FALKORDB
        default_group_id = "_"
        _database = "org-previous"

        def clone(self, database):
            events.append(f"clone:{database}")
            return self

        async def ensure_database_initialized(self):
            events.append("initialized")

    graphiti = type("Graphiti", (), {})()
    graphiti.clients = type("Clients", (), {"driver": DefaultDriver()})()

    await _wrap_database_initialization(add_episode)(graphiti)

    assert events == ["clone:_", "initialized", "write:None"]


@pytest.mark.asyncio
async def test_initialization_wrapper_leaves_non_falkor_driver_untouched() -> None:
    calls: list[str] = []

    async def add_episode(_self, *, group_id=None):
        calls.append(f"write:{group_id}")
        return "written"

    graphiti = type("Graphiti", (), {})()
    graphiti.clients = type(
        "Clients",
        (),
        {"driver": type("Driver", (), {"provider": GraphProvider.NEO4J})()},
    )()

    result = await _wrap_database_initialization(add_episode)(graphiti)

    assert result == "written"
    assert calls == ["write:None"]


@pytest.mark.asyncio
async def test_concurrent_database_initialization_runs_once_and_is_awaited() -> None:
    apply_falkordb_compat(initialize_databases=True)
    driver = object.__new__(FalkorDriver)
    driver._database = "org-1"
    driver._init_task = None
    driver._klai_database_init_tasks = {}
    started = asyncio.Event()
    release = asyncio.Event()

    async def build_indices_and_constraints(_self):
        started.set()
        await release.wait()

    driver.build_indices_and_constraints = MethodType(build_indices_and_constraints, driver)
    first = asyncio.create_task(driver.ensure_database_initialized())
    await started.wait()
    second = asyncio.create_task(driver.ensure_database_initialized())
    await asyncio.sleep(0)

    assert not first.done()
    assert not second.done()
    assert len(driver._klai_database_init_tasks) == 1

    release.set()
    await asyncio.gather(first, second)


@pytest.mark.asyncio
async def test_failed_database_initialization_can_retry() -> None:
    apply_falkordb_compat(initialize_databases=True)
    driver = object.__new__(FalkorDriver)
    driver._database = "org-1"
    driver._init_task = None
    driver._klai_database_init_tasks = {}
    driver.build_indices_and_constraints = AsyncMock(
        side_effect=[RuntimeError("temporary"), None]
    )

    with pytest.raises(RuntimeError, match="temporary"):
        await driver.ensure_database_initialized()
    await driver.ensure_database_initialized()

    assert driver.build_indices_and_constraints.await_count == 2
