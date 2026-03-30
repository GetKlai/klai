"""Tests for enrichment task deduplication via Procrastinate queueing_lock.

Verifies that:
- configure(queueing_lock=...) is called with the correct key
- AlreadyEnqueued raised by defer_async is caught and logged (not propagated)
- ingest_document returns ok even when the enrichment task is already queued
"""
from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# Define a fake AlreadyEnqueued before any imports that might trigger procrastinate
class _AlreadyEnqueued(Exception):
    pass


@pytest.fixture(autouse=True)
def _patch_procrastinate_exceptions(monkeypatch):
    """Inject fake AlreadyEnqueued into sys.modules so the lazy import in ingest.py works."""
    fake_exc = types.SimpleNamespace(AlreadyEnqueued=_AlreadyEnqueued)
    monkeypatch.setitem(sys.modules, "procrastinate.exceptions", fake_exc)


def _make_mock_app(side_effect=None):
    """Return a mock Procrastinate app whose enrich_document_bulk task can be configured."""
    configured = MagicMock()
    configured.defer_async = AsyncMock(side_effect=side_effect)
    task_fn = MagicMock()
    task_fn.configure = MagicMock(return_value=configured)

    mock_app = MagicMock()
    mock_app.enrich_document_bulk = task_fn
    return mock_app, task_fn, configured


def _base_patches(mock_app):
    """Return a context manager stack with all ingest_document dependencies mocked."""
    import contextlib

    @contextlib.asynccontextmanager
    async def _stack():
        with (
            patch(
                "knowledge_ingest.routes.ingest.chunker.chunk_markdown",
                return_value=[MagicMock(text="chunk text")],
            ),
            patch(
                "knowledge_ingest.routes.ingest.embedder.embed",
                new_callable=AsyncMock,
                return_value=[[0.1] * 10],
            ),
            patch(
                "knowledge_ingest.routes.ingest.pg_store.get_active_content_hash",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "knowledge_ingest.routes.ingest.pg_store.soft_delete_artifact",
                new_callable=AsyncMock,
            ),
            patch(
                "knowledge_ingest.routes.ingest.pg_store.create_artifact",
                new_callable=AsyncMock,
                return_value="art-test",
            ),
            patch(
                "knowledge_ingest.routes.ingest.qdrant_store.upsert_chunks",
                new_callable=AsyncMock,
            ),
            patch("knowledge_ingest.routes.ingest.get_pool", new_callable=AsyncMock),
            patch(
                "knowledge_ingest.routes.ingest.org_config.is_enrichment_enabled",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "knowledge_ingest.routes.ingest.kb_config.get_kb_visibility",
                new_callable=AsyncMock,
                return_value="internal",
            ),
            patch.dict(
                sys.modules,
                {"knowledge_ingest.enrichment_tasks": types.SimpleNamespace(get_app=lambda: mock_app)},
            ),
        ):
            yield

    return _stack()


@pytest.mark.asyncio
async def test_queueing_lock_uses_org_kb_path():
    """configure() is called with queueing_lock = '{org_id}:{kb_slug}:{path}'."""
    from knowledge_ingest.models import IngestRequest
    from knowledge_ingest.routes.ingest import ingest_document

    mock_app, task_fn, _ = _make_mock_app()
    req = IngestRequest(
        org_id="org-1",
        kb_slug="my-kb",
        path="docs/page.md",
        content="# Title\n\nContent.",
        source_type="docs",
        content_type="kb_article",
    )

    async with _base_patches(mock_app):
        result = await ingest_document(req)

    assert result["status"] == "ok"
    task_fn.configure.assert_called_once_with(queueing_lock="org-1:my-kb:docs/page.md")


@pytest.mark.asyncio
async def test_already_enqueued_does_not_propagate():
    """When defer_async raises AlreadyEnqueued, ingest_document still returns ok."""
    from knowledge_ingest.models import IngestRequest
    from knowledge_ingest.routes.ingest import ingest_document

    mock_app, task_fn, _ = _make_mock_app(side_effect=_AlreadyEnqueued())
    req = IngestRequest(
        org_id="org-1",
        kb_slug="my-kb",
        path="docs/page.md",
        content="# Title\n\nContent.",
        source_type="docs",
        content_type="kb_article",
    )

    async with _base_patches(mock_app):
        result = await ingest_document(req)

    assert result["status"] == "ok"
    # configure was still called (lock was set)
    task_fn.configure.assert_called_once()


@pytest.mark.asyncio
async def test_two_ingests_same_path_only_one_enrichment():
    """Second ingest for the same path silently skips enrichment (AlreadyEnqueued)."""
    from knowledge_ingest.models import IngestRequest
    from knowledge_ingest.routes.ingest import ingest_document

    # First call succeeds, second raises AlreadyEnqueued
    configured = MagicMock()
    configured.defer_async = AsyncMock(side_effect=[None, _AlreadyEnqueued()])
    task_fn = MagicMock()
    task_fn.configure = MagicMock(return_value=configured)
    mock_app = MagicMock()
    mock_app.enrich_document_bulk = task_fn

    req = IngestRequest(
        org_id="org-2",
        kb_slug="kb-slug",
        path="notes/doc.md",
        content="# Doc\n\nSome text.",
        source_type="docs",
        content_type="kb_article",
    )

    async with _base_patches(mock_app):
        result1 = await ingest_document(req)
        result2 = await ingest_document(req)

    assert result1["status"] == "ok"
    assert result2["status"] == "ok"
    # configure called twice (once per ingest), but only the first defer_async succeeds
    assert task_fn.configure.call_count == 2
    assert configured.defer_async.call_count == 2
