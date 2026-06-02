from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_crawl_taxonomy_backfill_skips_when_kb_has_no_taxonomy_nodes():
    from knowledge_ingest.adapters.crawler import _enqueue_taxonomy_backfill_after_crawl

    with (
        patch("knowledge_ingest.portal_client.fetch_taxonomy_nodes", AsyncMock(return_value=[])),
        patch("knowledge_ingest.enrichment_tasks.get_app") as get_app,
    ):
        result = await _enqueue_taxonomy_backfill_after_crawl(
            org_id="org-1",
            kb_slug="support",
            job_id="crawl-1",
            pages_done=3,
        )

    assert result is None
    get_app.assert_not_called()


@pytest.mark.asyncio
async def test_crawl_taxonomy_backfill_enqueues_deduped_job_when_nodes_exist():
    from knowledge_ingest.adapters.crawler import _enqueue_taxonomy_backfill_after_crawl

    defer_async = AsyncMock(return_value=123)
    configured = SimpleNamespace(defer_async=defer_async)
    run_taxonomy_backfill = MagicMock()
    run_taxonomy_backfill.configure.return_value = configured
    app = SimpleNamespace(run_taxonomy_backfill=run_taxonomy_backfill)

    with (
        patch(
            "knowledge_ingest.portal_client.fetch_taxonomy_nodes",
            AsyncMock(return_value=[SimpleNamespace(id=1, name="Support")]),
        ),
        patch("knowledge_ingest.enrichment_tasks.get_app", return_value=app),
    ):
        result = await _enqueue_taxonomy_backfill_after_crawl(
            org_id="org-1",
            kb_slug="support",
            job_id="crawl-1",
            pages_done=3,
        )

    assert result == 123
    run_taxonomy_backfill.configure.assert_called_once_with(
        queueing_lock="taxonomy-backfill:org-1:support",
    )
    defer_async.assert_awaited_once_with(
        org_id="org-1",
        kb_slug="support",
        batch_size=100,
    )
