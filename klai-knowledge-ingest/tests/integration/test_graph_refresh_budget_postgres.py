"""Real-Postgres check of the per-org daily graph-refresh cap."""

from __future__ import annotations

import asyncio
import os
import uuid

import asyncpg
import pytest

from knowledge_ingest import pg_store

_DSN = os.environ.get("GRAPH_REFRESH_BUDGET_TEST_DSN")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.no_mock_db_helpers,
    pytest.mark.skipif(not _DSN, reason="GRAPH_REFRESH_BUDGET_TEST_DSN is not configured"),
]


async def _tenant_connection(org_id: str) -> asyncpg.Connection:
    assert _DSN is not None
    conn = await asyncpg.connect(_DSN)
    await conn.execute("SELECT set_config('app.current_org_id', $1, false)", org_id)
    return conn


@pytest.mark.asyncio
async def test_concurrent_reservations_never_exceed_the_daily_cap() -> None:
    org_id = f"org-budget-{uuid.uuid4().hex}"
    conns = [await _tenant_connection(org_id) for _ in range(10)]
    try:
        reserved = await asyncio.gather(
            *(pg_store.reserve_graph_refresh_slot(conn, org_id, 4) for conn in conns)
        )
        assert sum(reserved) == 4

        await pg_store.release_graph_refresh_slot(conns[0], org_id)
        assert await pg_store.reserve_graph_refresh_slot(conns[0], org_id, 4) is True
        assert await pg_store.reserve_graph_refresh_slot(conns[0], org_id, 4) is False
    finally:
        await conns[0].execute(
            "DELETE FROM knowledge.graph_refresh_budget WHERE org_id = $1", org_id
        )
        for conn in conns:
            await conn.close()
