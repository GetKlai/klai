"""A saved Docs page that cannot be ingested is waited for, or recorded, never dropped.

Since ingest fails instead of storing chunks dense-only, an embedding outage
reaches the Docs paths as an exception. Before, the debounced task had no
retry and the webhook and the bulk sync only logged it, so the page stayed out
of search until its next edit with no trace. Now the task waits the outage out
like enrichment, and every page given up on logs ``docs_page_ingest_failed``
(alerted) and fails its request or job.
"""

from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog.testing
from fastapi import HTTPException

from knowledge_ingest.embedder import (
    EMBEDDING_OUTAGE_WINDOW_SECONDS,
    EMBEDDING_RETRY_SECONDS,
    EmbeddingUnavailable,
)
from tests.test_ingest_debounce import (  # noqa: F401 (fixtures)
    PUSH_PAYLOAD,
    WEBHOOK_SECRET,
    _sign,
    mock_pool,
    webhook_client,
)

PAGE = {"org_id": "org1", "kb_slug": "kb", "path": "docs/page.md", "gitea_repo": "org-x/kb"}


def _failures(logs: list[dict]) -> list[tuple]:
    return [
        (e["log_level"], e["path"], e["reason"])
        for e in logs
        if e["event"] == "docs_page_ingest_failed"
    ]


async def _run_task(ingest: AsyncMock, **job_args):
    """Run ingest_from_gitea once; return its recorded re-queue."""
    from knowledge_ingest.ingest_tasks import register_ingest_tasks

    tasks: dict = {}

    class _App:
        def task(self, **_kwargs):
            def _decorator(fn):
                fn.configure = MagicMock(return_value=SimpleNamespace(defer_async=AsyncMock()))
                tasks[fn.__name__] = fn
                return fn

            return _decorator

    register_ingest_tasks(_App())

    @asynccontextmanager
    async def _conn(_org_id):
        yield MagicMock()

    task = tasks["ingest_from_gitea"]
    context = SimpleNamespace(job=SimpleNamespace(queueing_lock="gitea:org1:kb:docs/page.md"))
    with (
        patch("knowledge_ingest.routes.ingest._fetch_gitea_file", AsyncMock(return_value="# T")),
        patch("knowledge_ingest.routes.ingest.ingest_document", ingest),
        patch("knowledge_ingest.db.tenant_scoped_connection", _conn),
    ):
        await task(context, **PAGE, user_id=None, **job_args)
    return task.configure


async def test_an_outage_queues_the_page_again_with_the_enrichment_bound():
    with structlog.testing.capture_logs() as logs:
        configure = await _run_task(AsyncMock(side_effect=EmbeddingUnavailable("down")))

    assert configure.call_args.kwargs == {
        "queueing_lock": "gitea:org1:kb:docs/page.md",
        "schedule_in": {"seconds": EMBEDDING_RETRY_SECONDS},
    }
    deferred = configure.return_value.defer_async.call_args.kwargs
    assert abs(deferred["embedding_deferred_since"] - time.time()) < 5
    assert deferred["path"] == "docs/page.md"
    assert _failures(logs) == []


async def test_an_outage_past_the_window_records_the_page_failed():
    since = int(time.time()) - EMBEDDING_OUTAGE_WINDOW_SECONDS

    with structlog.testing.capture_logs() as logs, pytest.raises(EmbeddingUnavailable):
        await _run_task(
            AsyncMock(side_effect=EmbeddingUnavailable("down")), embedding_deferred_since=since
        )

    assert _failures(logs) == [("error", "docs/page.md", "embedding_unavailable")]


async def test_any_other_failure_records_the_page_failed_and_fails_the_job():
    with structlog.testing.capture_logs() as logs, pytest.raises(RuntimeError):
        await _run_task(AsyncMock(side_effect=RuntimeError("db gone")))

    assert _failures(logs) == [("error", "docs/page.md", "RuntimeError")]


async def test_bulk_sync_queues_outage_pages_and_reports_failed_ones():
    from knowledge_ingest.models import BulkSyncRequest
    from knowledge_ingest.routes.ingest import bulk_sync_kb_route

    outcome = {
        "a.md": None,
        "b.md": EmbeddingUnavailable("down"),
        "c.md": RuntimeError("bad page"),
    }

    async def _ingest(_conn, req):
        if outcome[req.path]:
            raise outcome[req.path]
        return {"status": "ok"}

    queue = AsyncMock()
    req = BulkSyncRequest(org_id="org1", kb_slug="kb", gitea_repo="org-x/kb")
    with (
        patch("knowledge_ingest.routes.ingest._verify_internal_secret"),
        patch(
            "knowledge_ingest.routes.ingest._list_gitea_md_files",
            AsyncMock(return_value=list(outcome)),
        ),
        patch("knowledge_ingest.routes.ingest._fetch_gitea_file", AsyncMock(return_value="# T")),
        patch("knowledge_ingest.routes.ingest.ingest_document", _ingest),
        patch("knowledge_ingest.routes.ingest._queue_gitea_ingest", queue),
        structlog.testing.capture_logs() as logs,
        pytest.raises(HTTPException) as raised,
    ):
        await bulk_sync_kb_route(MagicMock(), req)

    assert raised.value.status_code == 502
    assert raised.value.detail == {
        "pages": 1,
        "deferred": ["b.md"],
        "failed": [{"path": "c.md", "reason": "RuntimeError"}],
    }
    queue.assert_awaited_once_with("org1", "kb", "b.md", "org-x/kb")
    assert _failures(logs) == [("error", "c.md", "RuntimeError")]


def test_a_webhook_page_that_cannot_be_queued_fails_the_delivery(webhook_client):  # noqa: F811
    client, _ = webhook_client
    body = json.dumps(PUSH_PAYLOAD).encode()
    proc_app = MagicMock()
    proc_app.ingest_from_gitea.configure.return_value.defer_async = AsyncMock(
        side_effect=ConnectionError("queue down")
    )

    with (
        patch(
            "knowledge_ingest.routes.ingest._get_org_id",
            new_callable=AsyncMock,
            return_value="org1",
        ),
        patch("knowledge_ingest.enrichment_tasks.get_app", return_value=proc_app),
        structlog.testing.capture_logs() as logs,
    ):
        resp = client.post(
            "/ingest/v1/webhook/gitea",
            content=body,
            headers={
                "content-type": "application/json",
                "x-gitea-signature": _sign(body, WEBHOOK_SECRET),
            },
        )

    assert resp.status_code == 502
    assert _failures(logs) == [("error", "docs/page.md", "queue_unavailable")]
