"""
Tests for content-hash deduplication in ingest_document().

When the SHA-256 of req.content matches the stored content_hash of the
current active artifact, ingest_document() must return early with
{"status": "skipped", "reason": "content unchanged"} without performing
any chunking, embedding, or Qdrant upserts.

SPEC-TI-003-FOLLOWUP-001: ingest_document now takes asyncpg.Connection
as its first argument. The patches below address the helpers it calls
on that conn -- conn itself is a mock instance.

Issue #403 follow-up: ingest_document creates artifacts as ``pending`` and
only marks them ``synced`` after Qdrant, PG extra, and enrichment enqueue
finish, so retry dedupe only trusts completed rows.
"""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
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

    @asynccontextmanager
    async def _tx():
        yield None

    conn.transaction = MagicMock(side_effect=_tx)
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
        patch(
            "knowledge_ingest.pg_store.soft_delete_artifact",
            new_callable=AsyncMock,
            return_value=["closed-artifact-id"],
        ) as mock_soft_delete,
        patch(
            "knowledge_ingest.pg_store.create_artifact",
            new_callable=AsyncMock,
            return_value="artifact-uuid-1",
        ) as mock_create,
        patch(
            "knowledge_ingest.pg_store.set_superseded_by",
            new_callable=AsyncMock,
        ) as mock_set_superseded_by,
        patch("knowledge_ingest.pg_store.update_artifact_extra", new_callable=AsyncMock),
        patch(
            "knowledge_ingest.pg_store.set_artifact_ingest_status",
            new_callable=AsyncMock,
            return_value={"artifact_id": "artifact-status", "path": req.path},
        ),
        patch(
            "knowledge_ingest.pg_store.insert_parent_chunks",
            new_callable=AsyncMock,
            return_value=[1],
        ),
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
    mock_soft_delete.assert_awaited_once_with(conn, req.org_id, req.kb_slug, req.path)
    mock_create.assert_awaited_once()
    mock_set_superseded_by.assert_awaited_once_with(
        conn,
        ["closed-artifact-id"],
        "artifact-uuid-1",
    )
    # The three steps must run inside one transaction (atomicity contract).
    conn.transaction.assert_called_once()


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
            "knowledge_ingest.pg_store.set_artifact_ingest_status",
            new_callable=AsyncMock,
            return_value={"artifact_id": "artifact-status", "path": req.path},
        ),
        patch(
            "knowledge_ingest.pg_store.insert_parent_chunks",
            new_callable=AsyncMock,
            return_value=[1],
        ),
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
            "knowledge_ingest.pg_store.set_artifact_ingest_status",
            new_callable=AsyncMock,
            return_value={"artifact_id": "artifact-status", "path": req.path},
        ),
        patch(
            "knowledge_ingest.pg_store.insert_parent_chunks",
            new_callable=AsyncMock,
            return_value=[1],
        ),
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
    assert call_kwargs["index_status"] == "pending"


@pytest.mark.asyncio
async def test_qdrant_failure_leaves_artifact_pending_for_retry():
    """A failed Qdrant write must not create a synced content-hash dedupe source."""
    req = _make_request("# Fresh content")
    conn = _make_mock_conn()

    mock_create = AsyncMock(return_value="artifact-qdrant-failed")
    mock_set_status = AsyncMock(
        return_value={"artifact_id": "artifact-qdrant-failed", "path": req.path}
    )

    with (
        patch(
            "knowledge_ingest.pg_store.get_active_content_hash",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("knowledge_ingest.pg_store.soft_delete_artifact", new_callable=AsyncMock),
        patch("knowledge_ingest.pg_store.create_artifact", mock_create),
        patch("knowledge_ingest.pg_store.update_artifact_extra", new_callable=AsyncMock),
        patch("knowledge_ingest.pg_store.set_artifact_ingest_status", mock_set_status),
        patch(
            "knowledge_ingest.pg_store.insert_parent_chunks",
            new_callable=AsyncMock,
            return_value=[1],
        ),
        patch(
            "knowledge_ingest.embedder.embed",
            new_callable=AsyncMock,
            return_value=[[0.1] * 10],
        ),
        patch(
            "knowledge_ingest.qdrant_store.upsert_chunks",
            new_callable=AsyncMock,
            side_effect=RuntimeError("qdrant unavailable"),
        ),
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

        with pytest.raises(RuntimeError, match="qdrant unavailable"):
            await ingest_document(conn, req)

    assert mock_create.call_args.kwargs["index_status"] == "pending"
    mock_set_status.assert_not_called()


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
            "knowledge_ingest.pg_store.set_artifact_ingest_status",
            new_callable=AsyncMock,
            return_value={"artifact_id": "artifact-status", "path": req.path},
        ),
        patch(
            "knowledge_ingest.pg_store.insert_parent_chunks",
            new_callable=AsyncMock,
            return_value=[1, 2],
        ),
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


@pytest.mark.asyncio
async def test_docling_skip_chunking_writes_parent_chunks() -> None:
    """Regression for 2026-05-28: docling-prechunked uploads (skip_chunking
    + chunks) MUST insert parent_chunks rows and thread parent_chunk_ids
    into the Qdrant upsert.

    Before this fix, the skip_chunking branch only set ``texts`` and never
    populated ``parents_serialised`` / ``parent_index_per_child``, so the
    insert_parent_chunks block was skipped. PDF uploads landed chunks in
    Qdrant but parent_chunks stayed empty for the artifact, breaking
    retrieval-api's child→parent lookup and causing chat to hallucinate
    from off-topic chunks (Jantine's "Wie is Frank?" incident).
    """
    req = _make_request("Preview only")
    req.content_hash = "docling-sha256"
    req.skip_chunking = True
    req.chunks = ["frank-paragraph", "verantwoordelijkheden-paragraph", "third"]
    conn = _make_mock_conn()

    mock_create = AsyncMock(return_value="artifact-docling-1")
    # Each parent_chunks INSERT returns its generated id — order-preserved.
    mock_insert_parents = AsyncMock(return_value=[101, 102, 103])

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
            "knowledge_ingest.pg_store.set_artifact_ingest_status",
            new_callable=AsyncMock,
            return_value={"artifact_id": "artifact-status", "path": req.path},
        ),
        patch("knowledge_ingest.pg_store.insert_parent_chunks", mock_insert_parents),
        patch(
            "knowledge_ingest.embedder.embed",
            new_callable=AsyncMock,
            return_value=[[0.1] * 10, [0.2] * 10, [0.3] * 10],
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
            return_value="private",
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

    # Contract 1: insert_parent_chunks IS called for docling uploads.
    # Before this fix, it was silently skipped — that's the bug.
    mock_insert_parents.assert_awaited_once()
    insert_kwargs = mock_insert_parents.call_args.kwargs
    assert insert_kwargs["artifact_id"] == "artifact-docling-1"
    assert insert_kwargs["org_id"] == "org1"
    parents_passed = insert_kwargs["parents"]
    assert len(parents_passed) == 3, "every docling chunk must become its own parent"
    assert [p["text"] for p in parents_passed] == [
        "frank-paragraph",
        "verantwoordelijkheden-paragraph",
        "third",
    ]
    # Each parent gets a sequential position (0-based) so retrieval-api can
    # reconstruct document order if needed.
    assert [p["position"] for p in parents_passed] == [0, 1, 2]

    # Contract 2: parent_chunk_ids flows into the Qdrant upsert so chunks
    # in Qdrant carry a parent_chunk_id field pointing to the new pg rows.
    upsert_kwargs = mock_upsert.call_args.kwargs
    assert upsert_kwargs["parent_chunk_ids"] == [101, 102, 103]
