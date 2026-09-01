from unittest.mock import AsyncMock, MagicMock

import pytest

from knowledge_ingest.resource_jobs import get_resource_job_counts


@pytest.mark.asyncio
async def test_monitoring_reports_raw_and_derived_connector_job_counts() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[
            {"status": status, "count": 1}
            for status in ("todo", "doing", "cancelled", "aborted", "failed", "succeeded")
        ]
    )
    acquired = MagicMock()
    acquired.__aenter__ = AsyncMock(return_value=conn)
    acquired.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire.return_value = acquired

    counts = await get_resource_job_counts(pool, "connector:org:kb:connector:generation")

    assert counts["pending"] == 1
    assert counts["running"] == 1
    assert counts["terminal"] == 4
    assert counts["failed_visible"] == 1
    assert set(counts) == {
        "todo",
        "doing",
        "cancelled",
        "aborted",
        "failed",
        "succeeded",
        "pending",
        "running",
        "terminal",
        "failed_visible",
    }
