"""The repair decides from current state, and never deletes a live document's points."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from knowledge_ingest import rebuild_tasks
from knowledge_ingest.scripts import repair_pg_qdrant_drift as repair
from knowledge_ingest.scripts.repair_pg_qdrant_drift import _plan


def _row(*, active=True, index_status="synced", has_text=True) -> dict:
    return {"active": active, "index_status": index_status, "has_text": has_text}


@pytest.mark.parametrize(
    "row, points, action",
    [
        # Reported as orphaned in production (2026-10): an upload whose
        # enrichment failed keeps its raw chunks and is still searchable.
        (_row(index_status="failed"), ["p1"], "skip"),
        (_row(), ["p1"], "skip"),
        (None, ["p1"], "delete-points"),
        (_row(active=False), ["p1"], "delete-points"),
        (_row(), [], "reindex"),
        (_row(has_text=False), [], "retire"),
        (_row(index_status="pending"), [], "skip"),
    ],
)
def test_plan_follows_the_current_state(row, points, action):
    assert _plan(row, points)[0] == action


def _conn_ctx(conn):
    @asynccontextmanager
    async def ctx(*args):
        yield conn

    return ctx


_ROW = {"artifact_id": "art-1", "org_id": "org-1", "kb_slug": "kb", "path": "doc.md"}


@pytest.mark.asyncio
async def test_retire_closes_only_the_planned_artifact_while_it_is_still_active():
    """Between the plan and the retire a new version of the path can become the
    active row; closing by path would retire that newer version."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)  # no longer the active, textless row
    soft_delete = AsyncMock(return_value=[("art-newer", "doc.md")])
    with (
        patch.object(repair, "tenant_scoped_connection", _conn_ctx(conn)),
        patch.object(repair.pg_store, "soft_delete_artifact", soft_delete),
        pytest.raises(RuntimeError),
    ):
        await repair._retire(_ROW)

    soft_delete.assert_not_awaited()
    sql, *params = conn.fetchrow.await_args.args
    assert "id = $1::uuid" in sql and "belief_time_end = $3" in sql
    assert params[0] == "art-1"


@pytest.mark.asyncio
async def test_reindex_reports_a_failed_rebuild_even_when_points_exist():
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"id": "art-1"})
    with (
        patch.object(repair, "cross_org_admin_connection", _conn_ctx(conn)),
        patch.object(rebuild_tasks, "_rebuild_artifact", AsyncMock(return_value="failed")),
        patch.object(repair, "_point_ids", AsyncMock(return_value=["p1"])),
        pytest.raises(RuntimeError, match="failed"),
    ):
        await repair._reindex(_ROW)
