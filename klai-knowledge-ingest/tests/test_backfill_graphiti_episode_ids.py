from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _connection_context(conn: AsyncMock) -> MagicMock:
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


@pytest.mark.asyncio
async def test_dry_run_reports_rows_without_updating():
    from scripts import backfill_graphiti_episode_ids as script

    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=2)

    with patch(
        "scripts.backfill_graphiti_episode_ids.tenant_scoped_connection",
        return_value=_connection_context(conn),
    ):
        result = await script.run("org-1", dry_run=True)

    assert result == 0
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_populates_list_from_scalar_idempotently_for_one_tenant():
    from scripts import backfill_graphiti_episode_ids as script

    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=2)
    conn.execute = AsyncMock(return_value="UPDATE 2")

    with patch(
        "scripts.backfill_graphiti_episode_ids.tenant_scoped_connection",
        return_value=_connection_context(conn),
    ):
        result = await script.run("org-1", dry_run=False)

    assert result == 0
    sql = conn.execute.await_args.args[0]
    assert "graphiti_episode_ids" in sql
    assert "jsonb_build_array" in sql
    assert "no-chunks" in sql
    assert "NOT (extra::jsonb ? 'graphiti_episode_ids')" in sql
    assert conn.execute.await_args.args[1] == "org-1"
