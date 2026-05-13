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


def _make_mock_conn() -> MagicMock:
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchval = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value=None)
    return conn


def _base_patches(mock_app):
    """Return a context manager stack with all ingest_document dependencies mocked.

    SPEC-TI-003-FOLLOWUP-001: ingest_document now takes conn explicitly,
    so we no longer patch knowledge_ingest.routes.ingest.get_pool (it does
    not exist after the refactor).
    """
    import contextlib

    @contextlib.asynccontextmanager
    async def _stack():
        with (
            patch(
                "knowledge_ingest.routes.ingest.chunker.chunk_markdown_with_parents",
                return_value=([MagicMock(text="chunk text", parent_index=0)], []),
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
                "knowledge_ingest.routes.ingest.pg_store.update_artifact_extra",
                new_callable=AsyncMock,
            ),
            patch(
                "knowledge_ingest.routes.ingest.qdrant_store.upsert_chunks",
                new_callable=AsyncMock,
            ),
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
            patch(
                "knowledge_ingest.portal_client.fetch_taxonomy_nodes",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "knowledge_ingest.content_labeler.generate_content_label",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "knowledge_ingest.enrichment_tasks.get_app",
                return_value=mock_app,
            ),
            patch("knowledge_ingest.routes.ingest.settings") as mock_settings,
        ):
            # Disable the graphiti enqueue branch — these tests focus on
            # the enrichment-defer contract, not the FalkorDB pipeline.
            mock_settings.graphiti_enabled = False
            mock_settings.chunk_size = 1500
            mock_settings.chunk_overlap = 200
            mock_settings.enrichment_enabled = True
            mock_settings.enrichment_max_chunks = 200
            mock_settings.taxonomy_centroid_match_threshold = 0.85
            yield

    return _stack()


@pytest.mark.asyncio
async def test_queueing_lock_uses_org_kb_path():
    """configure() is called with queueing_lock = '{org_id}:{kb_slug}:{path}'."""
    from knowledge_ingest.models import IngestRequest
    from knowledge_ingest.routes.ingest import ingest_document

    mock_app, task_fn, _ = _make_mock_app()
    conn = _make_mock_conn()
    req = IngestRequest(
        org_id="org-1",
        kb_slug="my-kb",
        path="docs/page.md",
        content="# Title\n\nContent.",
        source_type="docs",
        content_type="kb_article",
    )

    async with _base_patches(mock_app):
        result = await ingest_document(conn, req)

    assert result["status"] == "ok"
    task_fn.configure.assert_called_once_with(queueing_lock="org-1:my-kb:docs/page.md")


@pytest.mark.asyncio
async def test_already_enqueued_does_not_propagate():
    """When defer_async raises AlreadyEnqueued, ingest_document still returns ok."""
    from knowledge_ingest.models import IngestRequest
    from knowledge_ingest.routes.ingest import ingest_document

    mock_app, task_fn, _ = _make_mock_app(side_effect=_AlreadyEnqueued())
    conn = _make_mock_conn()
    req = IngestRequest(
        org_id="org-1",
        kb_slug="my-kb",
        path="docs/page.md",
        content="# Title\n\nContent.",
        source_type="docs",
        content_type="kb_article",
    )

    async with _base_patches(mock_app):
        result = await ingest_document(conn, req)

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

    conn = _make_mock_conn()
    req = IngestRequest(
        org_id="org-2",
        kb_slug="kb-slug",
        path="notes/doc.md",
        content="# Doc\n\nSome text.",
        source_type="docs",
        content_type="kb_article",
    )

    async with _base_patches(mock_app):
        result1 = await ingest_document(conn, req)
        result2 = await ingest_document(conn, req)

    assert result1["status"] == "ok"
    assert result2["status"] == "ok"
    # configure called twice (once per ingest), but only the first defer_async succeeds
    assert task_fn.configure.call_count == 2
    assert configured.defer_async.call_count == 2


@pytest.mark.asyncio
async def test_truncated_docling_document_skips_enrichment_enqueue():
    """A bounded preview for a pre-chunked Docling file must not start LLM fan-out."""
    from knowledge_ingest.models import IngestRequest
    from knowledge_ingest.routes.ingest import ingest_document

    mock_app, task_fn, _ = _make_mock_app()
    conn = _make_mock_conn()
    req = IngestRequest(
        org_id="org-3",
        kb_slug="chemie",
        path="file:sha256:source",
        content="Preview only",
        source_type="file",
        content_type="document",
        skip_chunking=True,
        chunks=["docling chunk one", "docling chunk two"],
        extra={
            "document_text_truncated": True,
            "docling_chunk_count": 2,
        },
    )

    async with _base_patches(mock_app):
        result = await ingest_document(conn, req)

    assert result["status"] == "ok"
    task_fn.configure.assert_not_called()
