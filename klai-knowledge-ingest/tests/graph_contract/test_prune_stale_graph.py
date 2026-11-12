"""scripts/prune_stale_graph.py against a real FalkorDB graph.

Postgres is a fake connection here: which episodes are alive is
``get_current_graph_episode_uuids_for_org``'s job, proven against real Postgres in
tests/integration/test_alive_episodes_postgres.py.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from scripts import prune_stale_graph
from tests.graph_contract.conftest import Contract, seed_graph, stored_graph


def _row(id_: str, episode_ids: list[str], *, active: bool, superseded_by: str | None = None):
    return {
        "id": id_,
        "superseded_by": superseded_by,
        "active": active,
        "episode_ids": episode_ids,
    }


def _postgres(monkeypatch: pytest.MonkeyPatch, rows: list[dict], alive: set[str]) -> None:
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=rows)

    @asynccontextmanager
    async def _tenant(_org_id: str):
        yield conn

    monkeypatch.setattr(prune_stale_graph, "tenant_scoped_connection", _tenant)
    monkeypatch.setattr(
        prune_stale_graph.pg_store,
        "get_current_graph_episode_uuids_for_org",
        AsyncMock(return_value=alive),
    )


_ROWS = [
    _row("doc-current", ["current"], active=True),
    _row("doc-replaced", ["replaced"], active=False, superseded_by="doc-current"),
    _row("doc-removed", ["removed"], active=False),
]
_EDGES = {
    "replaced-only": ["replaced"],
    "replaced-and-current": ["replaced", "current"],
    "current-only": ["current"],
    "cites-missing-episode": ["missing"],
    "graph-only-episode": ["no-row"],
    "removed-document": ["removed", "missing"],
}


async def test_dry_run_counts_and_changes_nothing(
    graph_helpers: Contract, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    await seed_graph(graph_helpers, ["current", "replaced", "removed", "no-row"], _EDGES)
    _postgres(monkeypatch, _ROWS, {"current"})
    before = await stored_graph(graph_helpers)

    assert await prune_stale_graph.run(graph_helpers.group_id, execute=False) == 0

    assert await stored_graph(graph_helpers) == before
    out = capsys.readouterr().out
    for line in (
        "episodes_in_graph: 4",
        "episodes_alive_in_graph: 1",
        "episodes_removed_replaced_version: 1",
        "episodes_removed_removed_document: 1",
        "episodes_removed_no_row: 1",
        "edges_in_graph: 6",
        "edges_removed: 4",
        "edges_kept: 2",
        "edges_kept_with_shorter_episode_list: 1",
        "edges_without_existing_episode: 1",
        "documents_left_without_episode: 0",
    ):
        assert f"  {line}\n" in out


async def test_execute_leaves_only_current_facts(
    graph_helpers: Contract, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    await seed_graph(graph_helpers, ["current", "replaced", "removed", "no-row"], _EDGES)
    _postgres(monkeypatch, _ROWS, {"current"})

    assert await prune_stale_graph.run(graph_helpers.group_id, execute=True) == 0

    episodes, edges, _ = await stored_graph(graph_helpers)
    assert episodes == {"current"}
    assert edges == {"replaced-and-current": ["current"], "current-only": ["current"]}
    after = capsys.readouterr().out.split("after\n")[1]
    assert "  edges_removed: 0\n" in after
    assert "  episodes_in_graph: 1\n" in after


async def test_refuses_to_empty_a_document_that_has_graph_content(
    graph_helpers: Contract, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The kept version lists episodes the graph lost; only its predecessor's are there."""
    await seed_graph(graph_helpers, ["older"], {"older-fact": ["older"]})
    rows = [
        _row("doc-active", ["lost"], active=True),
        _row("doc-older", ["older"], active=False, superseded_by="doc-active"),
    ]
    _postgres(monkeypatch, rows, {"lost"})
    before = await stored_graph(graph_helpers)

    assert await prune_stale_graph.run(graph_helpers.group_id, execute=True) == 2

    assert await stored_graph(graph_helpers) == before
    out = capsys.readouterr().out
    assert "documents_left_without_episode: 1" in out
    assert "REFUSING" in out
    assert "active artifact doc-active" in out
