from unittest.mock import AsyncMock, MagicMock

import pytest
from structlog.testing import capture_logs

from knowledge_ingest.queues import CRAWL_JOBS, ENRICH_BULK, GRAPHITI_BULK
from knowledge_ingest.resource_jobs import (
    CONNECTOR_WRITER_QUEUES,
    ConnectorResource,
    cancel_jobs_by_resource_key,
    connector_resource_key,
    get_resource_job_counts,
    list_live_jobs_by_resource_key,
    parse_connector_resource_key,
)


def test_connector_resource_key_is_stable_and_exact_matchable() -> None:
    assert (
        connector_resource_key("org-1", "support", "connector-7", "run-42")
        == "connector:org-1:support:connector-7:run-42"
    )


def test_parse_connector_resource_key_returns_all_authority_fields() -> None:
    assert parse_connector_resource_key("connector:org-1:support:connector-7:run-42") == (
        ConnectorResource(
            org_id="org-1",
            kb_slug="support",
            connector_id="connector-7",
            generation="run-42",
        )
    )


@pytest.mark.parametrize(
    "resource_key",
    [
        "",
        "artifact:org-1:support:connector-7:run-42",
        "connector:org-1:support:connector-7",
        "connector:org-1:support:connector-7:run-42:extra",
        "connector:org-1::connector-7:run-42",
        "connector:org-1:support:connector-7:",
    ],
)
def test_parse_connector_resource_key_rejects_malformed_keys(resource_key: str) -> None:
    with pytest.raises(ValueError, match="connector resource key"):
        parse_connector_resource_key(resource_key)


def test_connector_resource_key_rejects_delimiter_in_components() -> None:
    with pytest.raises(ValueError, match="generation"):
        connector_resource_key("org-1", "support", "connector-7", "run:42")


def test_connector_writer_queues_are_explicit() -> None:
    assert CONNECTOR_WRITER_QUEUES == (CRAWL_JOBS, ENRICH_BULK, GRAPHITI_BULK)


def _pool_with_rows(rows):
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=rows)
    acquire = MagicMock()
    acquire.__aenter__ = AsyncMock(return_value=conn)
    acquire.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire.return_value = acquire
    return pool, conn


@pytest.mark.asyncio
async def test_live_job_lookup_filters_exact_resource_key_and_3x_live_statuses() -> None:
    pool, conn = _pool_with_rows([{"id": 11}, {"id": 12}])

    assert await list_live_jobs_by_resource_key(pool, "connector:o:k:c:g") == [11, 12]

    sql = conn.fetch.await_args.args[0]
    assert "args->>'resource_key' = $2" in sql
    assert "status IN ('todo', 'doing')" in sql
    assert "aborting" not in sql
    assert "args::text" not in sql


@pytest.mark.asyncio
async def test_cancel_jobs_preserves_rows_and_requests_abort() -> None:
    pool, _ = _pool_with_rows([{"id": 11}])
    proc_app = MagicMock()
    proc_app.job_manager.cancel_job_by_id_async = AsyncMock(return_value=True)

    report = await cancel_jobs_by_resource_key(proc_app, pool, "connector:o:k:c:g")

    assert (report.jobs_found, report.jobs_cancelled, report.jobs_failed_to_cancel) == (1, 1, 0)
    proc_app.job_manager.cancel_job_by_id_async.assert_awaited_once_with(
        11, abort=True, delete_job=False
    )


@pytest.mark.asyncio
async def test_cancel_jobs_counts_false_result_as_failed_to_cancel() -> None:
    pool, _ = _pool_with_rows([{"id": 11}])
    proc_app = MagicMock()
    proc_app.job_manager.cancel_job_by_id_async = AsyncMock(return_value=False)

    with capture_logs() as logs:
        report = await cancel_jobs_by_resource_key(proc_app, pool, "connector:o:k:c:g")

    assert (report.jobs_found, report.jobs_cancelled, report.jobs_failed_to_cancel) == (1, 0, 1)
    event = next(log for log in logs if log["event"] == "connector_resource_jobs_cancel_requested")
    assert event["jobs_found"] == 1
    assert event["jobs_cancelled"] == 0
    assert event["jobs_failed_to_cancel"] == 1


@pytest.mark.asyncio
async def test_resource_job_counts_use_procrastinate_3x_taxonomy() -> None:
    pool, conn = _pool_with_rows(
        [
            {"status": status, "count": 1}
            for status in ("todo", "doing", "cancelled", "aborted", "failed", "succeeded")
        ]
    )

    counts = await get_resource_job_counts(pool, "connector:o:k:c:g")

    assert counts == {
        "todo": 1,
        "doing": 1,
        "cancelled": 1,
        "aborted": 1,
        "failed": 1,
        "succeeded": 1,
        "pending": 1,
        "running": 1,
        "terminal": 4,
        "failed_visible": 1,
    }
    sql = conn.fetch.await_args.args[0]
    assert "args->>'resource_key' = $1" in sql
    assert "aborting" not in sql
