"""Specification tests for MsDocsAdapter -- SPEC-KB-MS-DOCS-001.

Mirrors the test structure of test_google_drive.py since the adapter itself
mirrors GoogleDriveAdapter. Tests drive-root delta sync, incremental delta
via persisted deltaLink, site_url resolution, credential metadata extraction,
and refresh-token rotation.

All OAuth token strings below are test placeholders, NOT real credentials.
"""

# ruff: noqa: S106  -- test-only placeholder token strings

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.adapters.base import DocumentRef


def _make_connector(config: dict[str, Any]) -> SimpleNamespace:
    base = {
        "access_token": "placeholder-access-value",
        "refresh_token": "placeholder-refresh-value",
        "token_expiry": "2030-01-01T00:00:00+00:00",
    }
    base.update(config)
    return SimpleNamespace(
        id="msdocs-conn-001",
        org_id="org-001",
        config=base,
    )


@pytest.fixture
def ms_connector() -> SimpleNamespace:
    return _make_connector({})


@pytest.fixture
def ms_adapter() -> Any:
    from app.adapters.ms_docs import MsDocsAdapter

    settings = MagicMock()
    settings.ms_docs_client_id = "placeholder-client-id"
    settings.ms_docs_client_secret = "placeholder-client-secret"
    settings.ms_docs_tenant_id = "common"

    portal_client = MagicMock()
    portal_client.update_credentials = AsyncMock()

    adapter = MsDocsAdapter(settings=settings, portal_client=portal_client)
    adapter._cache_token("msdocs-conn-001", "placeholder-access-value", expires_in_seconds=3600.0)
    return adapter


def _drive_item(
    item_id: str,
    name: str,
    mime: str,
    *,
    last_modified: str = "2026-04-20T10:00:00Z",
    web_url: str | None = None,
    size: int = 1024,
    created_by_email: str = "alice@example.com",
    modified_by_email: str = "bob@example.com",
) -> dict[str, Any]:
    return {
        "id": item_id,
        "name": name,
        "webUrl": web_url or f"https://contoso.sharepoint.com/personal/alice/Documents/{name}",
        "size": size,
        "lastModifiedDateTime": last_modified,
        "file": {"mimeType": mime},
        "createdBy": {"user": {"email": created_by_email}},
        "lastModifiedBy": {"user": {"email": modified_by_email}},
    }


@pytest.mark.asyncio
async def test_list_documents_first_sync_hits_me_drive_delta(
    ms_adapter: Any, ms_connector: SimpleNamespace,
) -> None:
    """No drive_id, no site_url, no cursor → /me/drive/root/delta."""
    delta_response = {
        "value": [
            _drive_item(
                "item-docx-1",
                "Report.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
            _drive_item("item-pdf-1", "Policy.pdf", "application/pdf"),
        ],
        "@odata.deltaLink": "https://graph.microsoft.com/v1.0/me/drive/root/delta?token=xyz",
    }
    call_url: dict[str, str] = {}

    async def _fake_get(url: str) -> dict[str, Any]:
        call_url["url"] = url
        return delta_response

    with patch.object(ms_adapter, "_graph_get_json", side_effect=_fake_get):
        refs = await ms_adapter.list_documents(ms_connector, cursor_context=None)

    assert "me/drive/root/delta" in call_url["url"]
    assert len(refs) == 2
    assert all(isinstance(r, DocumentRef) for r in refs)
    docx = next(r for r in refs if r.ref == "item-docx-1")
    assert docx.content_type == "word_document"
    assert docx.source_url.startswith("https://contoso.sharepoint.com/")
    assert docx.last_edited == "2026-04-20T10:00:00Z"
    assert ms_adapter._latest_delta_link["msdocs-conn-001"].endswith("token=xyz")


@pytest.mark.asyncio
async def test_list_documents_site_url_resolves_and_uses_site_delta(ms_adapter: Any) -> None:
    """site_url config is resolved via /sites/{hostname}:/{path} before delta."""
    connector = _make_connector({"site_url": "https://contoso.sharepoint.com/sites/marketing"})
    connector.id = "msdocs-conn-001"

    resolve_response = {"id": "contoso.sharepoint.com,guid1,guid2", "displayName": "Marketing"}
    delta_response = {
        "value": [_drive_item("item-marketing-1", "Brief.docx",
                              "application/vnd.openxmlformats-officedocument.wordprocessingml.document")],
        "@odata.deltaLink": "https://graph.microsoft.com/v1.0/sites/.../drive/root/delta?token=s1",
    }
    seen_urls: list[str] = []

    async def _fake_get(url: str, connector: Any = None) -> dict[str, Any]:
        seen_urls.append(url)
        if ":/sites/marketing" in url:
            return resolve_response
        return delta_response

    with patch.object(ms_adapter, "_graph_get_json", side_effect=_fake_get):
        refs = await ms_adapter.list_documents(connector, cursor_context=None)

    assert any("contoso.sharepoint.com:/sites/marketing" in u for u in seen_urls)
    assert any("sites/contoso.sharepoint.com,guid1,guid2/drive/root/delta" in u for u in seen_urls)
    assert len(refs) == 1
    assert ms_adapter._resolved_sites["msdocs-conn-001"] == "contoso.sharepoint.com,guid1,guid2"


@pytest.mark.asyncio
async def test_list_documents_incremental_uses_persisted_delta_link(
    ms_adapter: Any, ms_connector: SimpleNamespace,
) -> None:
    """A cursor with delta_link calls that URL directly; no resolution lookup."""
    stored_link = "https://graph.microsoft.com/v1.0/me/drive/root/delta?token=stored"
    delta_response = {
        "value": [_drive_item(
            "item-changed-1", "Updated.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )],
        "@odata.deltaLink": "https://graph.microsoft.com/v1.0/me/drive/root/delta?token=new",
    }
    call_urls: list[str] = []

    async def _fake_get(url: str) -> dict[str, Any]:
        call_urls.append(url)
        return delta_response

    with patch.object(ms_adapter, "_graph_get_json", side_effect=_fake_get):
        refs = await ms_adapter.list_documents(
            ms_connector, cursor_context={"delta_link": stored_link}
        )

    assert call_urls == [stored_link]
    assert len(refs) == 1
    assert refs[0].content_type == "excel_document"
    assert ms_adapter._latest_delta_link["msdocs-conn-001"].endswith("token=new")


@pytest.mark.asyncio
async def test_list_documents_paginates_next_link(
    ms_adapter: Any, ms_connector: SimpleNamespace,
) -> None:
    """Multi-page delta response: collect across @odata.nextLink pages."""
    page1 = {
        "value": [_drive_item("item-a", "A.pdf", "application/pdf")],
        "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/drive/root/delta?token=page2",
    }
    page2 = {
        "value": [_drive_item("item-b", "B.pdf", "application/pdf")],
        "@odata.deltaLink": "https://graph.microsoft.com/v1.0/me/drive/root/delta?token=final",
    }
    responses = iter([page1, page2])

    async def _fake_get(url: str) -> dict[str, Any]:
        return next(responses)

    with patch.object(ms_adapter, "_graph_get_json", side_effect=_fake_get):
        refs = await ms_adapter.list_documents(ms_connector, cursor_context=None)

    assert {r.ref for r in refs} == {"item-a", "item-b"}
    assert ms_adapter._latest_delta_link["msdocs-conn-001"].endswith("token=final")


@pytest.mark.asyncio
async def test_list_documents_drive_id_takes_precedence(ms_adapter: Any) -> None:
    """config.drive_id is preferred over site_url; no resolution call needed."""
    connector = _make_connector(
        {"drive_id": "b!xyz", "site_url": "https://contoso.sharepoint.com/sites/marketing"}
    )
    connector.id = "msdocs-conn-001"

    delta_response = {
        "value": [],
        "@odata.deltaLink": "https://graph.microsoft.com/v1.0/drives/b!xyz/root/delta?token=a",
    }
    call_urls: list[str] = []

    async def _fake_get(url: str) -> dict[str, Any]:
        call_urls.append(url)
        return delta_response

    with patch.object(ms_adapter, "_graph_get_json", side_effect=_fake_get):
        await ms_adapter.list_documents(connector, cursor_context=None)

    assert any("drives/b!xyz/root/delta" in u for u in call_urls)
    assert not any(":/sites/marketing" in u for u in call_urls)


@pytest.mark.asyncio
async def test_fetch_document_downloads_content(
    ms_adapter: Any, ms_connector: SimpleNamespace,
) -> None:
    """A drive item is fetched via /drive/items/{id}/content."""
    ref = DocumentRef(
        path="Report.docx",
        ref="item-docx-1",
        size=1024,
        content_type="word_document",
        source_ref="item-docx-1",
        source_url="https://contoso.sharepoint.com/personal/alice/Documents/Report.docx",
        last_edited="2026-04-20T10:00:00Z",
    )
    expected = b"FAKE_DOCX_BINARY"

    async def _fake_content(url: str, connector: Any = None) -> bytes:
        assert "/drive/items/item-docx-1/content" in url
        return expected

    with patch.object(ms_adapter, "_graph_get_bytes", side_effect=_fake_content):
        data = await ms_adapter.fetch_document(ref, ms_connector)

    assert data == expected


@pytest.mark.asyncio
async def test_get_cursor_state_returns_stored_delta_link(
    ms_adapter: Any, ms_connector: SimpleNamespace,
) -> None:
    """After a list_documents run, get_cursor_state returns the latest deltaLink."""
    ms_adapter._latest_delta_link["msdocs-conn-001"] = "https://graph/.../delta?token=zzz"
    state = await ms_adapter.get_cursor_state(ms_connector)
    assert state == {"delta_link": "https://graph/.../delta?token=zzz"}


@pytest.mark.asyncio
async def test_get_cursor_state_bootstraps_when_empty(
    ms_adapter: Any, ms_connector: SimpleNamespace,
) -> None:
    """No cached delta_link → call Graph delta once to obtain one."""
    assert "msdocs-conn-001" not in ms_adapter._latest_delta_link

    delta_response = {
        "value": [],
        "@odata.deltaLink": "https://graph.microsoft.com/v1.0/me/drive/root/delta?token=boot",
    }

    async def _fake_get(url: str) -> dict[str, Any]:
        return delta_response

    with patch.object(ms_adapter, "_graph_get_json", side_effect=_fake_get):
        state = await ms_adapter.get_cursor_state(ms_connector)

    assert state == {"delta_link": "https://graph.microsoft.com/v1.0/me/drive/root/delta?token=boot"}


@pytest.mark.asyncio
async def test_content_type_mime_mapping(
    ms_adapter: Any, ms_connector: SimpleNamespace,
) -> None:
    """All MIME types from R2.6 map to the expected content_type labels."""
    mime_map = {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "word_document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "excel_document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": "powerpoint_document",
        "application/pdf": "pdf_document",
        "text/plain": "kb_article",
    }
    items = [_drive_item(f"item-{i}", f"f{i}", mime) for i, mime in enumerate(mime_map)]
    delta_response = {"value": items, "@odata.deltaLink": "https://graph/.../delta?t=x"}

    async def _fake_get(url: str) -> dict[str, Any]:
        return delta_response

    with patch.object(ms_adapter, "_graph_get_json", side_effect=_fake_get):
        refs = await ms_adapter.list_documents(ms_connector, cursor_context=None)

    for ref, expected in zip(refs, mime_map.values(), strict=True):
        assert ref.content_type == expected


@pytest.mark.asyncio
async def test_identifier_capture_sender_and_mentioned(
    ms_adapter: Any, ms_connector: SimpleNamespace,
) -> None:
    """Adapter-owned metadata carries sender_email + mentioned_emails."""
    delta_response = {
        "value": [_drive_item(
            "item-xyz", "Doc.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            created_by_email="alice@example.com",
            modified_by_email="bob@example.com",
        )],
        "@odata.deltaLink": "https://graph/.../delta?t=x",
    }

    async def _fake_get(url: str) -> dict[str, Any]:
        return delta_response

    with patch.object(ms_adapter, "_graph_get_json", side_effect=_fake_get):
        refs = await ms_adapter.list_documents(ms_connector, cursor_context=None)

    meta = ms_adapter._get_metadata_for_ref(refs[0])
    assert meta.get("sender_email") == "bob@example.com"
    assert set(meta.get("mentioned_emails", [])) == {"alice@example.com", "bob@example.com"}


@pytest.mark.asyncio
async def test_identifier_capture_tolerates_missing_emails(
    ms_adapter: Any, ms_connector: SimpleNamespace,
) -> None:
    """When createdBy/lastModifiedBy lack emails, sender_email='' and list empty."""
    delta_response = {
        "value": [{
            "id": "item-anon",
            "name": "Anon.pdf",
            "webUrl": "https://contoso.sharepoint.com/Documents/Anon.pdf",
            "size": 100,
            "lastModifiedDateTime": "2026-04-20T10:00:00Z",
            "file": {"mimeType": "application/pdf"},
        }],
        "@odata.deltaLink": "https://graph/.../delta?t=x",
    }

    async def _fake_get(url: str) -> dict[str, Any]:
        return delta_response

    with patch.object(ms_adapter, "_graph_get_json", side_effect=_fake_get):
        refs = await ms_adapter.list_documents(ms_connector, cursor_context=None)

    meta = ms_adapter._get_metadata_for_ref(refs[0])
    assert meta.get("sender_email") == ""
    assert meta.get("mentioned_emails", []) == []


@pytest.mark.asyncio
async def test_refresh_oauth_token_posts_to_microsoft_endpoint(
    ms_adapter: Any, ms_connector: SimpleNamespace,
) -> None:
    """_refresh_oauth_token POSTs refresh_token grant to login.microsoftonline.com."""
    mock_response = MagicMock()
    mock_response.json = MagicMock(
        return_value={
            "access_token": "placeholder-new-access",
            "expires_in": 3600,
            "refresh_token": "placeholder-new-refresh",
        }
    )
    mock_response.raise_for_status = MagicMock(return_value=None)

    http_client = MagicMock()
    http_client.post = AsyncMock(return_value=mock_response)
    http_client.__aenter__ = AsyncMock(return_value=http_client)
    http_client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "app.adapters.ms_docs.httpx.AsyncClient",
        MagicMock(return_value=http_client),
    ):
        payload = await ms_adapter._refresh_oauth_token(
            ms_connector, refresh_token="placeholder-refresh-value"
        )

    http_client.post.assert_awaited_once()
    url = http_client.post.call_args.args[0]
    assert "login.microsoftonline.com/common/oauth2/v2.0/token" in url
    body = http_client.post.call_args.kwargs.get("data", {})
    assert body.get("grant_type") == "refresh_token"
    assert body.get("refresh_token") == "placeholder-refresh-value"
    assert body.get("client_id") == "placeholder-client-id"
    assert body.get("client_secret") == "placeholder-client-secret"
    assert payload["access_token"] == "placeholder-new-access"
    assert payload["refresh_token"] == "placeholder-new-refresh"


@pytest.mark.asyncio
async def test_graph_get_json_retries_on_429(
    ms_adapter: Any, ms_connector: SimpleNamespace,
) -> None:
    """A 429 response triggers one retry after Retry-After seconds."""
    import httpx

    throttled = MagicMock()
    throttled.status_code = 429
    throttled.headers = {"Retry-After": "1"}
    http_err = httpx.HTTPStatusError("throttled", request=MagicMock(), response=throttled)
    throttled.raise_for_status = MagicMock(side_effect=http_err)

    ok_body = {"value": [], "@odata.deltaLink": "https://graph/.../delta?t=x"}
    ok_resp = MagicMock()
    ok_resp.status_code = 200
    ok_resp.raise_for_status = MagicMock(return_value=None)
    ok_resp.json = MagicMock(return_value=ok_body)

    responses = [throttled, ok_resp]

    async def _get_side_effect(*args: Any, **kwargs: Any) -> Any:
        return responses.pop(0)

    http_client = MagicMock()
    http_client.get = AsyncMock(side_effect=_get_side_effect)
    http_client.__aenter__ = AsyncMock(return_value=http_client)
    http_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("app.adapters.ms_docs.httpx.AsyncClient", MagicMock(return_value=http_client)),
        patch("app.adapters.ms_docs.asyncio.sleep", AsyncMock()) as sleep_mock,
    ):
        result = await ms_adapter._graph_get_json(
            "https://graph.microsoft.com/v1.0/me/drive/root/delta"
        )

    assert result == ok_body
    assert http_client.get.await_count == 2
    sleep_mock.assert_awaited_once()
