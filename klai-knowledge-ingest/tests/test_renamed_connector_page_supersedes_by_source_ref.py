"""A renamed connector page must supersede its previous artifact, not duplicate it.

Contract: for connector-sourced documents the stable identity is
``(source_connector_id, source_ref)`` -- the provider's own document id --
NOT ``path``, which mirrors a user-editable title.

Production incident (Voys ``support`` KB, 2026-08-14): a manual Notion
recall re-ingested 95 pages. Pages whose title was unchanged superseded
their May row correctly; pages that had been RENAMED in Notion did not
match on ``path``, so the May artifact stayed active alongside the August
one. Four pages ended up with two active artifacts and two live Qdrant
chunk sets each, e.g. ``App troubleshoot & transfers`` (May content) next
to ``App troubleshooting`` (August content). Retrieval could cite the
three-month-old copy as current.
"""

from __future__ import annotations

from contextlib import ExitStack, asynccontextmanager, contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest import pg_store
from knowledge_ingest.models import IngestRequest

_SENTINEL = 253402300800

_CONNECTOR_ID = "939b7851-c675-4dad-996f-b1cbce2d81ae"
_SOURCE_REF = "53d643e8-0a98-4c1b-95f0-03b9c018109c"
_OLD_PATH = "App troubleshoot & transfers"
_NEW_PATH = "App troubleshooting"


def _make_procrastinate_app() -> MagicMock:
    """Procrastinate stub whose ``configure_task(...).defer_async(...)`` is awaitable."""
    app = MagicMock()
    app.configure_task.return_value.defer_async = AsyncMock(return_value=None)
    app.ingest_graphiti_episode.configure.return_value.defer_async = AsyncMock(return_value=None)
    return app


@contextmanager
def _ingest_patches(req, closed_rows, delete_document_side_effect=None):
    """Patch ingest_document's collaborators; yield the mocks tests assert on.

    ``closed_rows`` is what ``soft_delete_artifact`` returns — the
    ``(artifact_id, path)`` pairs this ingest supersedes.
    """
    with ExitStack() as stack:

        def _p(target, **kw):
            return stack.enter_context(patch(target, **kw))

        _p(
            "knowledge_ingest.connector_state.connector_is_active",
            new_callable=AsyncMock,
            return_value=True,
        )
        _p("knowledge_ingest.enrichment_tasks.get_app", return_value=_make_procrastinate_app())
        _p(
            "knowledge_ingest.pg_store.get_active_content_hash",
            new_callable=AsyncMock,
            return_value=None,
        )
        soft_delete = _p(
            "knowledge_ingest.pg_store.soft_delete_artifact",
            new_callable=AsyncMock,
            return_value=closed_rows,
        )
        delete_document = _p(
            "knowledge_ingest.qdrant_store.delete_document",
            new_callable=AsyncMock,
            side_effect=delete_document_side_effect,
        )
        _p(
            "knowledge_ingest.pg_store.create_artifact",
            new_callable=AsyncMock,
            return_value="august-artifact-id",
        )
        set_superseded_by = _p(
            "knowledge_ingest.pg_store.set_superseded_by", new_callable=AsyncMock
        )
        _p("knowledge_ingest.pg_store.update_artifact_extra", new_callable=AsyncMock)
        _p(
            "knowledge_ingest.pg_store.set_artifact_ingest_status",
            new_callable=AsyncMock,
            return_value={"artifact_id": "august-artifact-id", "path": req.path},
        )
        _p(
            "knowledge_ingest.pg_store.insert_parent_chunks",
            new_callable=AsyncMock,
            return_value=[1],
        )
        _p("knowledge_ingest.embedder.embed", new_callable=AsyncMock, return_value=[[0.1] * 10])
        _p("knowledge_ingest.qdrant_store.upsert_chunks", new_callable=AsyncMock)
        _p(
            "knowledge_ingest.org_config.is_enrichment_enabled",
            new_callable=AsyncMock,
            return_value=False,
        )
        _p(
            "knowledge_ingest.routes.ingest.kb_config.get_kb_visibility",
            new_callable=AsyncMock,
            return_value="internal",
        )
        settings = _p("knowledge_ingest.routes.ingest.settings")
        settings.chunk_size = 1500
        settings.chunk_overlap = 200
        settings.enrichment_enabled = False

        yield {
            "soft_delete": soft_delete,
            "delete_document": delete_document,
            "set_superseded_by": set_superseded_by,
        }


def _make_conn() -> MagicMock:
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


@pytest.mark.asyncio
async def test_soft_delete_closes_previous_artifact_of_same_source_ref():
    """The close query must match on source identity, not only on path."""
    conn = _make_conn()

    await pg_store.soft_delete_artifact(
        conn,
        "org1",
        "support",
        _NEW_PATH,
        source_connector_id=_CONNECTOR_ID,
        source_ref=_SOURCE_REF,
    )

    sql = conn.fetch.call_args[0][0]
    values = conn.fetch.call_args[0][1:]

    assert "source_ref" in sql, "close query ignores source_ref -- renames orphan the old row"
    assert "source_connector_id" in sql, "source_ref must be scoped to its connector"
    assert _CONNECTOR_ID in values
    assert _SOURCE_REF in values
    # The path branch must survive for manual uploads / non-connector docs.
    assert _NEW_PATH in values


@pytest.mark.asyncio
async def test_soft_delete_without_source_ref_is_unchanged():
    """Non-connector callers (personal KB, manual upload) keep path-only semantics."""
    conn = _make_conn()

    await pg_store.soft_delete_artifact(conn, "org1", "personal", "note.md")

    values = conn.fetch.call_args[0][1:]
    assert "note.md" in values
    assert _SENTINEL in values


@pytest.mark.asyncio
async def test_ingest_forwards_connector_identity_when_page_is_renamed():
    """ingest_document must hand the connector identity to the close step."""
    req = IngestRequest(
        org_id="org1",
        kb_slug="support",
        path=_NEW_PATH,
        content="# App troubleshooting\n" + ("body content " * 40),
        source_type="notion",
        content_type="kb_article",
        source_connector_id=_CONNECTOR_ID,
        source_ref=_SOURCE_REF,
    )
    conn = _make_conn()

    with _ingest_patches(req, closed_rows=[("may-artifact-id", _OLD_PATH)]) as mocks:
        from knowledge_ingest.routes.ingest import ingest_document

        result = await ingest_document(conn, req)

    mock_soft_delete = mocks["soft_delete"]
    mock_delete_doc = mocks["delete_document"]
    mock_set_superseded = mocks["set_superseded_by"]

    assert result["status"] == "ok"
    kwargs = mock_soft_delete.await_args.kwargs
    assert kwargs.get("source_connector_id") == _CONNECTOR_ID, (
        "ingest did not pass the connector id -- the renamed page's old row stays active"
    )
    assert kwargs.get("source_ref") == _SOURCE_REF
    # The row closed under the OLD title must be linked to the new artifact.
    mock_set_superseded.assert_awaited_once_with(conn, ["may-artifact-id"], "august-artifact-id")
    # ...and its chunks must leave Qdrant, or the pre-rename content stays
    # retrievable under an open-ended valid_until.
    mock_delete_doc.assert_awaited_once_with("org1", "support", _OLD_PATH)


def _renamed_request() -> IngestRequest:
    return IngestRequest(
        org_id="org1",
        kb_slug="support",
        path=_NEW_PATH,
        content="# App troubleshooting\n" + ("body content " * 40),
        source_type="notion",
        content_type="kb_article",
        source_connector_id=_CONNECTOR_ID,
        source_ref=_SOURCE_REF,
    )


@pytest.mark.asyncio
async def test_unrenamed_page_does_not_trigger_a_qdrant_delete():
    """Same path in and out: upsert_chunks already clears those points.

    Deleting here would be a redundant round-trip on the hot path — every
    ordinary re-ingest of an unchanged title would pay for it.
    """
    req = _renamed_request()
    conn = _make_conn()

    with _ingest_patches(req, closed_rows=[("previous-artifact-id", _NEW_PATH)]) as mocks:
        from knowledge_ingest.routes.ingest import ingest_document

        result = await ingest_document(conn, req)

    assert result["status"] == "ok"
    mocks["delete_document"].assert_not_awaited()


@pytest.mark.asyncio
async def test_qdrant_cleanup_failure_does_not_fail_the_ingest():
    """A Qdrant blip must not fail an ingest whose supersede already committed.

    Raising would not roll the supersede back, and a retry cannot recover:
    the stale row is closed, so soft_delete_artifact stops returning it and
    the cleanup never runs again. The ingest therefore completes and the
    failure is surfaced at error level instead.
    """
    req = _renamed_request()
    conn = _make_conn()

    with _ingest_patches(
        req,
        closed_rows=[("may-artifact-id", _OLD_PATH)],
        delete_document_side_effect=RuntimeError("qdrant unreachable"),
    ) as mocks:
        from knowledge_ingest.routes.ingest import ingest_document

        result = await ingest_document(conn, req)

    assert result["status"] == "ok", "a cleanup blip must not fail the whole ingest"
    mocks["delete_document"].assert_awaited_once_with("org1", "support", _OLD_PATH)
