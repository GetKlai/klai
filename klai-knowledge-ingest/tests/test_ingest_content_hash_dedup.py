"""
Tests for content-hash deduplication in ingest_document().

When the SHA-256 of req.content matches the stored content_hash of the
current active artifact, ingest_document() must return early with
{"status": "skipped", "reason": "content unchanged"} without performing
any chunking, embedding, or Qdrant upserts.

SPEC-TI-003-FOLLOWUP-001: ingest_document now takes asyncpg.Connection
as its first argument. The patches below address the helpers it calls
on that conn -- conn itself is a mock instance.

SPEC-INGEST-CONTENT-PG-001: ingest_document persists extra_payload via
pg_store.update_artifact_extra before defer; the enrichment task takes
only artifact_id. The new ``update_artifact_extra`` mock keeps these
tests aligned with the merged contract.
"""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest.models import IngestRequest


def _make_request(content: str = "# Hello\nWorld") -> IngestRequest:
    return IngestRequest(
        org_id="org1",
        kb_slug="my-kb",
        path="docs/page.md",
        content=content,
        source_type="docs",
        content_type="kb_article",
    )


def _make_mock_conn() -> MagicMock:
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchval = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value=None)
    return conn


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.mark.asyncio
async def test_skips_when_content_unchanged():
    """ingest_document returns 'skipped' without calling embed when hash matches."""
    req = _make_request()
    stored_hash = _sha256(req.content)
    conn = _make_mock_conn()

    with (
        patch(
            "knowledge_ingest.pg_store.get_active_content_hash",
            new_callable=AsyncMock,
            return_value=stored_hash,
        ),
        patch("knowledge_ingest.embedder.embed", new_callable=AsyncMock) as mock_embed,
    ):
        from knowledge_ingest.routes.ingest import ingest_document

        result = await ingest_document(conn, req)

    assert result["status"] == "skipped"
    assert result["reason"] == "content unchanged"
    mock_embed.assert_not_called()


@pytest.mark.asyncio
async def test_proceeds_when_content_changed():
    """ingest_document runs the full pipeline when content hash differs."""
    req = _make_request("# New content\nDifferent text")
    old_hash = _sha256("# Old content\nOriginal text")
    conn = _make_mock_conn()

    with (
        patch(
            "knowledge_ingest.pg_store.get_active_content_hash",
            new_callable=AsyncMock,
            return_value=old_hash,  # different from current content
        ),
        patch("knowledge_ingest.pg_store.soft_delete_artifact", new_callable=AsyncMock),
        patch(
            "knowledge_ingest.pg_store.create_artifact",
            new_callable=AsyncMock,
            return_value="artifact-uuid-1",
        ),
        patch("knowledge_ingest.pg_store.update_artifact_extra", new_callable=AsyncMock),
        patch(
            "knowledge_ingest.embedder.embed",
            new_callable=AsyncMock,
            return_value=[[0.1] * 10],
        ),
        patch("knowledge_ingest.qdrant_store.upsert_chunks", new_callable=AsyncMock),
        patch(
            "knowledge_ingest.org_config.is_enrichment_enabled",
            new_callable=AsyncMock,
            return_value=False,
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
        patch("knowledge_ingest.routes.ingest.settings") as mock_settings,
    ):
        mock_settings.graphiti_enabled = False
        mock_settings.chunk_size = 1500
        mock_settings.chunk_overlap = 200
        mock_settings.enrichment_enabled = False

        from knowledge_ingest.routes.ingest import ingest_document

        result = await ingest_document(conn, req)

    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_proceeds_when_no_previous_artifact():
    """ingest_document runs the full pipeline when there is no stored hash (first ingest)."""
    req = _make_request()
    conn = _make_mock_conn()

    with (
        patch(
            "knowledge_ingest.pg_store.get_active_content_hash",
            new_callable=AsyncMock,
            return_value=None,  # no previous artifact
        ),
        patch("knowledge_ingest.pg_store.soft_delete_artifact", new_callable=AsyncMock),
        patch(
            "knowledge_ingest.pg_store.create_artifact",
            new_callable=AsyncMock,
            return_value="artifact-uuid-2",
        ),
        patch("knowledge_ingest.pg_store.update_artifact_extra", new_callable=AsyncMock),
        patch(
            "knowledge_ingest.embedder.embed",
            new_callable=AsyncMock,
            return_value=[[0.1] * 10],
        ),
        patch("knowledge_ingest.qdrant_store.upsert_chunks", new_callable=AsyncMock),
        patch(
            "knowledge_ingest.org_config.is_enrichment_enabled",
            new_callable=AsyncMock,
            return_value=False,
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
        patch("knowledge_ingest.routes.ingest.settings") as mock_settings,
    ):
        mock_settings.graphiti_enabled = False
        mock_settings.chunk_size = 1500
        mock_settings.chunk_overlap = 200
        mock_settings.enrichment_enabled = False

        from knowledge_ingest.routes.ingest import ingest_document

        result = await ingest_document(conn, req)

    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_content_hash_stored_on_create():
    """create_artifact is called with the correct content_hash on new ingest."""
    req = _make_request("# Fresh content")
    expected_hash = _sha256(req.content)
    conn = _make_mock_conn()

    mock_create = AsyncMock(return_value="artifact-uuid-3")

    with (
        patch(
            "knowledge_ingest.pg_store.get_active_content_hash",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("knowledge_ingest.pg_store.soft_delete_artifact", new_callable=AsyncMock),
        patch("knowledge_ingest.pg_store.create_artifact", mock_create),
        patch("knowledge_ingest.pg_store.update_artifact_extra", new_callable=AsyncMock),
        patch(
            "knowledge_ingest.embedder.embed",
            new_callable=AsyncMock,
            return_value=[[0.1] * 10],
        ),
        patch("knowledge_ingest.qdrant_store.upsert_chunks", new_callable=AsyncMock),
        patch(
            "knowledge_ingest.org_config.is_enrichment_enabled",
            new_callable=AsyncMock,
            return_value=False,
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
        patch("knowledge_ingest.routes.ingest.settings") as mock_settings,
    ):
        mock_settings.graphiti_enabled = False
        mock_settings.chunk_size = 1500
        mock_settings.chunk_overlap = 200
        mock_settings.enrichment_enabled = False

        from knowledge_ingest.routes.ingest import ingest_document

        await ingest_document(conn, req)

    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["content_hash"] == expected_hash


@pytest.mark.asyncio
async def test_content_hash_override_used_for_prechunked_ingest():
    """Pre-chunked large documents can dedupe on full-source hash."""
    req = _make_request("Preview only")
    req.content_hash = "source-sha256"
    req.skip_chunking = True
    req.chunks = ["chunk one", "chunk two"]
    conn = _make_mock_conn()

    mock_create = AsyncMock(return_value="artifact-uuid-4")

    with (
        patch(
            "knowledge_ingest.pg_store.get_active_content_hash",
            new_callable=AsyncMock,
            return_value=None,
        ) as mock_get_hash,
        patch("knowledge_ingest.pg_store.soft_delete_artifact", new_callable=AsyncMock),
        patch("knowledge_ingest.pg_store.create_artifact", mock_create),
        patch("knowledge_ingest.pg_store.update_artifact_extra", new_callable=AsyncMock),
        patch(
            "knowledge_ingest.embedder.embed",
            new_callable=AsyncMock,
            return_value=[[0.1] * 10, [0.2] * 10],
        ),
        patch("knowledge_ingest.qdrant_store.upsert_chunks", new_callable=AsyncMock) as mock_upsert,
        patch(
            "knowledge_ingest.org_config.is_enrichment_enabled",
            new_callable=AsyncMock,
            return_value=False,
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
        patch("knowledge_ingest.routes.ingest.settings") as mock_settings,
    ):
        mock_settings.graphiti_enabled = False
        mock_settings.chunk_size = 1500
        mock_settings.chunk_overlap = 200
        mock_settings.enrichment_enabled = False

        from knowledge_ingest.routes.ingest import ingest_document

        await ingest_document(conn, req)

    mock_get_hash.assert_called_once_with(conn, req.org_id, req.kb_slug, req.path)
    assert mock_create.call_args.kwargs["content_hash"] == "source-sha256"
    assert mock_upsert.call_args.kwargs["chunks"] == ["chunk one", "chunk two"]
