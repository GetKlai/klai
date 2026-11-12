"""scripts/prune_stale_graph.py against a real FalkorDB graph and real artifact rows."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest

from scripts import prune_stale_graph
from tests.graph_contract.conftest import Contract, seed_graph, stored_graph


@pytest.fixture
def prune(graph_helpers: Contract, tenant_rows, monkeypatch: pytest.MonkeyPatch):
    conn, insert = tenant_rows

    @asynccontextmanager
    async def _tenant(_org_id: str):
        yield conn

    monkeypatch.setattr(prune_stale_graph, "tenant_scoped_connection", _tenant)
    return insert


_EDGES = {
    "replaced-only": ["replaced"],
    "replaced-and-current": ["replaced", "current"],
    "current-only": ["current"],
    "cites-missing-episode": ["missing"],
    "graph-only-episode": ["no-row"],
    "removed-document": ["removed", "missing"],
}


async def _seed_tenant(contract: Contract, insert) -> None:
    await seed_graph(contract, ["current", "replaced", "removed", "no-row"], _EDGES)
    current = await insert({"graphiti_episode_ids": ["current"]}, active=True)
    await insert({"graphiti_episode_ids": ["replaced"]}, active=False, superseded_by=current)
    await insert({"graphiti_episode_ids": ["removed"]}, active=False, path="removed-page")
    driver = contract.graphiti.driver.clone(database=contract.group_id)
    # Edges no episode backs at all: an empty list and no list.
    await driver.execute_query(
        "CREATE (:Entity {uuid: 'x1', group_id: $g})-[:RELATES_TO {uuid: 'empty-list', "
        "episodes: []}]->(:Entity {uuid: 'x2', group_id: $g}), "
        "(:Entity {uuid: 'x3', group_id: $g})-[:RELATES_TO {uuid: 'no-list'}]->"
        "(:Entity {uuid: 'x4', group_id: $g})",
        g=contract.group_id,
    )
    # Written by a running job and not recorded yet.
    await driver.execute_query(
        "CREATE (:Episodic {uuid: 'in-flight', group_id: $g, created_at: $now})",
        g=contract.group_id,
        now=datetime.now(UTC),
    )


async def test_dry_run_counts_and_changes_nothing(
    graph_helpers: Contract, prune, capsys: pytest.CaptureFixture
) -> None:
    await _seed_tenant(graph_helpers, prune)
    before = await stored_graph(graph_helpers)

    assert await prune_stale_graph.run(graph_helpers.group_id, execute=False) == 0

    assert await stored_graph(graph_helpers) == before
    out = capsys.readouterr().out
    for line in (
        "episodes_in_graph: 5",
        "episodes_current: 1",
        "episodes_removed_replaced_version: 1",
        "episodes_removed_removed_document: 1",
        "episodes_removed_recorded_by_no_row: 1",
        "episodes_kept_too_recent: 1",
        "edges_in_graph: 8",
        "edges_removed: 6",
        "edges_removed_without_existing_episode: 3",
        "edges_kept: 2",
        "edges_kept_with_shorter_episode_list: 1",
        "documents_left_without_episode: 0",
    ):
        assert f"  {line}\n" in out


async def test_execute_leaves_only_current_facts(
    graph_helpers: Contract, prune, capsys: pytest.CaptureFixture
) -> None:
    await _seed_tenant(graph_helpers, prune)

    assert await prune_stale_graph.run(graph_helpers.group_id, execute=True) == 0

    episodes, edges, _ = await stored_graph(graph_helpers)
    assert episodes == {"current", "in-flight"}
    assert edges == {"replaced-and-current": ["current"], "current-only": ["current"]}
    after = capsys.readouterr().out.split("after\n")[1]
    assert "  edges_removed: 0\n" in after


@pytest.mark.parametrize("link", ["path", "source_ref"])
async def test_an_unlinked_extraction_stays_until_the_new_version_is_extracted(
    graph_helpers: Contract, prune, capsys: pytest.CaptureFixture, link: str
) -> None:
    """A closed row without superseded_by is still the same document by path or source."""
    await seed_graph(graph_helpers, ["previous"], {"previous-fact": ["previous"]})
    by_path = link == "path"
    source_ref = None if by_path else "item-7"
    await prune({}, active=True, path="page" if by_path else "renamed-page", source_ref=source_ref)
    await prune({"graphiti_episode_ids": ["previous"]}, active=False, source_ref=source_ref)
    before = await stored_graph(graph_helpers)

    assert await prune_stale_graph.run(graph_helpers.group_id, execute=True) == 0

    assert await stored_graph(graph_helpers) == before
    assert "episodes_kept_document_not_finished_extracting: 1" in capsys.readouterr().out


async def test_refuses_to_empty_a_document_that_has_graph_content(
    graph_helpers: Contract, prune, capsys: pytest.CaptureFixture
) -> None:
    """The kept version lists episodes the graph lost; only its predecessor's are there."""
    await seed_graph(graph_helpers, ["older"], {"older-fact": ["older"]})
    active = await prune({"graphiti_episode_ids": ["lost"]}, active=True)
    await prune({"graphiti_episode_ids": ["older"]}, active=False, superseded_by=active)
    before = await stored_graph(graph_helpers)

    assert await prune_stale_graph.run(graph_helpers.group_id, execute=True) == 2

    assert await stored_graph(graph_helpers) == before
    out = capsys.readouterr().out
    assert "documents_left_without_episode: 1" in out
    assert f"active artifact {active}" in out
