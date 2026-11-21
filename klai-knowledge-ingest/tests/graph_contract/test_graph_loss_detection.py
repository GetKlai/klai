"""The nightly reconcile's graph check against a real Postgres and a real FalkorDB.

FalkorDB persists by snapshot only, so a crash or a kill mid-save loses writes
Postgres has already recorded as a finished extraction. The check counts the
active rows whose recorded, finished extraction has an episode the graph does
not hold; it never repairs anything.
"""

from __future__ import annotations

import time
import types

import pytest
from graphiti_core.driver.falkordb_driver import FalkorDriver

from knowledge_ingest import consistency_reconcile
from knowledge_ingest import graph as graph_module
from tests.graph_contract.conftest import Contract, seed_graph


async def test_counts_active_finished_extractions_whose_episodes_are_gone(
    graph_helpers: Contract, tenant_rows
) -> None:
    conn, insert = tenant_rows
    org_id = graph_helpers.group_id
    await seed_graph(graph_helpers, ["present"], {"present-fact": ["present"]})
    lost = await insert(
        {"graphiti_episode_ids": ["present", "gone"], "graphiti_episode_complete": True},
        active=True,
        path="lost",
    )
    await insert(
        {"graphiti_episode_ids": ["present"], "graphiti_episode_complete": True},
        active=True,
        path="intact",
    )
    in_flight = await insert(
        {"graphiti_episode_ids": ["not-yet"], "graphiti_episode_complete": True},
        active=True,
        path="in-flight",
    )
    await conn.execute(
        "UPDATE knowledge.artifacts SET created_at = $1 WHERE id = $2", int(time.time()), in_flight
    )
    await insert(
        {"graphiti_episode_ids": ["gone-too"], "graphiti_episode_complete": False},
        active=True,
        path="still-extracting",
    )
    await insert({"graphiti_episode_ids": ["gone-history"]}, active=False, path="closed")

    report = await consistency_reconcile.lost_graph_extractions(conn, {org_id})

    assert report == {
        "graph_lost_extractions": 1,
        "graph_lost_by_org": {org_id: 1},
        "graph_lost_sample": [{"org_id": org_id, "artifact_id": lost}],
    }


async def test_an_unreachable_graph_is_an_error_not_a_count(
    graph_helpers: Contract, tenant_rows, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn, insert = tenant_rows
    await insert({"graphiti_episode_ids": ["gone"], "graphiti_episode_complete": True}, active=True)
    # Nothing listens on port 1; the driver connects when the check asks for it.
    monkeypatch.setattr(
        graph_module,
        "_get_graphiti",
        lambda: types.SimpleNamespace(driver=FalkorDriver(host="127.0.0.1", port=1)),
    )

    with pytest.raises(Exception):  # noqa: B017 - any connection error, never a count
        await consistency_reconcile.lost_graph_extractions(conn, {graph_helpers.group_id})
