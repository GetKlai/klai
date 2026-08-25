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
from klai_kb_slugs import episode_name

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
def _ingest_patches(
    req,
    closed_rows,
    delete_document_side_effect=None,
    episode_ids=None,
    rename_side_effect=None,
):
    """Patch ingest_document's collaborators; yield the mocks tests assert on.

    ``closed_rows`` is what ``soft_delete_artifact`` returns — the
    ``(artifact_id, path)`` pairs this ingest supersedes. ``episode_ids`` is
    what those closed rows recorded in ``extra.graphiti_episode_id``.
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
            "knowledge_ingest.pg_store.get_active_artifact_state",
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

        get_episode_ids = _p(
            "knowledge_ingest.pg_store.get_episode_ids_for_document_history",
            new_callable=AsyncMock,
            return_value=list(episode_ids or []),
        )
        rename_episodes = _p(
            "knowledge_ingest.graph.rename_episodes_to_document_keys",
            new_callable=AsyncMock,
            side_effect=rename_side_effect,
            return_value=len(episode_ids or []),
        )

        yield {
            "soft_delete": soft_delete,
            "delete_document": delete_document,
            "set_superseded_by": set_superseded_by,
            "get_episode_ids": get_episode_ids,
            "rename_episodes": rename_episodes,
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


@pytest.mark.asyncio
async def test_renamed_page_repoints_its_graph_episodes_at_the_new_document_key():
    """Facts extracted before a rename must keep citing their document.

    SPEC-RAG-GRAPH-CITE-002 names episodes ``doc:<kb_slug>:<path>`` so they
    survive re-ingest. A rename breaks that: the old artifact's episodes keep
    the OLD path, and the block above deletes exactly the chunks that name
    resolved against. Every fact extracted before the rename then falls back
    to rendering as a truncated sentence — the failure the doc-key naming was
    introduced to remove, reappearing for the one case where the document
    identity moved.
    """
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

    with _ingest_patches(
        req,
        closed_rows=[("may-artifact-id", _OLD_PATH)],
        episode_ids=["may-episode-uuid"],
    ) as mocks:
        from knowledge_ingest.routes.ingest import ingest_document

        result = await ingest_document(conn, req)

    assert result["status"] == "ok"

    mocks["get_episode_ids"].assert_awaited_once()
    episode_args = mocks["get_episode_ids"].await_args
    assert "may-artifact-id" in episode_args[0][2], (
        "the closed row under the old path was not looked up for its episode"
    )

    mocks["rename_episodes"].assert_awaited_once()
    org_arg, renames = mocks["rename_episodes"].await_args[0]
    assert org_arg == "org1"
    assert renames == {episode_name("support", _NEW_PATH): ["may-episode-uuid"]}, (
        "pre-rename episodes still carry the old path -- their citations stay unresolvable"
    )


@pytest.mark.asyncio
async def test_unrenamed_reingest_does_not_touch_the_graph():
    """A plain re-ingest supersedes under the SAME path and must rename nothing.

    The episode already carries the right name, so a rename here would be
    write traffic to FalkorDB bought with nothing.
    """
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

    with _ingest_patches(
        req,
        closed_rows=[("july-artifact-id", _NEW_PATH)],
        episode_ids=["july-episode-uuid"],
    ) as mocks:
        from knowledge_ingest.routes.ingest import ingest_document

        result = await ingest_document(conn, req)

    assert result["status"] == "ok"
    mocks["rename_episodes"].assert_not_awaited()
    mocks["get_episode_ids"].assert_not_awaited()


@pytest.mark.asyncio
async def test_episode_rename_failure_does_not_fail_the_ingest():
    """A stranded episode costs a citation, not correctness.

    The supersede is already committed by this point, so raising would fail
    the ingest without undoing it — and the retry cannot recover, because the
    old row is closed and ``soft_delete_artifact`` stops returning it. Same
    reasoning as the stale-chunk cleanup directly above it.
    """
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

    with _ingest_patches(
        req,
        closed_rows=[("may-artifact-id", _OLD_PATH)],
        episode_ids=["may-episode-uuid"],
        rename_side_effect=RuntimeError("falkordb unreachable"),
    ) as mocks:
        from knowledge_ingest.routes.ingest import ingest_document

        result = await ingest_document(conn, req)

    assert result["status"] == "ok", "a FalkorDB hiccup must not fail a committed ingest"
    mocks["rename_episodes"].assert_awaited_once()


@pytest.mark.asyncio
async def test_episode_lookup_walks_the_whole_predecessor_chain():
    """Versions older than the one just closed hold episodes on the same dead path.

    ``soft_delete_artifact`` closes ACTIVE rows only, so a document ingested
    more than once before its rename hands over the newest version alone. Its
    predecessors carry episodes named after the very path this ingest is about
    to delete from Qdrant; stopping at the direct predecessor strands them
    exactly as before the fix.
    """
    conn = _make_conn()

    await pg_store.get_episode_ids_for_document_history(conn, "org1", ["may-artifact-id"])

    sql = conn.fetch.call_args[0][0]
    assert "RECURSIVE" in sql, "only the rows handed in are looked up -- older versions strand"
    assert "superseded_by" in sql
    assert "depth < 100" in sql, "an unbounded walk hangs the ingest request on a cycle"
    assert "'no-chunks'" in sql, "the skip sentinel is not an episode uuid"


@pytest.mark.asyncio
async def test_episode_lookup_is_a_noop_without_artifact_ids():
    """No closed rows means no query -- an empty ANY() would scan the tenant."""
    conn = _make_conn()

    assert await pg_store.get_episode_ids_for_document_history(conn, "org1", []) == []
    conn.fetch.assert_not_awaited()
