"""Specification tests for GoogleDriveAdapter -- SPEC-KB-025.

RED phase: these tests define expected behavior before implementation exists.

All OAuth token strings below are test placeholders, NOT real credentials.
"""

# ruff: noqa: S106  -- test-only placeholder token strings

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.adapters.base import DocumentRef

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_connector(config: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        id="gdrive-conn-001",
        org_id="org-001",
        config=config,
    )


@pytest.fixture
def gdrive_connector() -> SimpleNamespace:
    """Connector with a fresh access token and folder filter."""
    return _make_connector(
        {
            "access_token": "placeholder-access-value",
            "refresh_token": "placeholder-refresh-value",
            "token_expiry": "2030-01-01T00:00:00+00:00",
            "folder_id": "fldr-root",
            "max_files": 200,
        }
    )


@pytest.fixture
def gdrive_adapter() -> Any:
    """Create a GoogleDriveAdapter instance backed by a mock portal client."""
    from app.adapters.google_drive import GoogleDriveAdapter

    settings = MagicMock()
    settings.google_drive_client_id = "placeholder-client-id"
    settings.google_drive_client_secret = "placeholder-client-secret"

    portal_client = MagicMock()
    portal_client.update_credentials = AsyncMock()

    adapter = GoogleDriveAdapter(settings=settings, portal_client=portal_client)
    return adapter


def _list_response(files: list[dict[str, Any]], next_page_token: str | None = None) -> dict:
    payload: dict[str, Any] = {"files": files}
    if next_page_token:
        payload["nextPageToken"] = next_page_token
    return payload


# ---------------------------------------------------------------------------
# 1. list_documents -- first sync, returns Google Doc + binary doc
# ---------------------------------------------------------------------------


async def test_list_documents_first_sync_returns_drive_files(
    gdrive_adapter: Any,
    gdrive_connector: SimpleNamespace,
) -> None:
    """First sync (no cursor) returns all supported Drive files as DocumentRefs."""
    files_response = _list_response(
        [
            {
                "id": "file-gdoc-1",
                "name": "Team Notes",
                "mimeType": "application/vnd.google-apps.document",
                "modifiedTime": "2026-04-10T10:00:00.000Z",
                "webViewLink": "https://docs.google.com/document/d/file-gdoc-1",
                "size": None,  # Google-native docs have no size
            },
            {
                "id": "file-pdf-1",
                "name": "Policy.pdf",
                "mimeType": "application/pdf",
                "modifiedTime": "2026-04-11T09:00:00.000Z",
                "webViewLink": "https://drive.google.com/file/d/file-pdf-1/view",
                "size": "12345",
            },
        ]
    )

    with patch.object(
        gdrive_adapter,
        "_list_files",
        AsyncMock(return_value=files_response),
    ):
        refs = await gdrive_adapter.list_documents(gdrive_connector, cursor_context=None)

    assert len(refs) == 2
    assert all(isinstance(r, DocumentRef) for r in refs)
    gdoc = next(r for r in refs if r.ref == "file-gdoc-1")
    pdf = next(r for r in refs if r.ref == "file-pdf-1")
    assert gdoc.content_type == "google_doc"
    assert pdf.content_type == "kb_article" or pdf.content_type == "pdf_document"
    assert gdoc.source_url == "https://docs.google.com/document/d/file-gdoc-1"


# ---------------------------------------------------------------------------
# 2. list_documents -- incremental sync uses Changes API cursor
# ---------------------------------------------------------------------------


async def test_list_documents_incremental_uses_cursor(
    gdrive_adapter: Any,
    gdrive_connector: SimpleNamespace,
) -> None:
    """Incremental sync passes cursor_context['page_token'] through to the changes API."""
    cursor = {"page_token": "existing-cursor-token"}
    changes_response = {
        "changes": [
            {
                "fileId": "file-gdoc-2",
                "file": {
                    "id": "file-gdoc-2",
                    "name": "Changed doc",
                    "mimeType": "application/vnd.google-apps.document",
                    "modifiedTime": "2026-04-12T11:00:00.000Z",
                    "webViewLink": "https://docs.google.com/document/d/file-gdoc-2",
                    "size": None,
                },
            },
        ],
        "newStartPageToken": "new-cursor-token",
    }

    list_changes = AsyncMock(return_value=changes_response)
    with patch.object(gdrive_adapter, "_list_changes", list_changes):
        refs = await gdrive_adapter.list_documents(gdrive_connector, cursor_context=cursor)

    list_changes.assert_awaited_once()
    call_kwargs = list_changes.call_args.kwargs or {}
    call_args = list_changes.call_args.args or ()
    # The adapter forwards the page_token to the changes API
    assert (
        call_kwargs.get("page_token") == "existing-cursor-token"
        or "existing-cursor-token" in call_args
    )
    assert len(refs) == 1
    assert refs[0].ref == "file-gdoc-2"


# ---------------------------------------------------------------------------
# 3. list_documents -- respects max_files cap
# ---------------------------------------------------------------------------


async def test_list_documents_respects_max_files(
    gdrive_adapter: Any,
) -> None:
    """max_files in config caps how many DocumentRefs are returned."""
    connector = _make_connector(
        {
            "access_token": "placeholder-access-value",
            "refresh_token": "placeholder-refresh-value",
            "folder_id": "fldr-root",
            "max_files": 2,
        }
    )
    files = [
        {
            "id": f"file-{i}",
            "name": f"f{i}.pdf",
            "mimeType": "application/pdf",
            "modifiedTime": "2026-04-10T10:00:00.000Z",
            "webViewLink": f"https://drive.google.com/file/d/file-{i}/view",
            "size": "100",
        }
        for i in range(5)
    ]

    with patch.object(gdrive_adapter, "_list_files", AsyncMock(return_value=_list_response(files))):
        refs = await gdrive_adapter.list_documents(connector, cursor_context=None)

    assert len(refs) == 2


# ---------------------------------------------------------------------------
# 4. fetch_document -- Google Doc is exported as DOCX
# ---------------------------------------------------------------------------


async def test_fetch_document_exports_google_doc_as_docx(
    gdrive_adapter: Any,
    gdrive_connector: SimpleNamespace,
) -> None:
    """A Google-native document is fetched via files.export?mimeType=...docx."""
    ref = DocumentRef(
        path="Team Notes",
        ref="file-gdoc-1",
        size=0,
        content_type="google_doc",
        source_ref="file-gdoc-1",
        source_url="https://docs.google.com/document/d/file-gdoc-1",
        last_edited="2026-04-10T10:00:00.000Z",
    )
    expected_bytes = b"FAKE_DOCX_BINARY"
    export = AsyncMock(return_value=expected_bytes)

    with patch.object(gdrive_adapter, "_export_file", export):
        data = await gdrive_adapter.fetch_document(ref, gdrive_connector)

    assert data == expected_bytes
    export.assert_awaited_once()
    call = export.call_args
    mime = call.kwargs.get("mime_type") or (call.args[2] if len(call.args) >= 3 else None)
    assert mime is not None
    assert "wordprocessingml" in mime  # .docx export mime type


# ---------------------------------------------------------------------------
# 5. fetch_document -- binary PDF uses alt=media
# ---------------------------------------------------------------------------


async def test_fetch_document_binary_uses_alt_media(
    gdrive_adapter: Any,
    gdrive_connector: SimpleNamespace,
) -> None:
    """A non-Google-native file is fetched via files/{id}?alt=media."""
    ref = DocumentRef(
        path="Policy.pdf",
        ref="file-pdf-1",
        size=12345,
        content_type="pdf_document",
        source_ref="file-pdf-1",
        source_url="https://drive.google.com/file/d/file-pdf-1/view",
        last_edited="2026-04-11T09:00:00.000Z",
    )
    expected = b"FAKE_PDF_BYTES"
    download = AsyncMock(return_value=expected)

    with patch.object(gdrive_adapter, "_download_file", download):
        data = await gdrive_adapter.fetch_document(ref, gdrive_connector)

    assert data == expected
    download.assert_awaited_once()


# ---------------------------------------------------------------------------
# 6. ensure_token -- refreshes expired tokens via OAuth refresh endpoint
# ---------------------------------------------------------------------------


async def test_ensure_token_refreshes_when_expired(
    gdrive_adapter: Any,
) -> None:
    """When the cached token has expired, ensure_token refreshes via the token endpoint."""
    connector = _make_connector(
        {
            "access_token": "placeholder-old-access-value",
            "refresh_token": "placeholder-refresh-value",
        }
    )
    # Prime cache with an already-expired token
    gdrive_adapter._token_cache[connector.id] = (  # type: ignore[attr-defined]
        "placeholder-old-access-value",
        time.monotonic() - 100.0,
    )

    refresh_payload = {
        "access_token": "placeholder-fresh-access-value",
        "expires_in": 3599,
        "token_type": "Bearer",
    }
    refresh = AsyncMock(return_value=refresh_payload)

    with patch.object(gdrive_adapter, "_refresh_oauth_token", refresh):
        token = await gdrive_adapter.ensure_token(connector)

    assert token == "placeholder-fresh-access-value"
    refresh.assert_awaited_once()
    # Writeback to portal must happen with the new access token
    gdrive_adapter._portal_client.update_credentials.assert_awaited_once()
    kwargs = gdrive_adapter._portal_client.update_credentials.call_args.kwargs
    assert kwargs.get("access_token") == "placeholder-fresh-access-value"
    assert kwargs.get("connector_id") == "gdrive-conn-001"


# ---------------------------------------------------------------------------
# 7. ensure_token -- uses cache when token still fresh
# ---------------------------------------------------------------------------


async def test_ensure_token_uses_cache_when_fresh(
    gdrive_adapter: Any,
) -> None:
    """When cached token is still valid, ensure_token returns it without refreshing."""
    connector = _make_connector(
        {
            "access_token": "placeholder-cached-access",
            "refresh_token": "placeholder-refresh-value",
        }
    )
    gdrive_adapter._token_cache[connector.id] = (  # type: ignore[attr-defined]
        "placeholder-cached-access",
        time.monotonic() + 3600.0,
    )

    refresh = AsyncMock()

    with patch.object(gdrive_adapter, "_refresh_oauth_token", refresh):
        token = await gdrive_adapter.ensure_token(connector)

    assert token == "placeholder-cached-access"
    refresh.assert_not_awaited()
    gdrive_adapter._portal_client.update_credentials.assert_not_awaited()


# ---------------------------------------------------------------------------
# 8. get_cursor_state -- persists newStartPageToken from Changes API
# ---------------------------------------------------------------------------


async def test_get_cursor_state_returns_page_token(
    gdrive_adapter: Any,
    gdrive_connector: SimpleNamespace,
) -> None:
    """After a sync, get_cursor_state returns a dict containing the next page_token."""
    # Simulate that a previous list_documents call stored the token internally
    gdrive_adapter._latest_page_token[gdrive_connector.id] = "new-cursor-token"  # type: ignore[attr-defined]

    state = await gdrive_adapter.get_cursor_state(gdrive_connector)

    assert state.get("page_token") == "new-cursor-token"
