"""
Tests for content-hash deduplication in ingest_document().

When the SHA-256 of req.content matches the stored content_hash of the
current active artifact, ingest_document() must return early with
{"status": "skipped", "reason": "content unchanged"} without performing
any chunking, embedding, or Qdrant upserts.
"""
from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest.models import IngestRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(content: str = "# Hello\nWorld") -> IngestRequest:
    return IngestRequest(
        org_id="org1",
        kb_slug="my-kb",
        path="docs/page.md",
        content=content,
        source_type="docs",
        content_type="kb_article",
    )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_skips_when_content_unchanged():
    """ingest_document returns 'skipped' without calling embed when hash matches."""
    req = _make_request()
    stored_hash = _sha256(req.content)

    with patch(
        "knowledge_ingest.pg_store.get_active_content_hash",
        new_callable=AsyncMock,
        return_value=stored_hash,
    ), patch(
        "knowledge_ingest.embedder.embed",
        new_callable=AsyncMock,
    ) as mock_embed:
        from knowledge_ingest.routes.ingest import ingest_document
        result = await ingest_document(req)

    assert result["status"] == "skipped"
    assert result["reason"] == "content unchanged"
    mock_embed.assert_not_called()


@pytest.mark.asyncio
async def test_proceeds_when_content_changed():
    """ingest_document runs the full pipeline when content hash differs."""
    req = _make_request("# New content\nDifferent text")
    old_hash = _sha256("# Old content\nOriginal text")

    mock_pool = MagicMock()
    mock_pool.execute = AsyncMock(return_value=None)
    mock_pool.fetchval = AsyncMock(return_value=None)
    mock_pool.fetchrow = AsyncMock(return_value=None)

    with patch(
        "knowledge_ingest.pg_store.get_active_content_hash",
        new_callable=AsyncMock,
        return_value=old_hash,  # different from current content
    ), patch(
        "knowledge_ingest.pg_store.soft_delete_artifact",
        new_callable=AsyncMock,
    ), patch(
        "knowledge_ingest.pg_store.create_artifact",
        new_callable=AsyncMock,
        return_value="artifact-uuid-1",
    ), patch(
        "knowledge_ingest.embedder.embed",
        new_callable=AsyncMock,
        return_value=[[0.1] * 10],
    ), patch(
        "knowledge_ingest.qdrant_store.upsert_chunks",
        new_callable=AsyncMock,
    ), patch(
        "knowledge_ingest.org_config.is_enrichment_enabled",
        new_callable=AsyncMock,
        return_value=False,
    ), patch(
        "knowledge_ingest.routes.ingest.kb_config.get_kb_visibility",
        new_callable=AsyncMock,
        return_value="internal",
    ), patch(
        "knowledge_ingest.routes.ingest.get_pool",
        new_callable=AsyncMock,
        return_value=mock_pool,
    ), patch(
        "knowledge_ingest.routes.ingest.settings"
    ) as mock_settings:
        mock_settings.graphiti_enabled = False
        mock_settings.chunk_size = 1500
        mock_settings.chunk_overlap = 200
        mock_settings.enrichment_enabled = False

        from knowledge_ingest.routes.ingest import ingest_document
        result = await ingest_document(req)

    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_proceeds_when_no_previous_artifact():
    """ingest_document runs the full pipeline when there is no stored hash (first ingest)."""
    req = _make_request()

    mock_pool = MagicMock()
    mock_pool.execute = AsyncMock(return_value=None)
    mock_pool.fetchval = AsyncMock(return_value=None)
    mock_pool.fetchrow = AsyncMock(return_value=None)

    with patch(
        "knowledge_ingest.pg_store.get_active_content_hash",
        new_callable=AsyncMock,
        return_value=None,  # no previous artifact
    ), patch(
        "knowledge_ingest.pg_store.soft_delete_artifact",
        new_callable=AsyncMock,
    ), patch(
        "knowledge_ingest.pg_store.create_artifact",
        new_callable=AsyncMock,
        return_value="artifact-uuid-2",
    ), patch(
        "knowledge_ingest.embedder.embed",
        new_callable=AsyncMock,
        return_value=[[0.1] * 10],
    ), patch(
        "knowledge_ingest.qdrant_store.upsert_chunks",
        new_callable=AsyncMock,
    ), patch(
        "knowledge_ingest.org_config.is_enrichment_enabled",
        new_callable=AsyncMock,
        return_value=False,
    ), patch(
        "knowledge_ingest.routes.ingest.kb_config.get_kb_visibility",
        new_callable=AsyncMock,
        return_value="internal",
    ), patch(
        "knowledge_ingest.routes.ingest.get_pool",
        new_callable=AsyncMock,
        return_value=mock_pool,
    ), patch(
        "knowledge_ingest.routes.ingest.settings"
    ) as mock_settings:
        mock_settings.graphiti_enabled = False
        mock_settings.chunk_size = 1500
        mock_settings.chunk_overlap = 200
        mock_settings.enrichment_enabled = False

        from knowledge_ingest.routes.ingest import ingest_document
        result = await ingest_document(req)

    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_content_hash_stored_on_create():
    """create_artifact is called with the correct content_hash on new ingest."""
    req = _make_request("# Fresh content")
    expected_hash = _sha256(req.content)

    mock_pool = MagicMock()
    mock_pool.execute = AsyncMock(return_value=None)
    mock_pool.fetchval = AsyncMock(return_value=None)
    mock_pool.fetchrow = AsyncMock(return_value=None)

    mock_create = AsyncMock(return_value="artifact-uuid-3")

    with patch(
        "knowledge_ingest.pg_store.get_active_content_hash",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "knowledge_ingest.pg_store.soft_delete_artifact",
        new_callable=AsyncMock,
    ), patch(
        "knowledge_ingest.pg_store.create_artifact",
        mock_create,
    ), patch(
        "knowledge_ingest.embedder.embed",
        new_callable=AsyncMock,
        return_value=[[0.1] * 10],
    ), patch(
        "knowledge_ingest.qdrant_store.upsert_chunks",
        new_callable=AsyncMock,
    ), patch(
        "knowledge_ingest.org_config.is_enrichment_enabled",
        new_callable=AsyncMock,
        return_value=False,
    ), patch(
        "knowledge_ingest.routes.ingest.kb_config.get_kb_visibility",
        new_callable=AsyncMock,
        return_value="internal",
    ), patch(
        "knowledge_ingest.routes.ingest.get_pool",
        new_callable=AsyncMock,
        return_value=mock_pool,
    ), patch(
        "knowledge_ingest.routes.ingest.settings"
    ) as mock_settings:
        mock_settings.graphiti_enabled = False
        mock_settings.chunk_size = 1500
        mock_settings.chunk_overlap = 200
        mock_settings.enrichment_enabled = False

        from knowledge_ingest.routes.ingest import ingest_document
        await ingest_document(req)

    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["content_hash"] == expected_hash
