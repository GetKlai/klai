"""The org-wide sweep after a connector purge never removes history, only true orphans.

Replaced versions' episodes are removed per run with the owner's approval
(scripts/prune_stale_graph.py), so the sweep must keep every episode a row
records. Runs the purge's own two steps (``connector_cleanup`` step 10)
against a real Postgres and a real FalkorDB; needs
``GRAPH_REFRESH_BUDGET_TEST_DSN`` next to the graph-contract variables.
"""

from __future__ import annotations

import json
import os
import uuid

import asyncpg
import pytest

from knowledge_ingest import graph as graph_module
from knowledge_ingest import pg_store
from tests.graph_contract.conftest import Contract, seed_graph, stored_graph

_DSN = os.environ.get("GRAPH_REFRESH_BUDGET_TEST_DSN")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.no_mock_db_helpers,
    pytest.mark.skipif(
        not _DSN and os.environ.get("GRAPH_CONTRACT_REQUIRED") != "1",
        reason="GRAPH_REFRESH_BUDGET_TEST_DSN is not configured",
    ),
]


async def _insert(conn, org_id: str, extra: dict, *, active: bool, superseded_by=None) -> str:
    artifact_id = str(uuid.uuid4())
    await conn.execute(
        """
        INSERT INTO knowledge.artifacts (
            id, org_id, provenance_type, assertion_mode, belief_time_start,
            belief_time_end, superseded_by, created_at, kb_slug, path, extra
        ) VALUES ($1, $2, 'observed', 'factual', 1, $3, $4, 1, 'docs', 'page', $5::jsonb)
        """,
        artifact_id,
        org_id,
        253402300800 if active else 2,
        superseded_by,
        json.dumps(extra),
    )
    return artifact_id


async def test_purge_sweep_keeps_replaced_versions_and_removes_unrecorded_episodes(
    graph_helpers: Contract,
) -> None:
    assert _DSN, "GRAPH_REFRESH_BUDGET_TEST_DSN is required with GRAPH_CONTRACT_REQUIRED=1"
    org_id = graph_helpers.group_id
    await seed_graph(
        graph_helpers,
        ["current", "replaced", "no-row"],
        {"current-fact": ["current"], "replaced-fact": ["replaced"], "orphan-fact": ["no-row"]},
    )
    conn = await asyncpg.connect(_DSN)
    await conn.execute("SELECT set_config('app.current_org_id', $1, false)", org_id)
    try:
        active = await _insert(conn, org_id, {"graphiti_episode_ids": ["current"]}, active=True)
        await _insert(
            conn, org_id, {"graphiti_episode_ids": ["replaced"]}, active=False, superseded_by=active
        )

        alive = await pg_store.get_alive_episode_uuids_for_org(conn, org_id)
        deleted = await graph_module.sweep_orphan_episodes_org_wide(org_id, alive)
    finally:
        await conn.execute("DELETE FROM knowledge.artifacts WHERE org_id = $1", org_id)
        await conn.close()

    episodes, edges, _ = await stored_graph(graph_helpers)
    assert deleted == 1
    assert episodes == {"current", "replaced"}
    assert set(edges) == {"current-fact", "replaced-fact"}
