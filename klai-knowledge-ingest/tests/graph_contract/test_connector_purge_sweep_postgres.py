"""The org-wide sweep after a connector purge never removes history, only true orphans.

Replaced versions' episodes are removed per run with the owner's approval
(scripts/prune_stale_graph.py), so the sweep must keep every episode a row
records, and an episode young enough to belong to a running job. Runs the
purge's own two steps (``connector_cleanup`` step 10) against a real Postgres
and a real FalkorDB.
"""

from __future__ import annotations

from datetime import UTC, datetime

from knowledge_ingest import graph as graph_module
from knowledge_ingest import pg_store
from tests.graph_contract.conftest import Contract, seed_graph, stored_graph


async def test_purge_sweep_keeps_history_and_young_episodes_and_removes_orphans(
    graph_helpers: Contract, tenant_rows
) -> None:
    conn, insert = tenant_rows
    org_id = graph_helpers.group_id
    await seed_graph(
        graph_helpers,
        ["current", "replaced", "no-row"],
        {"current-fact": ["current"], "replaced-fact": ["replaced"], "orphan-fact": ["no-row"]},
    )
    # A running job has written this episode and not yet recorded it.
    driver = graph_helpers.graphiti.driver.clone(database=org_id)
    await driver.execute_query(
        "CREATE (:Episodic {uuid: 'in-flight', group_id: $g, created_at: $now})",
        g=org_id,
        now=datetime.now(UTC),
    )
    active = await insert({"graphiti_episode_ids": ["current"]}, active=True)
    await insert({"graphiti_episode_ids": ["replaced"]}, active=False, superseded_by=active)

    alive = await pg_store.get_alive_episode_uuids_for_org(conn, org_id)
    deleted = await graph_module.sweep_orphan_episodes_org_wide(org_id, alive)

    episodes, edges, _ = await stored_graph(graph_helpers)
    assert deleted == 1
    assert episodes == {"current", "replaced", "in-flight"}
    assert set(edges) == {"current-fact", "replaced-fact"}
