"""A document deleted, trashed or unshared at its source leaves the knowledge store.

Each adapter runs for real through ``SyncEngine.run_sync``; only the network
boundary (HTTP transport or the SDK's request method) is faked, so the
adapter's own pagination, trash filtering and completeness decision are what
is under test. The delete call itself is knowledge-ingest's
``DELETE /ingest/v1/connector/document`` and is asserted on the client mock.

All identifiers and URLs are synthetic.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from notion_client import Client
from notion_sync.client import RateLimitedNotionClient
from sqlalchemy.dialects import postgresql

from app.adapters import confluence as confluence_module
from app.adapters import google_drive as drive_module
from app.adapters.airtable import AirtableAdapter
from app.adapters.base import DocumentRef
from app.adapters.confluence import ConfluenceAdapter
from app.adapters.github import GitHubAdapter
from app.adapters.google_drive import GoogleDriveAdapter
from app.adapters.json_feed import JsonFeedAdapter
from app.adapters.ms_docs import MsDocsAdapter
from app.adapters.notion import NotionAdapter
from app.clients.knowledge_ingest import KnowledgeIngestClient
from app.core.enums import SyncStatus
from app.services.portal_client import PortalConnectorConfig
from app.services.sync_engine import SyncEngine

CONNECTOR_ID = uuid.UUID("0f0f0f0f-1111-2222-3333-444444444444")
ORG_ID = "org-synthetic"
KB_SLUG = "kb-synthetic"
EDITED = "2026-01-01T00:00:00.000Z"


# -- Engine harness -------------------------------------------------------------


def _portal_config(connector_type: str, config: dict[str, Any]) -> PortalConnectorConfig:
    return PortalConnectorConfig(
        connector_id=str(CONNECTOR_ID),
        kb_id=7,
        kb_slug=KB_SLUG,
        zitadel_org_id=ORG_ID,
        connector_type=connector_type,
        config=config,
        schedule=None,
        is_enabled=True,
    )


def _run() -> MagicMock:
    run = MagicMock()
    run.cursor_state = None
    run.error_details = None
    run.quality_status = None
    run.skip_reasons = {}
    return run


def _previous(known: list[str], **cursor: Any) -> MagicMock:
    """The previous successful run; unless a test says otherwise the store holds the same refs."""
    previous = MagicMock()
    previous.cursor_state = {"synced_refs": known, **cursor}
    return previous


async def _sync(
    adapter: Any,
    portal_config: PortalConnectorConfig,
    previous: MagicMock | None,
    *,
    stored: set[str] | dict[str, set[str]] | Exception | None = None,
    delete_error: Exception | None = None,
) -> tuple[MagicMock, MagicMock]:
    """Run one sync and return (sync_run, ingest_client).

    ``stored`` is what knowledge-ingest holds for the connector (ref -> stored
    paths); it defaults to the previous run's synced_refs. A plain set of refs
    gives each ref a path of its own that no listing yields.
    """
    run = _run()

    @asynccontextmanager
    async def session_maker() -> AsyncIterator[Any]:
        session = AsyncMock()
        session.get = AsyncMock(return_value=run)
        yield session

    portal_client = MagicMock()
    portal_client.get_connector_config = AsyncMock(return_value=portal_config)
    portal_client.report_sync_status = AsyncMock()
    registry = MagicMock()
    registry.get = MagicMock(return_value=adapter)
    ingest_client = MagicMock()
    ingest_client.ingest_document = AsyncMock(return_value=True)
    ingest_client.delete_connector_document = AsyncMock(return_value=True, side_effect=delete_error)
    if stored is None:
        stored = set(previous.cursor_state["synced_refs"]) if previous else set()
    if isinstance(stored, set):
        stored = {ref: {f"stored-path:{ref}"} for ref in stored}
    if isinstance(stored, Exception):
        ingest_client.list_connector_source_refs = AsyncMock(side_effect=stored)
    else:
        ingest_client.list_connector_source_refs = AsyncMock(return_value=stored)
    engine = SyncEngine(
        session_maker=session_maker,
        registry=registry,
        ingest_client=ingest_client,
        portal_client=portal_client,
        settings=MagicMock(),
        image_store=None,
        crawl_sync_client=MagicMock(),
    )
    engine._get_last_successful_run = AsyncMock(return_value=previous)  # type: ignore[method-assign]
    engine._get_last_pending_run = AsyncMock(return_value=None)  # type: ignore[method-assign]
    await engine.run_sync(CONNECTOR_ID, uuid.uuid4())
    return run, ingest_client


def _deleted(ingest_client: MagicMock) -> list[str]:
    return [call.kwargs["source_ref"] for call in ingest_client.delete_connector_document.await_args_list]


def _assert_single_scoped_delete(ingest_client: MagicMock, source_ref: str) -> None:
    ingest_client.delete_connector_document.assert_awaited_once_with(
        org_id=ORG_ID,
        kb_slug=KB_SLUG,
        source_connector_id=str(CONNECTOR_ID),
        source_ref=source_ref,
    )


@pytest.fixture(autouse=True)
def _long_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    parsed = MagicMock()
    parsed.text = "Synthetic document body long enough to pass the short-document gate. " * 2
    parsed.images = []
    monkeypatch.setattr("app.services.sync_engine.parse_document_with_images", lambda _c, _f: parsed)


def _json(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content) if request.content else {}


# -- Shared guards (engine) -------------------------------------------------------


def _fake_adapter(refs: list[DocumentRef], *, complete: bool = True) -> MagicMock:
    adapter = MagicMock()
    adapter.confirm_gone = AsyncMock(side_effect=lambda _connector, missing: set(missing))
    adapter.get_cursor_state = AsyncMock(return_value={})
    adapter.list_documents = AsyncMock(return_value=refs)
    adapter.listing_is_complete = MagicMock(return_value=complete)
    adapter.fetch_document = AsyncMock(return_value=b"body")
    adapter.get_sync_metrics = MagicMock(return_value={})
    adapter.post_sync = AsyncMock()
    return adapter


def _doc(ref: str) -> DocumentRef:
    return DocumentRef(path=ref, ref=ref, size=1, content_type="text/plain", source_ref=ref)


@pytest.mark.asyncio
async def test_listing_that_raised_deletes_nothing() -> None:
    adapter = _fake_adapter([])
    adapter.list_documents = AsyncMock(side_effect=RuntimeError("pagination failed on page 3"))

    run, ingest = await _sync(adapter, _portal_config("notion", {}), _previous(["ref-a", "ref-b"]))

    assert run.status == SyncStatus.FAILED
    ingest.delete_connector_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_listing_while_the_store_holds_documents_is_refused(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An expired token and an emptied source look the same; nothing is deleted."""
    caplog.set_level("INFO")

    run, ingest = await _sync(_fake_adapter([]), _portal_config("notion", {}), _previous(["ref-a", "ref-b"]))

    assert run.status == SyncStatus.COMPLETED
    ingest.delete_connector_document.assert_not_awaited()
    assert run.error_details == [
        {"warning": "stale_cleanup_refused_empty", "stale_cleanup_refused": True, "stored_ref_count": 2}
    ]
    refused = next(r for r in caplog.records if getattr(r, "event", None) == "stale_cleanup_refused_empty")
    assert refused.stored_ref_count == 2


@pytest.mark.asyncio
async def test_incomplete_listing_deletes_nothing_and_a_later_complete_one_does() -> None:
    first, ingest = await _sync(
        _fake_adapter([_doc("ref-a")], complete=False), _portal_config("notion", {}), _previous(["ref-a", "ref-b"])
    )
    ingest.delete_connector_document.assert_not_awaited()
    ingest.list_connector_source_refs.assert_not_awaited()

    _second, ingest = await _sync(
        _fake_adapter([_doc("ref-a")]),
        _portal_config("notion", {}),
        _previous(first.cursor_state["synced_refs"]),
        stored={"ref-a", "ref-b"},
    )
    _assert_single_scoped_delete(ingest, "ref-b")


@pytest.mark.asyncio
async def test_stale_complete_flag_from_an_earlier_run_never_deletes() -> None:
    """A listing path that records nothing must not inherit True from the run before."""

    class _Adapter(NotionAdapter):
        record = True

        async def list_documents(self, connector: Any, cursor_context: Any = None) -> list[DocumentRef]:
            if self.record:
                self._record_listing(connector, complete=True)
            return [_doc("ref-a")]

        async def get_cursor_state(self, connector: Any) -> dict[str, Any]:
            return {}

        async def fetch_document(self, ref: DocumentRef, connector: Any) -> bytes:
            return b"body"

        async def confirm_gone(self, connector: Any, source_refs: list[str]) -> set[str]:
            return set(source_refs)

    adapter = _Adapter(MagicMock())
    _first, ingest = await _sync(adapter, _portal_config("notion", {}), _previous(["ref-a", "ref-b"]))
    _assert_single_scoped_delete(ingest, "ref-b")

    adapter.record = False
    _second, ingest = await _sync(
        adapter, _portal_config("notion", {}), _previous(["ref-a"]), stored={"ref-a", "ref-c"}
    )

    ingest.delete_connector_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_document_the_store_holds_but_no_run_remembered_is_removed() -> None:
    """The diff is against the store, so a document deleted before any run noticed still goes."""
    _run_, ingest = await _sync(
        _fake_adapter([_doc("ref-a")]), _portal_config("notion", {}), _previous(["ref-a"]), stored={"ref-a", "ref-old"}
    )

    _assert_single_scoped_delete(ingest, "ref-old")
    ingest.list_connector_source_refs.assert_awaited_once_with(
        org_id=ORG_ID, kb_slug=KB_SLUG, source_connector_id=str(CONNECTOR_ID)
    )


@pytest.mark.asyncio
async def test_store_read_failure_deletes_nothing() -> None:
    run, ingest = await _sync(
        _fake_adapter([_doc("ref-a")]),
        _portal_config("notion", {}),
        _previous(["ref-a", "ref-b"]),
        stored=RuntimeError("knowledge-ingest unavailable"),
    )

    assert run.status == SyncStatus.COMPLETED
    ingest.delete_connector_document.assert_not_awaited()
    assert run.error_details[0]["warning"] == "stale_cleanup_store_unavailable"


@pytest.mark.asyncio
async def test_refs_the_adapter_does_not_confirm_as_gone_are_kept() -> None:
    adapter = _fake_adapter([_doc("ref-a")])
    adapter.confirm_gone = AsyncMock(return_value={"ref-c"})

    _run_, ingest = await _sync(adapter, _portal_config("notion", {}), _previous(["ref-a", "ref-b", "ref-c"]))

    _assert_single_scoped_delete(ingest, "ref-c")
    adapter.confirm_gone.assert_awaited_once()
    assert adapter.confirm_gone.await_args.args[1] == ["ref-b", "ref-c"]


@pytest.mark.asyncio
async def test_failed_document_holding_back_deletions_is_reported(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")
    adapter = _fake_adapter([_doc("ref-a"), _doc("ref-c")])
    adapter.fetch_document = AsyncMock(side_effect=[RuntimeError("parse failed"), b"body"])

    run, ingest = await _sync(adapter, _portal_config("notion", {}), _previous(["ref-a", "ref-b"]))

    ingest.delete_connector_document.assert_not_awaited()
    assert {"warning": "stale_cleanup_held_back", "reason": "documents_failed", "documents_failed": 1} in (
        run.error_details
    )
    counts = next(r for r in caplog.records if getattr(r, "event", None) == "source_deletions")
    assert (counts.held_back, counts.documents_failed) == ("documents_failed", 1)


@pytest.mark.asyncio
async def test_incomplete_listing_reports_the_cap_that_held_deletions_back(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if _json(request).get("start_cursor"):
            return httpx.Response(200, json={"results": [_notion_page("page-3")], "has_more": False})
        return httpx.Response(200, json={"results": [_notion_page("page-1")], "has_more": True, "next_cursor": "c2"})

    adapter = _notion_adapter(monkeypatch, handler)

    run, _ingest = await _sync(adapter, _notion_config(max_pages=1), _previous(["page-1", "page-9"]))

    held = next(e for e in run.error_details if e.get("warning") == "stale_cleanup_held_back")
    assert held["reason"] == "listing_incomplete:max_pages_reached"


@pytest.mark.asyncio
async def test_empty_listing_refusal_keeps_the_previous_synced_refs() -> None:
    """Otherwise every document counts as new and is fetched again once the token works."""
    run, _ingest = await _sync(_fake_adapter([]), _portal_config("notion", {}), _previous(["ref-a", "ref-b"]))

    assert run.cursor_state["synced_refs"] == ["ref-a", "ref-b"]


@pytest.mark.asyncio
async def test_unchanged_commit_skip_carries_synced_refs_forward() -> None:
    adapter = _fake_adapter([])
    adapter.get_cursor_state = AsyncMock(return_value={"tree_sha": "c1", "path_filter": None})

    run, _ingest = await _sync(
        adapter, _portal_config("github", {}), _previous(["ref-a"], tree_sha="c1", path_filter=None)
    )

    adapter.list_documents.assert_not_awaited()
    assert run.cursor_state == {"tree_sha": "c1", "path_filter": None, "synced_refs": ["ref-a"]}


@pytest.mark.asyncio
async def test_counts_are_logged_per_run(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")

    await _sync(
        _fake_adapter([_doc("ref-a"), _doc("ref-c")]), _portal_config("notion", {}), _previous(["ref-a", "ref-b"])
    )

    counts = next(r for r in caplog.records if getattr(r, "event", None) == "source_deletions")
    assert (counts.listing_complete, counts.listed, counts.known, counts.removed, counts.kept) == (True, 2, 2, 1, 1)


@pytest.mark.asyncio
async def test_baseline_is_read_for_this_connector_only() -> None:
    """The known set comes from this connector's own last successful run."""
    other_connector = uuid.UUID("99999999-8888-7777-6666-555555555555")
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock())

    await SyncEngine._get_last_successful_run(session, other_connector)

    statement = session.execute.await_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    assert "sync_runs.connector_id = %(connector_id_1)s" in str(compiled)
    assert compiled.params["connector_id_1"] == other_connector


@pytest.mark.asyncio
async def test_changed_path_filter_is_synced_even_without_a_new_commit() -> None:
    adapter = _fake_adapter([_doc("ref-a")])
    adapter.get_cursor_state = AsyncMock(return_value={"tree_sha": "c1", "path_filter": "docs/*"})

    _run_, ingest = await _sync(
        adapter, _portal_config("github", {}), _previous(["ref-a", "ref-b"], tree_sha="c1", path_filter=None)
    )

    _assert_single_scoped_delete(ingest, "ref-b")


# -- Notion (real notion-client, transport faked) ---------------------------------


DB_A = "aaaaaaaa-0000-4000-8000-000000000001"
DB_B = "bbbbbbbb-0000-4000-8000-000000000002"


def _notion_page(page_id: str, *, database_id: str = DB_A, **flags: Any) -> dict[str, Any]:
    return {
        "object": "page",
        "id": page_id,
        "last_edited_time": EDITED,
        "properties": {},
        "parent": {"type": "data_source_id", "data_source_id": f"ds-{database_id}", "database_id": database_id},
        "in_trash": False,
        "archived": False,
        **flags,
    }


def _notion_adapter(monkeypatch: pytest.MonkeyPatch, handler: Any) -> NotionAdapter:
    monkeypatch.setattr(RateLimitedNotionClient, "_wait_for_rate_limit", lambda _self: None)
    monkeypatch.setattr("notion_sync.client.time.sleep", lambda _s: None)

    def build(token: str) -> RateLimitedNotionClient:
        return RateLimitedNotionClient(Client(auth=token, client=httpx.Client(transport=httpx.MockTransport(handler))))

    monkeypatch.setattr(NotionAdapter, "_build_sync_client", staticmethod(build))
    adapter = NotionAdapter(MagicMock())
    adapter.fetch_document = AsyncMock(return_value=b"body")  # type: ignore[method-assign]
    return adapter


def _notion_error(status: int, code: str) -> httpx.Response:
    return httpx.Response(status, json={"object": "error", "status": status, "code": code, "message": "x"})


def _search(pages: list[dict[str, Any]], retrieve: dict[str, Any] | None = None) -> Any:
    """Serve search with *pages*; a direct retrieve answers from *retrieve*, else
    from the search pages, else 404 object_not_found."""
    by_id = {page["id"]: page for page in pages} | (retrieve or {})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/search":
            return httpx.Response(200, json={"results": pages, "has_more": False, "next_cursor": None})
        if request.url.path.startswith("/v1/databases/"):
            return httpx.Response(200, json={"object": "database"})
        found = by_id.get(request.url.path.rsplit("/", 1)[-1])
        if found is None:
            return _notion_error(404, "object_not_found")
        if isinstance(found, httpx.Response):
            return found
        return httpx.Response(200, json=found)

    return handler


def _notion_config(**extra: Any) -> PortalConnectorConfig:
    return _portal_config("notion", {"access_token": "secret_synthetic", **extra})


@pytest.mark.asyncio
async def test_notion_page_deleted_at_source_is_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _notion_adapter(monkeypatch, _search([_notion_page("page-live")]))

    run, ingest = await _sync(adapter, _notion_config(), _previous(["page-live", "page-gone"]))

    assert run.status == SyncStatus.COMPLETED
    _assert_single_scoped_delete(ingest, "page-gone")


@pytest.mark.parametrize(
    "trash_flags",
    [
        {"in_trash": True, "archived": True},  # Notion-Version 2025-09-03, the notion-client 3.1.0 default
        {"in_trash": True, "archived": False},
        {"in_trash": True},  # later API versions drop ``archived``
    ],
)
@pytest.mark.asyncio
async def test_notion_trashed_page_is_removed(monkeypatch: pytest.MonkeyPatch, trash_flags: dict[str, Any]) -> None:
    trashed = _notion_page("page-trashed")
    trashed.pop("archived")
    trashed.update(trash_flags)
    adapter = _notion_adapter(monkeypatch, _search([_notion_page("page-live"), trashed]))

    _run_, ingest = await _sync(adapter, _notion_config(), _previous(["page-live", "page-trashed"]))

    _assert_single_scoped_delete(ingest, "page-trashed")


@pytest.mark.asyncio
async def test_notion_page_missing_from_search_but_still_in_scope_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    """Search lags; a page that still exists and still belongs must survive its absence."""
    lagging = _notion_page("page-lagging")
    adapter = _notion_adapter(monkeypatch, _search([_notion_page("page-live")], {"page-lagging": lagging}))

    _run_, ingest = await _sync(
        adapter, _notion_config(database_ids=[DB_A]), _previous(["page-live", "page-lagging"])
    )

    ingest.delete_connector_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_notion_page_missing_from_search_and_now_trashed_is_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    trashed = _notion_page("page-x", in_trash=True)
    adapter = _notion_adapter(monkeypatch, _search([_notion_page("page-live")], {"page-x": trashed}))

    _run_, ingest = await _sync(adapter, _notion_config(), _previous(["page-live", "page-x"]))

    _assert_single_scoped_delete(ingest, "page-x")


@pytest.mark.asyncio
async def test_notion_page_missing_from_search_and_outside_the_filter_is_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    moved = _notion_page("page-x", database_id=DB_B)
    adapter = _notion_adapter(monkeypatch, _search([_notion_page("page-live")], {"page-x": moved}))

    _run_, ingest = await _sync(adapter, _notion_config(database_ids=[DB_A]), _previous(["page-live", "page-x"]))

    _assert_single_scoped_delete(ingest, "page-x")


@pytest.mark.parametrize("status,code", [(500, "internal_server_error"), (429, "rate_limited")])
@pytest.mark.asyncio
async def test_notion_confirmation_that_fails_keeps_the_page(
    monkeypatch: pytest.MonkeyPatch, status: int, code: str
) -> None:
    flaky = {"page-x": _notion_error(status, code)}
    adapter = _notion_adapter(monkeypatch, _search([_notion_page("page-live")], flaky))

    _run_, ingest = await _sync(adapter, _notion_config(), _previous(["page-live", "page-x", "page-gone"]))

    _assert_single_scoped_delete(ingest, "page-gone")


@pytest.mark.asyncio
async def test_notion_listing_cut_by_max_pages_deletes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if _json(request).get("start_cursor"):
            return httpx.Response(200, json={"results": [_notion_page("page-3")], "has_more": False})
        pages = [_notion_page("page-1"), _notion_page("page-2")]
        return httpx.Response(200, json={"results": pages, "has_more": True, "next_cursor": "c2"})

    adapter = _notion_adapter(monkeypatch, handler)

    run, ingest = await _sync(adapter, _notion_config(max_pages=2), _previous(["page-1", "page-2", "page-9"]))

    assert run.status == SyncStatus.COMPLETED
    ingest.delete_connector_document.assert_not_awaited()


@pytest.mark.parametrize("status,code", [(500, "internal_server_error"), (429, "rate_limited")])
@pytest.mark.asyncio
async def test_notion_search_failing_mid_pagination_deletes_nothing(
    monkeypatch: pytest.MonkeyPatch, status: int, code: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if _json(request).get("start_cursor"):
            return httpx.Response(status, json={"object": "error", "status": status, "code": code, "message": "x"})
        return httpx.Response(200, json={"results": [_notion_page("page-1")], "has_more": True, "next_cursor": "c2"})

    adapter = _notion_adapter(monkeypatch, handler)

    run, ingest = await _sync(adapter, _notion_config(), _previous(["page-1", "page-2"]))

    assert run.status == SyncStatus.FAILED
    ingest.delete_connector_document.assert_not_awaited()


def _pinned_handler(errors: dict[str, tuple[int, str]]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/search":
            return httpx.Response(200, json={"results": [], "has_more": False, "next_cursor": None})
        page_id = request.url.path.rsplit("/", 1)[-1]
        if page_id in errors:
            status, code = errors[page_id]
            return httpx.Response(status, json={"object": "error", "status": status, "code": code, "message": "x"})
        return httpx.Response(200, json=_notion_page(page_id))

    return handler


@pytest.mark.asyncio
async def test_notion_pinned_page_no_longer_found_is_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _notion_adapter(monkeypatch, _pinned_handler({"page-gone": (404, "object_not_found")}))

    _run_, ingest = await _sync(
        adapter, _notion_config(page_ids=["page-live", "page-gone"]), _previous(["page-live", "page-gone"])
    )

    _assert_single_scoped_delete(ingest, "page-gone")


@pytest.mark.asyncio
async def test_notion_pinned_page_with_a_transient_error_fails_the_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _notion_adapter(monkeypatch, _pinned_handler({"page-flaky": (500, "internal_server_error")}))

    run, ingest = await _sync(
        adapter, _notion_config(page_ids=["page-live", "page-flaky"]), _previous(["page-live", "page-flaky"])
    )

    assert run.status == SyncStatus.FAILED
    ingest.delete_connector_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_notion_database_filter_removes_pages_outside_it_and_keeps_pages_inside(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = [_notion_page("page-in", database_id=DB_A), _notion_page("page-out", database_id=DB_B)]
    adapter = _notion_adapter(monkeypatch, _search(pages))

    _run_, ingest = await _sync(adapter, _notion_config(database_ids=[DB_A]), _previous(["page-in", "page-out"]))

    _assert_single_scoped_delete(ingest, "page-out")


# -- GitHub (real gidgethub over httpx, transport faked) ----------------------------


def _github_adapter(tree: list[dict[str, Any]], *, truncated: bool = False) -> GitHubAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/access_tokens"):
            return httpx.Response(201, json={"token": "ghs_synthetic"})
        if "/git/trees/" in path:
            return httpx.Response(200, json={"sha": "t1", "tree": tree, "truncated": truncated})
        if "/git/refs/heads/" in path:
            return httpx.Response(200, json={"object": {"sha": "c2"}})
        return httpx.Response(404, json={"message": "Not Found"})

    settings = MagicMock()
    adapter = GitHubAdapter(settings)
    adapter._http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter._make_app_jwt = lambda: "jwt"  # type: ignore[method-assign]
    adapter.fetch_document = AsyncMock(return_value=b"body")  # type: ignore[method-assign]
    return adapter


def _blob(path: str) -> dict[str, Any]:
    return {"type": "blob", "path": path, "sha": f"sha-{path}", "size": 10}


def _github_config(**extra: Any) -> PortalConnectorConfig:
    return _portal_config(
        "github", {"installation_id": 1, "repo_owner": "acme", "repo_name": "handbook", "branch": "main", **extra}
    )


def _gh_ref(path: str) -> str:
    return f"acme/handbook:main:{path}"


@pytest.mark.asyncio
async def test_github_file_removed_from_the_repository_is_removed() -> None:
    adapter = _github_adapter([_blob("docs/a.md")])

    _run_, ingest = await _sync(
        adapter, _github_config(), _previous([_gh_ref("docs/a.md"), _gh_ref("docs/b.md")], tree_sha="c1")
    )

    _assert_single_scoped_delete(ingest, _gh_ref("docs/b.md"))


@pytest.mark.asyncio
async def test_github_branch_switch_with_unchanged_content_deletes_nothing() -> None:
    """Unchanged files are skipped at ingest and keep the old branch's ref in the store."""
    adapter = _github_adapter([_blob("docs/a.md"), _blob("docs/b.md")])
    old_ref = "acme/handbook:release:{}"
    stored = {old_ref.format(p): {p} for p in ("docs/a.md", "docs/b.md", "docs/gone.md")}

    _run_, ingest = await _sync(adapter, _github_config(), _previous([]), stored=stored)

    _assert_single_scoped_delete(ingest, old_ref.format("docs/gone.md"))


@pytest.mark.asyncio
async def test_github_failed_delete_is_retried_on_the_same_commit() -> None:
    """With no new commit the unchanged-commit skip must not leave a failed delete undone."""
    gone = _gh_ref("docs/b.md")
    stored = {_gh_ref("docs/a.md"): {"docs/a.md"}, gone: {"docs/b.md"}}

    first, ingest = await _sync(
        _github_adapter([_blob("docs/a.md")]),
        _github_config(),
        _previous([_gh_ref("docs/a.md"), gone], tree_sha="c1"),
        stored=stored,
        delete_error=RuntimeError("knowledge-ingest unavailable"),
    )
    assert first.status == SyncStatus.COMPLETED
    assert first.cursor_state["deletions_open"] is True
    previous = MagicMock()
    previous.cursor_state = first.cursor_state  # same commit c2, same filter

    second, ingest = await _sync(_github_adapter([_blob("docs/a.md")]), _github_config(), previous, stored=stored)

    _assert_single_scoped_delete(ingest, gone)
    assert second.cursor_state["deletions_open"] is False


@pytest.mark.asyncio
async def test_github_same_commit_without_open_deletions_still_skips() -> None:
    adapter = _github_adapter([_blob("docs/a.md")])
    adapter.list_documents = AsyncMock()  # type: ignore[method-assign]
    previous = _previous([_gh_ref("docs/a.md")], tree_sha="c2", path_filter=None, deletions_open=False)

    run, ingest = await _sync(adapter, _github_config(), previous)

    adapter.list_documents.assert_not_awaited()
    ingest.list_connector_source_refs.assert_not_awaited()
    assert run.status == SyncStatus.COMPLETED


@pytest.mark.asyncio
async def test_github_same_commit_with_a_held_document_lists_again() -> None:
    # A held document is accepted only when it is offered again; without a new
    # commit the same-tree shortcut would return before it is.
    adapter = _github_adapter([_blob("docs/a.md")])
    adapter.list_documents = AsyncMock(return_value=[])  # type: ignore[method-assign]
    previous = _previous([_gh_ref("docs/a.md")], tree_sha="c2", path_filter=None, held_open=True)

    await _sync(adapter, _github_config(), previous)

    adapter.list_documents.assert_awaited()


@pytest.mark.asyncio
async def test_github_truncated_tree_deletes_nothing() -> None:
    adapter = _github_adapter([_blob("docs/a.md")], truncated=True)

    _run_, ingest = await _sync(
        adapter, _github_config(), _previous([_gh_ref("docs/a.md"), _gh_ref("docs/b.md")], tree_sha="c1")
    )

    ingest.delete_connector_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_github_path_filter_removes_files_outside_it_and_keeps_files_inside() -> None:
    adapter = _github_adapter([_blob("docs/a.md"), _blob("notes/b.md")])

    _run_, ingest = await _sync(
        adapter,
        _github_config(path_filter="docs/*"),
        _previous([_gh_ref("docs/a.md"), _gh_ref("notes/b.md")], tree_sha="c1"),
    )

    _assert_single_scoped_delete(ingest, _gh_ref("notes/b.md"))


# -- Confluence (adapter pagination, SDK client faked at .get) --------------------


class _FakeConfluence:
    """Serves v2 /spaces and /pages and honours the ``status`` filter like the API does."""

    def __init__(self, spaces: dict[str, str], pages: dict[str, list[Any]]) -> None:
        self._spaces = spaces
        self._pages = pages

    def get_endpoint(self, name: str) -> str:
        return f"api/v2/{name}"

    def get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        if endpoint.endswith("spaces"):
            keys = params.get("keys") or list(self._spaces)
            return {"results": [{"key": k, "id": self._spaces[k]} for k in keys if k in self._spaces], "_links": {}}
        pages = self._pages.get(params["space-id"], [])
        statuses = set(str(params.get("status", "current,archived,trashed,draft")).split(","))
        listed = [p for p in pages if not isinstance(p, dict) or p["status"] in statuses]
        start = int(params.get("cursor", 0))
        end = start + int(params["limit"])
        links = {"next": f"/wiki/api/v2/pages?cursor={end}&limit={params['limit']}"} if end < len(listed) else {}
        return {"results": listed[start:end], "_links": links}


def _confluence_adapter(monkeypatch: pytest.MonkeyPatch, fake: _FakeConfluence) -> ConfluenceAdapter:
    monkeypatch.setattr(confluence_module, "validate_confluence_base_url_strict", lambda *_a, **_k: None)
    monkeypatch.setattr(confluence_module, "_build_confluence_client", lambda *_a: fake)
    adapter = ConfluenceAdapter(MagicMock())
    adapter.fetch_document = AsyncMock(return_value=b"body")  # type: ignore[method-assign]
    return adapter


BASE = "https://wiki.example.com"


def _cf_page(page_id: str, status: str = "current") -> dict[str, Any]:
    return {"id": page_id, "status": status, "version": {"createdAt": EDITED}}


def _cf_ref(space: str, page_id: str) -> str:
    return f"{BASE}/wiki/spaces/{space}/pages/{page_id}"


def _confluence_config(**extra: Any) -> PortalConnectorConfig:
    return _portal_config(
        "confluence", {"base_url": BASE, "email": "sync@example.com", "api_token": "synthetic", **extra}
    )


@pytest.mark.asyncio
async def test_confluence_deleted_page_is_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _confluence_adapter(monkeypatch, _FakeConfluence({"ENG": "1"}, {"1": [_cf_page("10")]}))

    _run_, ingest = await _sync(
        adapter, _confluence_config(space_keys=["ENG"]), _previous([_cf_ref("ENG", "10"), _cf_ref("ENG", "11")])
    )

    _assert_single_scoped_delete(ingest, _cf_ref("ENG", "11"))


@pytest.mark.parametrize("status", ["trashed", "archived"])
@pytest.mark.asyncio
async def test_confluence_trashed_or_archived_page_is_removed(monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    pages = {"1": [_cf_page("10"), _cf_page("11", status=status)]}
    adapter = _confluence_adapter(monkeypatch, _FakeConfluence({"ENG": "1"}, pages))

    _run_, ingest = await _sync(
        adapter, _confluence_config(space_keys=["ENG"]), _previous([_cf_ref("ENG", "10"), _cf_ref("ENG", "11")])
    )

    _assert_single_scoped_delete(ingest, _cf_ref("ENG", "11"))


@pytest.mark.asyncio
async def test_confluence_space_at_the_page_cap_deletes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = {"1": [_cf_page(str(i)) for i in range(confluence_module._MAX_PAGES_PER_SPACE + 1)]}
    adapter = _confluence_adapter(monkeypatch, _FakeConfluence({"ENG": "1"}, pages))

    _run_, ingest = await _sync(
        adapter, _confluence_config(space_keys=["ENG"]), _previous([_cf_ref("ENG", "0"), _cf_ref("ENG", "gone")])
    )

    ingest.delete_connector_document.assert_not_awaited()


@pytest.mark.parametrize("space_keys", [["ENG", "OPS"], []], ids=["configured", "all-spaces"])
@pytest.mark.asyncio
async def test_confluence_space_the_listing_no_longer_returns_keeps_its_pages(
    monkeypatch: pytest.MonkeyPatch, space_keys: list[str]
) -> None:
    """A lost permission and a deleted space look the same; its pages stay, the rest is cleaned."""
    adapter = _confluence_adapter(monkeypatch, _FakeConfluence({"ENG": "1"}, {"1": [_cf_page("10")]}))

    run, ingest = await _sync(
        adapter,
        _confluence_config(space_keys=space_keys),
        _previous([_cf_ref("ENG", "10"), _cf_ref("ENG", "11"), _cf_ref("OPS", "20")]),
    )

    _assert_single_scoped_delete(ingest, _cf_ref("ENG", "11"))
    assert {"warning": "stale_cleanup_kept_unconfirmed", "count": 1} in run.error_details


@pytest.mark.asyncio
async def test_confluence_listing_with_a_skipped_item_is_not_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _confluence_adapter(monkeypatch, _FakeConfluence({"ENG": "1"}, {"1": [_cf_page("10"), "not-an-object"]}))

    run, ingest = await _sync(
        adapter, _confluence_config(space_keys=["ENG"]), _previous([_cf_ref("ENG", "10"), _cf_ref("ENG", "11")])
    )

    ingest.delete_connector_document.assert_not_awaited()
    held = next(e for e in run.error_details if e.get("warning") == "stale_cleanup_held_back")
    assert held["reason"] == "listing_incomplete:page_listing_incomplete:ENG"


@pytest.mark.asyncio
async def test_confluence_space_removed_from_config_removes_its_pages_and_keeps_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeConfluence({"ENG": "1", "OPS": "2"}, {"1": [_cf_page("10")], "2": [_cf_page("20")]})
    adapter = _confluence_adapter(monkeypatch, fake)

    _run_, ingest = await _sync(
        adapter, _confluence_config(space_keys=["ENG"]), _previous([_cf_ref("ENG", "10"), _cf_ref("OPS", "20")])
    )

    _assert_single_scoped_delete(ingest, _cf_ref("OPS", "20"))


# -- Airtable (real pyairtable pagination, Api.request faked) ---------------------

# pyairtable's Table.iterate follows every ``offset`` and lets request errors
# propagate; that is what makes an Airtable listing complete. Verified in the
# installed source (pyairtable/api/api.py::iterate_requests) for this version.
VERIFIED_PYAIRTABLE = "3.5.0.post1"


def test_pyairtable_version_is_the_one_verified_for_complete_listings() -> None:
    assert version("pyairtable") == VERIFIED_PYAIRTABLE, (
        f"pyairtable {version('pyairtable')} is installed; re-read iterate_requests for offset handling and "
        "error propagation, re-run tests/test_source_deletions.py, then update VERIFIED_PYAIRTABLE."
    )


def _airtable(monkeypatch: pytest.MonkeyPatch, pages: list[Any]) -> AirtableAdapter:
    responses = iter(pages)

    def request(_self: Any, **_kwargs: Any) -> Any:
        page = next(responses)
        if isinstance(page, Exception):
            raise page
        return page

    monkeypatch.setattr("pyairtable.api.api.Api.request", request)
    adapter = AirtableAdapter(MagicMock())
    adapter.fetch_document = AsyncMock(return_value=b"body")  # type: ignore[method-assign]
    return adapter


def _record(record_id: str) -> dict[str, Any]:
    return {"id": record_id, "createdTime": EDITED, "fields": {"Name": record_id}}


def _airtable_config() -> PortalConnectorConfig:
    return _portal_config("airtable", {"api_key": "pat_synthetic", "base_id": "appSynthetic", "table_names": ["T"]})


@pytest.mark.asyncio
async def test_airtable_record_deleted_at_source_is_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _airtable(monkeypatch, [{"records": [_record("rec1")], "offset": "o1"}, {"records": [_record("rec2")]}])

    _run_, ingest = await _sync(
        adapter, _airtable_config(), _previous(["appSynthetic/T/rec1", "appSynthetic/T/rec2", "appSynthetic/T/rec3"])
    )

    _assert_single_scoped_delete(ingest, "appSynthetic/T/rec3")


@pytest.mark.asyncio
async def test_airtable_page_failing_mid_listing_deletes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _airtable(monkeypatch, [{"records": [_record("rec1")], "offset": "o1"}, RuntimeError("422 offset")])

    run, ingest = await _sync(adapter, _airtable_config(), _previous(["appSynthetic/T/rec1", "appSynthetic/T/rec2"]))

    assert run.status == SyncStatus.FAILED
    ingest.delete_connector_document.assert_not_awaited()


# -- Google Drive (adapter over httpx, transport faked) ---------------------------


def _drive(
    monkeypatch: pytest.MonkeyPatch, files: dict[str, dict[str, Any] | int], *, incomplete_search: bool = False
) -> GoogleDriveAdapter:
    """``files`` maps id -> file object, or id -> HTTP status for files.get."""
    doc_mime = "application/vnd.google-apps.document"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/startPageToken"):
            return httpx.Response(200, json={"startPageToken": "p1"})
        if path.endswith("/drive/v3/files"):
            query = request.url.params["q"]
            listed = [
                f
                for f in files.values()
                if isinstance(f, dict) and not ("trashed = false" in query and f.get("trashed"))
            ]
            assert "incompleteSearch" in request.url.params["fields"]
            return httpx.Response(200, json={"files": listed, "incompleteSearch": incomplete_search})
        entry = files[path.rsplit("/", 1)[-1]]
        if isinstance(entry, int):
            return httpx.Response(entry, json={"error": {"code": entry}})
        return httpx.Response(200, json=entry)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        drive_module.httpx,
        "AsyncClient",
        lambda **kw: real_client(
            transport=httpx.MockTransport(handler), **{k: v for k, v in kw.items() if k != "transport"}
        ),
    )
    for f in files.values():
        if isinstance(f, dict):
            f.setdefault("mimeType", doc_mime)
            f.setdefault("modifiedTime", EDITED)
            f.setdefault("name", f["id"])
    adapter = GoogleDriveAdapter(MagicMock(), MagicMock())
    adapter.ensure_token = AsyncMock(return_value="tok")  # type: ignore[method-assign]
    adapter.fetch_document = AsyncMock(return_value=b"body")  # type: ignore[method-assign]
    return adapter


@pytest.mark.asyncio
async def test_drive_folder_file_deleted_or_trashed_is_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _drive(monkeypatch, {"f1": {"id": "f1"}, "f2": {"id": "f2", "trashed": True}})

    _run_, ingest = await _sync(
        adapter, _portal_config("google_drive", {"folder_id": "fold"}), _previous(["f1", "f2", "f3"])
    )

    assert sorted(_deleted(ingest)) == ["f2", "f3"]


@pytest.mark.asyncio
async def test_drive_selected_file_deleted_or_trashed_is_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _drive(monkeypatch, {"f1": {"id": "f1"}, "f2": {"id": "f2", "trashed": True}, "f3": 404})

    _run_, ingest = await _sync(
        adapter, _portal_config("google_drive", {"item_ids": ["f1", "f2", "f3"]}), _previous(["f1", "f2", "f3"])
    )

    assert sorted(_deleted(ingest)) == ["f2", "f3"]


@pytest.mark.asyncio
async def test_drive_same_named_files_one_deleted_keeps_the_remaining_files_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drive paths are file names; the stored row may carry the deleted twin's ref."""
    adapter = _drive(monkeypatch, {"f1": {"id": "f1", "name": "Report.docx"}})

    _run_, ingest = await _sync(
        adapter,
        _portal_config("google_drive", {"folder_id": "fold"}),
        _previous(["f1", "f2"]),
        stored={"f2": {"Report.docx"}, "f3": {"Other.docx"}},
    )

    _assert_single_scoped_delete(ingest, "f3")


@pytest.mark.asyncio
async def test_drive_incomplete_search_deletes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _drive(monkeypatch, {"f1": {"id": "f1"}}, incomplete_search=True)

    run, ingest = await _sync(adapter, _portal_config("google_drive", {"folder_id": "fold"}), _previous(["f1", "f9"]))

    ingest.delete_connector_document.assert_not_awaited()
    held = next(e for e in run.error_details if e.get("warning") == "stale_cleanup_held_back")
    assert held["reason"] == "listing_incomplete:incomplete_search"


@pytest.mark.asyncio
async def test_drive_selected_file_with_a_server_error_fails_the_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _drive(monkeypatch, {"f1": {"id": "f1"}, "f2": 500})

    run, ingest = await _sync(
        adapter, _portal_config("google_drive", {"item_ids": ["f1", "f2"]}), _previous(["f1", "f2"])
    )

    assert run.status == SyncStatus.FAILED
    ingest.delete_connector_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_drive_listing_cut_by_max_files_deletes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _drive(monkeypatch, {"f1": {"id": "f1"}, "f2": {"id": "f2"}})

    _run_, ingest = await _sync(
        adapter, _portal_config("google_drive", {"folder_id": "fold", "max_files": 1}), _previous(["f1", "f9"])
    )

    ingest.delete_connector_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_drive_unscoped_change_feed_deletes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _drive(monkeypatch, {"f1": {"id": "f1"}})

    _run_, ingest = await _sync(adapter, _portal_config("google_drive", {}), _previous(["f1", "f9"]))

    ingest.delete_connector_document.assert_not_awaited()


# -- OneDrive / SharePoint pinned items --------------------------------------------


def _ms(monkeypatch: pytest.MonkeyPatch, items: dict[str, int | None]) -> MsDocsAdapter:
    adapter = MsDocsAdapter(MagicMock(), MagicMock())

    async def graph_get_json(url: str, connector: Any) -> dict[str, Any]:
        item_id = url.rsplit("/", 1)[-1]
        status = items[item_id]
        if status:
            request = httpx.Request("GET", "https://graph.example.com" + "/" + item_id)
            raise httpx.HTTPStatusError("x", request=request, response=httpx.Response(status, request=request))
        return {"id": item_id, "name": item_id, "file": {"mimeType": "text/plain"}, "lastModifiedDateTime": EDITED}

    adapter._graph_get_json = graph_get_json  # type: ignore[method-assign]
    adapter.fetch_document = AsyncMock(return_value=b"body")  # type: ignore[method-assign]
    return adapter


@pytest.mark.asyncio
async def test_ms_pinned_item_gone_is_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _ms(monkeypatch, {"i1": None, "i2": 404})

    _run_, ingest = await _sync(
        adapter, _portal_config("ms_docs", {"drive_id": "d1", "item_ids": ["i1", "i2"]}), _previous(["i1", "i2"])
    )

    _assert_single_scoped_delete(ingest, "i2")


@pytest.mark.asyncio
async def test_ms_pinned_item_forbidden_fails_the_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _ms(monkeypatch, {"i1": None, "i2": 403})

    run, ingest = await _sync(
        adapter, _portal_config("ms_docs", {"drive_id": "d1", "item_ids": ["i1", "i2"]}), _previous(["i1", "i2"])
    )

    assert run.status == SyncStatus.FAILED
    ingest.delete_connector_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_ms_delta_mode_deletes_nothing() -> None:
    """Delta returns only what changed since the cursor; a missing item is no evidence."""
    adapter = MsDocsAdapter(MagicMock(), MagicMock())
    item = {"id": "i1", "name": "i1", "file": {"mimeType": "text/plain"}, "lastModifiedDateTime": EDITED}
    adapter._drain_delta = AsyncMock(return_value=([item], "https://graph.example.com/delta?token=t2"))  # type: ignore[method-assign]
    adapter.fetch_document = AsyncMock(return_value=b"body")  # type: ignore[method-assign]

    _run_, ingest = await _sync(
        adapter,
        _portal_config("ms_docs", {"drive_id": "d1"}),
        _previous(["i1", "i9"], delta_link="https://graph.example.com/delta?token=t1"),
    )

    ingest.delete_connector_document.assert_not_awaited()


# -- JSON feed --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_json_feed_group_gone_from_the_feed_is_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    records = [{"name": f"Item {i}", "category": "a", "price": i} for i in range(5)]
    adapter = JsonFeedAdapter()
    monkeypatch.setattr(adapter, "_fetch_json", AsyncMock(return_value=records))
    config = _portal_config("json_feed", {"url": "https://data.example.com/feed.json", "group_by": ["category"]})
    live_ref = (await adapter.list_documents(config))[0].source_ref
    gone_ref = f"json-feed:{CONNECTOR_ID}:category-b"

    _run_, ingest = await _sync(adapter, config, _previous([live_ref, gone_ref]))

    _assert_single_scoped_delete(ingest, gone_ref)


# -- Caller side of GET /ingest/v1/connector/source-refs -------------------------


@pytest.mark.asyncio
async def test_client_reads_every_page_of_stored_refs_for_the_connector() -> None:
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/ingest/v1/connector/source-refs"
        assert request.headers["X-Internal-Secret"] == "internal-secret"
        assert request.headers["X-Caller-Service"] == "connector"
        params = dict(request.url.params)
        seen.append(params)
        if params["after"] == "":
            page = [
                {"source_ref": "ref-a", "paths": ["a.md"]},
                {"source_ref": "ref-b", "paths": ["b.md", "b-old.md"]},
            ]
            return httpx.Response(200, json={"source_refs": page, "next_after": "ref-b"})
        return httpx.Response(
            200, json={"source_refs": [{"source_ref": "ref-c", "paths": ["c.md"]}], "next_after": None}
        )

    client = KnowledgeIngestClient("http://knowledge-ingest", "internal-secret")
    client._client = httpx.AsyncClient(base_url="http://knowledge-ingest", transport=httpx.MockTransport(handler))

    refs = await client.list_connector_source_refs(org_id=ORG_ID, kb_slug=KB_SLUG, source_connector_id="c-1")

    assert refs == {"ref-a": {"a.md"}, "ref-b": {"b.md", "b-old.md"}, "ref-c": {"c.md"}}
    assert [(p["org_id"], p["kb_slug"], p["connector_id"], p["after"]) for p in seen] == [
        (ORG_ID, KB_SLUG, "c-1", ""),
        (ORG_ID, KB_SLUG, "c-1", "ref-b"),
    ]


@pytest.mark.asyncio
async def test_client_raises_when_the_store_read_fails() -> None:
    client = KnowledgeIngestClient("http://knowledge-ingest", "internal-secret")
    client._client = httpx.AsyncClient(
        base_url="http://knowledge-ingest", transport=httpx.MockTransport(lambda _r: httpx.Response(500))
    )

    with pytest.raises(httpx.HTTPStatusError):
        await client.list_connector_source_refs(org_id=ORG_ID, kb_slug=KB_SLUG, source_connector_id="c-1")
