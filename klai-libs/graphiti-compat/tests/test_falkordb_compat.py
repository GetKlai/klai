"""Behavior tests for the temporary graphiti-core 0.29 FalkorDB fixes."""

from __future__ import annotations

import asyncio
import importlib
import re
from datetime import UTC, datetime
from pathlib import Path
from types import MethodType
from unittest.mock import AsyncMock

import graphiti_core
import pytest
from graphiti_core.driver.driver import GraphProvider
from graphiti_core.driver.falkordb_driver import FalkorDriver
from graphiti_core.search import search_utils
from graphiti_core.search.search_filters import SearchFilters

import klai_graphiti_compat
from klai_graphiti_compat import _wrap_database_initialization, apply_falkordb_compat

_UPSTREAM_EDGE_BFS_SEARCH = search_utils.edge_bfs_search
_UPSTREAM_EDGE_SIMILARITY_SEARCH = search_utils.edge_similarity_search
_UPSTREAM_NODE_SIMILARITY_SEARCH = search_utils.node_similarity_search


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
    driver.execute_query = AsyncMock(return_value=([], [], None))
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
    driver.build_indices_and_constraints = AsyncMock(side_effect=[RuntimeError("temporary"), None])
    driver.execute_query = AsyncMock(return_value=([], [], None))

    with pytest.raises(RuntimeError, match="temporary"):
        await driver.ensure_database_initialized()
    await driver.ensure_database_initialized()

    assert driver.build_indices_and_constraints.await_count == 2


def _reset_vector_search_patch(monkeypatch) -> None:
    monkeypatch.setattr(klai_graphiti_compat, "_vector_search_patched", False)
    monkeypatch.setattr(klai_graphiti_compat, "_ann_candidate_search_enabled", False)
    klai_graphiti_compat._index_unavailable_until.clear()
    klai_graphiti_compat._vector_fallback_last_logged.clear()
    reset_targets = {
        "graphiti_core.search.search_utils": {
            "edge_similarity_search",
            "node_similarity_search",
        },
        "graphiti_core.search.search": {
            "edge_similarity_search",
            "node_similarity_search",
        },
        "graphiti_core.utils.maintenance.node_operations": {"node_similarity_search"},
    }
    for module_name, names in _graphiti_similarity_importers().items():
        reset_targets.setdefault(module_name, set()).update(names)
    for module_name, names in reset_targets.items():
        module = importlib.import_module(module_name)
        if "edge_similarity_search" in names:
            monkeypatch.setattr(module, "edge_similarity_search", _UPSTREAM_EDGE_SIMILARITY_SEARCH)
        if "node_similarity_search" in names:
            monkeypatch.setattr(module, "node_similarity_search", _UPSTREAM_NODE_SIMILARITY_SEARCH)


def test_ann_flag_off_leaves_similarity_searches_unchanged(monkeypatch) -> None:
    _reset_vector_search_patch(monkeypatch)

    apply_falkordb_compat()

    assert search_utils.edge_similarity_search is _UPSTREAM_EDGE_SIMILARITY_SEARCH
    assert search_utils.node_similarity_search is _UPSTREAM_NODE_SIMILARITY_SEARCH


def _graphiti_similarity_importers() -> dict[str, set[str]]:
    root = Path(graphiti_core.__file__).parent
    importers: dict[str, set[str]] = {}
    import_start = re.compile(
        r"^from (graphiti_core\.search\.search_utils|\.search_utils) import\b"
    )

    for source_path in root.rglob("*.py"):
        lines = source_path.read_text(encoding="utf-8").splitlines()
        idx = 0
        while idx < len(lines):
            line = lines[idx]
            if not import_start.search(line):
                idx += 1
                continue
            block = [line]
            while "(" in block[0] and ")" not in "\n".join(block) and idx + 1 < len(lines):
                idx += 1
                line = lines[idx]
                block.append(line)
            text = "\n".join(block)
            names = {
                name
                for name in ("edge_similarity_search", "node_similarity_search")
                if name in text
            }
            if names:
                module_name = "graphiti_core." + str(source_path.relative_to(root))[:-3].replace(
                    "/", "."
                )
                if module_name.endswith(".__init__"):
                    module_name = module_name[: -len(".__init__")]
                importers.setdefault(module_name, set()).update(names)
            idx += 1
    return importers


def test_ann_flag_on_replaces_similarity_searches_and_derived_importers(monkeypatch) -> None:
    _reset_vector_search_patch(monkeypatch)

    apply_falkordb_compat(ann_candidate_search=True)

    assert search_utils.edge_similarity_search is not _UPSTREAM_EDGE_SIMILARITY_SEARCH
    assert search_utils.node_similarity_search is not _UPSTREAM_NODE_SIMILARITY_SEARCH

    importers = _graphiti_similarity_importers()
    assert importers
    assert "graphiti_core.search.search" in importers
    assert any("edge_similarity_search" in names for names in importers.values())
    for module_name, names in importers.items():
        module = importlib.import_module(module_name)
        for name in names:
            assert getattr(module, name) is getattr(search_utils, name)


@pytest.mark.asyncio
async def test_edge_similarity_delegates_edge_uuid_filters(monkeypatch) -> None:
    _reset_vector_search_patch(monkeypatch)
    calls = []

    async def original_edge_similarity(*args):
        calls.append(args)
        return ["original"]

    monkeypatch.setattr(search_utils, "edge_similarity_search", original_edge_similarity)
    apply_falkordb_compat(ann_candidate_search=True)

    driver = type(
        "Driver",
        (),
        {"provider": GraphProvider.FALKORDB, "search_interface": None},
    )()
    result = await search_utils.edge_similarity_search(
        driver,
        [0.1, 0.2],
        None,
        None,
        SearchFilters(edge_uuids=["edge-1"]),
    )

    assert result == ["original"]
    assert len(calls) == 1


def _edge_record(uuid: str) -> dict[str, object]:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return {
        "uuid": uuid,
        "source_node_uuid": "source",
        "target_node_uuid": "target",
        "group_id": "org-1",
        "created_at": now,
        "name": "RELATES_TO",
        "fact": f"fact {uuid}",
        "episodes": [],
        "expired_at": None,
        "valid_at": None,
        "invalid_at": None,
        "reference_time": None,
        "attributes": {},
    }


def _node_record(uuid: str) -> dict[str, object]:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return {
        "uuid": uuid,
        "name": f"Node {uuid}",
        "group_id": "org-1",
        "labels": ["Entity"],
        "created_at": now,
        "summary": "summary",
        "attributes": {},
        "name_embedding": [0.1, 0.2],
    }


@pytest.mark.asyncio
async def test_edge_similarity_fast_path_uses_vector_index_and_group_post_filter(
    monkeypatch,
) -> None:
    _reset_vector_search_patch(monkeypatch)
    apply_falkordb_compat(ann_candidate_search=True)

    class Driver:
        provider = GraphProvider.FALKORDB
        search_interface = None
        captured_query = ""

        def __init__(self) -> None:
            self.captured_kwargs = {}

        async def execute_query(self, cypher, **kwargs):
            self.captured_query = cypher
            self.captured_kwargs = kwargs
            return [_edge_record("edge-1")], [], None

    driver = Driver()
    result = await search_utils.edge_similarity_search(
        driver,
        [0.1, 0.2],
        None,
        None,
        SearchFilters(),
        group_ids=["org-1"],
        limit=10,
    )

    assert [edge.uuid for edge in result] == ["edge-1"]
    assert "db.idx.vector.queryRelationships" in driver.captured_query
    assert "(2 - score)/2" in driver.captured_query
    assert "e.group_id IN $group_ids" in driver.captured_query
    assert driver.captured_kwargs["k"] == 40


@pytest.mark.asyncio
async def test_node_similarity_fast_path_uses_vector_index_and_group_post_filter(
    monkeypatch,
) -> None:
    _reset_vector_search_patch(monkeypatch)
    apply_falkordb_compat(ann_candidate_search=True)

    class Driver:
        provider = GraphProvider.FALKORDB
        search_interface = None
        captured_query = ""

        def __init__(self) -> None:
            self.captured_kwargs = {}

        async def execute_query(self, cypher, **kwargs):
            self.captured_query = cypher
            self.captured_kwargs = kwargs
            return [_node_record("node-1")], [], None

    driver = Driver()
    result = await search_utils.node_similarity_search(
        driver,
        [0.1, 0.2],
        SearchFilters(),
        group_ids=["org-1"],
        limit=15,
    )

    assert [node.uuid for node in result] == ["node-1"]
    assert "db.idx.vector.queryNodes" in driver.captured_query
    assert "(2 - score)/2" in driver.captured_query
    assert "ORDER BY score DESC" in driver.captured_query
    assert "n.group_id IN $group_ids" in driver.captured_query
    assert driver.captured_kwargs["k"] == 60


@pytest.mark.asyncio
async def test_node_similarity_delegates_node_label_filters(monkeypatch) -> None:
    _reset_vector_search_patch(monkeypatch)
    calls = []

    async def original_node_similarity(*args):
        calls.append(args)
        return ["original"]

    monkeypatch.setattr(search_utils, "node_similarity_search", original_node_similarity)
    apply_falkordb_compat(ann_candidate_search=True)

    driver = type(
        "Driver",
        (),
        {"provider": GraphProvider.FALKORDB, "search_interface": None},
    )()
    result = await search_utils.node_similarity_search(
        driver,
        [0.1, 0.2],
        SearchFilters(node_labels=["X"]),
    )

    assert result == ["original"]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_similarity_fast_paths_delegate_when_limit_is_not_positive(monkeypatch) -> None:
    _reset_vector_search_patch(monkeypatch)
    calls = []

    async def original_edge_similarity(*args):
        calls.append(("edge", args))
        return []

    async def original_node_similarity(*args):
        calls.append(("node", args))
        return []

    monkeypatch.setattr(search_utils, "edge_similarity_search", original_edge_similarity)
    monkeypatch.setattr(search_utils, "node_similarity_search", original_node_similarity)
    apply_falkordb_compat(ann_candidate_search=True)

    class Driver:
        provider = GraphProvider.FALKORDB
        search_interface = None

        async def execute_query(self, *_args, **_kwargs):
            raise AssertionError("limit<=0 must not call the vector procedure")

    driver = Driver()
    await search_utils.edge_similarity_search(
        driver, [0.1, 0.2], None, None, SearchFilters(), limit=0
    )
    await search_utils.node_similarity_search(driver, [0.1, 0.2], SearchFilters(), limit=0)

    assert [name for name, _args in calls] == ["edge", "node"]


@pytest.mark.asyncio
async def test_edge_similarity_distance_scores_are_converted_for_filter_and_order(
    monkeypatch,
) -> None:
    _reset_vector_search_patch(monkeypatch)
    apply_falkordb_compat(ann_candidate_search=True)

    class Driver:
        provider = GraphProvider.FALKORDB
        search_interface = None
        captured_query = ""

        async def execute_query(self, _cypher, **kwargs):
            # A fake driver cannot prove the conversion happens — only the
            # generated Cypher can. The original version of this test
            # re-implemented (2-d)/2, the min_score filter and the ordering
            # in Python and would have passed against a broken query
            # (Opus review 2026-09-01, finding #4). Assert the load-bearing
            # clauses in the query text instead.
            self.captured_query = _cypher
            distances = {"too-low": 0.9, "best": 0.0, "mid": 0.4}
            min_score = kwargs["min_score"]
            ordered = sorted(
                (
                    (uuid, (2 - distance) / 2)
                    for uuid, distance in distances.items()
                    if (2 - distance) / 2 > min_score
                ),
                key=lambda item: item[1],
                reverse=True,
            )
            return [_edge_record(uuid) for uuid, _score in ordered], [], None

    driver = Driver()
    result = await search_utils.edge_similarity_search(
        driver,
        [0.1, 0.2],
        None,
        None,
        SearchFilters(),
        limit=10,
        min_score=0.7,
    )

    assert [edge.uuid for edge in result] == ["best", "mid"]
    assert "(2 - score)/2" in driver.captured_query
    assert "WHERE score > $min_score" in driver.captured_query
    assert "ORDER BY score DESC" in driver.captured_query
    assert "db.idx.vector.queryRelationships" in driver.captured_query


@pytest.mark.asyncio
async def test_index_marker_error_delegates_and_memoizes_unavailability(
    monkeypatch,
) -> None:
    _reset_vector_search_patch(monkeypatch)

    sentinel = [object()]
    original_calls = 0

    async def fake_original(*_args, **_kwargs):
        nonlocal original_calls
        original_calls += 1
        return sentinel

    monkeypatch.setattr(search_utils, "edge_similarity_search", fake_original)
    apply_falkordb_compat(ann_candidate_search=True)

    class Driver:
        provider = GraphProvider.FALKORDB
        search_interface = None
        _database = "tenant-x"

        def __init__(self) -> None:
            self.query_calls = 0

        async def execute_query(self, _cypher, **_kwargs):
            self.query_calls += 1
            raise RuntimeError("Invalid arguments for procedure 'db.idx.vector.queryRelationships'")

    driver = Driver()
    first = await search_utils.edge_similarity_search(
        driver, [0.1, 0.2], None, None, SearchFilters(), limit=10, min_score=0.7
    )
    second = await search_utils.edge_similarity_search(
        driver, [0.1, 0.2], None, None, SearchFilters(), limit=10, min_score=0.7
    )

    assert first is sentinel
    assert second is sentinel
    assert original_calls == 2
    assert driver.query_calls == 1
    assert "tenant-x:edge_similarity_search" in klai_graphiti_compat._index_unavailable_until


@pytest.mark.asyncio
async def test_edge_index_unavailability_does_not_disable_healthy_node_fast_path(
    monkeypatch,
) -> None:
    _reset_vector_search_patch(monkeypatch)

    async def fake_original_edge(*_args, **_kwargs):
        return []

    async def fake_original_node(*_args, **_kwargs):
        raise AssertionError("healthy node path must keep using the vector index")

    monkeypatch.setattr(search_utils, "edge_similarity_search", fake_original_edge)
    monkeypatch.setattr(search_utils, "node_similarity_search", fake_original_node)
    apply_falkordb_compat(ann_candidate_search=True)

    class Driver:
        provider = GraphProvider.FALKORDB
        search_interface = None
        _database = "tenant-shared"

        def __init__(self) -> None:
            self.queries: list[str] = []

        async def execute_query(self, cypher, **_kwargs):
            self.queries.append(cypher)
            if "queryRelationships" in cypher:
                raise RuntimeError(
                    "Invalid arguments for procedure 'db.idx.vector.queryRelationships'"
                )
            return [_node_record("node-1")], [], None

    driver = Driver()
    await search_utils.edge_similarity_search(
        driver, [0.1, 0.2], None, None, SearchFilters(), limit=10, min_score=0.7
    )
    result = await search_utils.node_similarity_search(
        driver, [0.1, 0.2], SearchFilters(), limit=10, min_score=0.7
    )

    assert [node.uuid for node in result] == ["node-1"]
    assert [query.count("db.idx.vector") for query in driver.queries] == [1, 1]
    assert "tenant-shared:edge_similarity_search" in klai_graphiti_compat._index_unavailable_until
    assert (
        "tenant-shared:node_similarity_search" not in klai_graphiti_compat._index_unavailable_until
    )


@pytest.mark.asyncio
async def test_non_index_error_re_raises_without_delegating(monkeypatch) -> None:
    _reset_vector_search_patch(monkeypatch)

    async def fake_original(*_args, **_kwargs):
        raise AssertionError("non-index errors must not delegate")

    monkeypatch.setattr(search_utils, "edge_similarity_search", fake_original)
    apply_falkordb_compat(ann_candidate_search=True)

    class Driver:
        provider = GraphProvider.FALKORDB
        search_interface = None
        _database = "tenant-timeout"

        async def execute_query(self, _cypher, **_kwargs):
            raise TimeoutError("query timed out")

    with pytest.raises(TimeoutError, match="query timed out"):
        await search_utils.edge_similarity_search(
            Driver(), [0.1, 0.2], None, None, SearchFilters(), limit=10, min_score=0.7
        )


@pytest.mark.asyncio
async def test_index_recovery_clears_memoized_state_and_logs_once(monkeypatch, caplog) -> None:
    _reset_vector_search_patch(monkeypatch)
    now = 100.0
    monkeypatch.setattr(klai_graphiti_compat, "_monotonic", lambda: now)

    original_calls = 0

    async def fake_original(*_args, **_kwargs):
        nonlocal original_calls
        original_calls += 1
        return []

    monkeypatch.setattr(search_utils, "node_similarity_search", fake_original)
    apply_falkordb_compat(ann_candidate_search=True)

    class Driver:
        provider = GraphProvider.FALKORDB
        search_interface = None
        _database = "tenant-recovered"

        def __init__(self) -> None:
            self.fail = True

        async def execute_query(self, _cypher, **_kwargs):
            if self.fail:
                raise RuntimeError("Invalid arguments for procedure 'db.idx.vector.queryNodes'")
            return [], [], None

    driver = Driver()
    await search_utils.node_similarity_search(
        driver, [0.1, 0.2], SearchFilters(), limit=10, min_score=0.7
    )
    assert original_calls == 1

    now += klai_graphiti_compat._INDEX_RETRY_SECONDS + 1
    driver.fail = False
    with caplog.at_level("INFO", logger="klai_graphiti_compat"):
        result = await search_utils.node_similarity_search(
            driver, [0.1, 0.2], SearchFilters(), limit=10, min_score=0.7
        )

    assert result == []
    assert original_calls == 1
    assert (
        "tenant-recovered:node_similarity_search"
        not in klai_graphiti_compat._index_unavailable_until
    )
    assert (
        "tenant-recovered:node_similarity_search"
        not in klai_graphiti_compat._vector_fallback_last_logged
    )
    recovered = [
        record
        for record in caplog.records
        if record.getMessage() == "graphiti_falkor_vector_search_recovered"
    ]
    assert len(recovered) == 1
    assert recovered[0].graphiti_fn == "node_similarity_search"


@pytest.mark.asyncio
async def test_recovery_log_only_fires_for_recovered_similarity_path(monkeypatch, caplog) -> None:
    _reset_vector_search_patch(monkeypatch)
    now = 100.0
    monkeypatch.setattr(klai_graphiti_compat, "_monotonic", lambda: now)

    async def fake_original_edge(*_args, **_kwargs):
        return []

    async def fake_original_node(*_args, **_kwargs):
        return []

    monkeypatch.setattr(search_utils, "edge_similarity_search", fake_original_edge)
    monkeypatch.setattr(search_utils, "node_similarity_search", fake_original_node)
    apply_falkordb_compat(ann_candidate_search=True)

    class Driver:
        provider = GraphProvider.FALKORDB
        search_interface = None
        _database = "tenant-two-paths"

        def __init__(self) -> None:
            self.edge_fail = True
            self.node_fail = True

        async def execute_query(self, cypher, **_kwargs):
            if "queryRelationships" in cypher and self.edge_fail:
                raise RuntimeError(
                    "Invalid arguments for procedure 'db.idx.vector.queryRelationships'"
                )
            if "queryNodes" in cypher and self.node_fail:
                raise RuntimeError("Invalid arguments for procedure 'db.idx.vector.queryNodes'")
            return [], [], None

    driver = Driver()
    await search_utils.edge_similarity_search(
        driver, [0.1, 0.2], None, None, SearchFilters(), limit=10, min_score=0.7
    )
    await search_utils.node_similarity_search(
        driver, [0.1, 0.2], SearchFilters(), limit=10, min_score=0.7
    )

    now += klai_graphiti_compat._INDEX_RETRY_SECONDS + 1
    driver.edge_fail = False
    with caplog.at_level("INFO", logger="klai_graphiti_compat"):
        await search_utils.edge_similarity_search(
            driver, [0.1, 0.2], None, None, SearchFilters(), limit=10, min_score=0.7
        )

    recovered = [
        record
        for record in caplog.records
        if record.getMessage() == "graphiti_falkor_vector_search_recovered"
    ]
    assert len(recovered) == 1
    assert recovered[0].graphiti_fn == "edge_similarity_search"
    assert (
        "tenant-two-paths:node_similarity_search" in klai_graphiti_compat._index_unavailable_until
    )


@pytest.mark.asyncio
async def test_missing_database_attribute_does_not_memoize_unavailability(monkeypatch) -> None:
    _reset_vector_search_patch(monkeypatch)

    original_calls = 0

    async def fake_original(*_args, **_kwargs):
        nonlocal original_calls
        original_calls += 1
        return []

    monkeypatch.setattr(search_utils, "edge_similarity_search", fake_original)
    apply_falkordb_compat(ann_candidate_search=True)

    class Driver:
        provider = GraphProvider.FALKORDB
        search_interface = None

        def __init__(self) -> None:
            self.query_calls = 0

        async def execute_query(self, _cypher, **_kwargs):
            self.query_calls += 1
            raise RuntimeError("Invalid arguments for procedure 'db.idx.vector.queryRelationships'")

    driver = Driver()
    await search_utils.edge_similarity_search(
        driver, [0.1, 0.2], None, None, SearchFilters(), limit=10, min_score=0.7
    )
    await search_utils.edge_similarity_search(
        driver, [0.1, 0.2], None, None, SearchFilters(), limit=10, min_score=0.7
    )

    assert driver.query_calls == 2
    assert original_calls == 2
    assert klai_graphiti_compat._index_unavailable_until == {}
    assert klai_graphiti_compat._vector_fallback_last_logged == {}


@pytest.mark.asyncio
async def test_fallback_warning_is_throttled_per_database_and_structured(
    monkeypatch, caplog
) -> None:
    _reset_vector_search_patch(monkeypatch)

    async def fake_original(*_args, **_kwargs):
        return []

    monkeypatch.setattr(search_utils, "edge_similarity_search", fake_original)
    apply_falkordb_compat(ann_candidate_search=True)

    class Driver:
        provider = GraphProvider.FALKORDB
        search_interface = None
        _database = "tenant-logged"

        async def execute_query(self, _cypher, **_kwargs):
            raise RuntimeError("undefined attribute db.idx.vector")

    with caplog.at_level("WARNING", logger="klai_graphiti_compat"):
        await search_utils.edge_similarity_search(
            Driver(), [0.1, 0.2], None, None, SearchFilters(), limit=10, min_score=0.7
        )
        klai_graphiti_compat._index_unavailable_until.clear()
        await search_utils.edge_similarity_search(
            Driver(), [0.1, 0.2], None, None, SearchFilters(), limit=10, min_score=0.7
        )

    warnings = [
        record
        for record in caplog.records
        if record.levelname == "WARNING"
        and record.getMessage() == "graphiti_falkor_vector_search_fell_back_to_scan"
    ]
    assert len(warnings) == 1
    assert warnings[0].graphiti_database == "tenant-logged"
    assert warnings[0].graphiti_fn == "edge_similarity_search"


@pytest.mark.asyncio
async def test_vector_indexes_are_created_after_graphiti_indices(monkeypatch) -> None:
    monkeypatch.setattr(klai_graphiti_compat, "_ann_candidate_search_enabled", True)
    events: list[str] = []

    class Driver:
        async def build_indices_and_constraints(self):
            events.append("graphiti")

        async def execute_query(self, query):
            events.append(query)
            return [], [], None

    await klai_graphiti_compat._initialize_database(Driver())

    assert events[0] == "graphiti"
    assert "RELATES_TO" in events[1]
    assert "fact_embedding" in events[1]
    assert "Entity" in events[2]
    assert "name_embedding" in events[2]


@pytest.mark.asyncio
async def test_duplicate_vector_index_errors_are_success() -> None:
    class Driver:
        async def execute_query(self, _query):
            raise RuntimeError("Property is already indexed")

    await klai_graphiti_compat._create_vector_indexes(Driver())
