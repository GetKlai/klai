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


# ===========================================================================
# SPEC-KB-CONNECTORS-001 Phase 4 — Google Docs/Sheets/Slides split
# ===========================================================================

# ---------------------------------------------------------------------------
# 9. _extract_config — content_types config extraction
# ---------------------------------------------------------------------------


def test_extract_config_no_content_types_by_default(gdrive_adapter: Any) -> None:
    """connector_type=google_drive with no content_types → content_types absent/None (backward-compat)."""
    from app.adapters.google_drive import GoogleDriveAdapter

    connector = SimpleNamespace(
        id="gdrive-conn-001",
        org_id="org-001",
        connector_type="google_drive",
        config={
            "access_token": "placeholder-access-value",
            "refresh_token": "placeholder-refresh-value",
            "folder_id": "fldr-root",
        },
    )
    cfg = GoogleDriveAdapter._extract_config(connector)
    # Existing behavior: no content_types filter applied
    assert cfg.get("content_types") is None


def test_extract_config_accepts_explicit_content_types_list(gdrive_adapter: Any) -> None:
    """Explicit content_types list in config is preserved as-is."""
    from app.adapters.google_drive import GoogleDriveAdapter

    connector = SimpleNamespace(
        id="gdrive-conn-001",
        org_id="org-001",
        connector_type="google_drive",
        config={
            "access_token": "placeholder-access-value",
            "refresh_token": "placeholder-refresh-value",
            "content_types": ["google_doc", "google_sheet"],
        },
    )
    cfg = GoogleDriveAdapter._extract_config(connector)
    assert cfg["content_types"] == ["google_doc", "google_sheet"]


def test_extract_config_rejects_invalid_content_type(gdrive_adapter: Any) -> None:
    """config with unknown content_type raises ValueError listing allowed values."""
    from app.adapters.google_drive import GoogleDriveAdapter

    connector = SimpleNamespace(
        id="gdrive-conn-001",
        org_id="org-001",
        connector_type="google_drive",
        config={
            "access_token": "placeholder-access-value",
            "refresh_token": "placeholder-refresh-value",
            "content_types": ["google_invalid"],
        },
    )
    with pytest.raises(ValueError, match="google_invalid"):
        GoogleDriveAdapter._extract_config(connector)


def test_extract_config_injects_preset_for_google_docs_connector_type() -> None:
    """connector_type=google_docs with no explicit content_types → injects ['google_doc']."""
    from app.adapters.google_drive import GoogleDriveAdapter

    connector = SimpleNamespace(
        id="gdocs-conn-001",
        org_id="org-001",
        connector_type="google_docs",
        config={
            "access_token": "placeholder-access-value",
            "refresh_token": "placeholder-refresh-value",
        },
    )
    cfg = GoogleDriveAdapter._extract_config(connector)
    assert cfg["content_types"] == ["google_doc"]


def test_extract_config_injects_preset_for_google_sheets_connector_type() -> None:
    """connector_type=google_sheets with no explicit content_types → injects ['google_sheet']."""
    from app.adapters.google_drive import GoogleDriveAdapter

    connector = SimpleNamespace(
        id="gsheets-conn-001",
        org_id="org-001",
        connector_type="google_sheets",
        config={
            "access_token": "placeholder-access-value",
            "refresh_token": "placeholder-refresh-value",
        },
    )
    cfg = GoogleDriveAdapter._extract_config(connector)
    assert cfg["content_types"] == ["google_sheet"]


def test_extract_config_injects_preset_for_google_slides_connector_type() -> None:
    """connector_type=google_slides with no explicit content_types → injects ['google_slides']."""
    from app.adapters.google_drive import GoogleDriveAdapter

    connector = SimpleNamespace(
        id="gslides-conn-001",
        org_id="org-001",
        connector_type="google_slides",
        config={
            "access_token": "placeholder-access-value",
            "refresh_token": "placeholder-refresh-value",
        },
    )
    cfg = GoogleDriveAdapter._extract_config(connector)
    assert cfg["content_types"] == ["google_slides"]


def test_extract_config_explicit_content_types_override_alias_preset() -> None:
    """Explicit content_types in config wins over connector_type alias preset."""
    from app.adapters.google_drive import GoogleDriveAdapter

    connector = SimpleNamespace(
        id="gdocs-conn-001",
        org_id="org-001",
        connector_type="google_docs",
        config={
            "access_token": "placeholder-access-value",
            "refresh_token": "placeholder-refresh-value",
            "content_types": ["google_sheet"],  # explicitly overrides the docs preset
        },
    )
    cfg = GoogleDriveAdapter._extract_config(connector)
    assert cfg["content_types"] == ["google_sheet"]


# ---------------------------------------------------------------------------
# 10. list_documents — content_types mimeType filter
# ---------------------------------------------------------------------------


def _make_alias_connector(
    connector_type: str = "google_drive",
    content_types: list[str] | None = None,
) -> SimpleNamespace:
    config: dict[str, Any] = {
        "access_token": "placeholder-access-value",
        "refresh_token": "placeholder-refresh-value",
        "token_expiry": "2030-01-01T00:00:00+00:00",
    }
    if content_types is not None:
        config["content_types"] = content_types
    return SimpleNamespace(
        id="gdrive-conn-filter-001",
        org_id="org-001",
        connector_type=connector_type,
        config=config,
    )


async def test_list_documents_no_filter_lists_all_workspace_types(
    gdrive_adapter: Any,
) -> None:
    """Backward-compat: no content_types → all returned files are yielded."""
    connector = _make_alias_connector(connector_type="google_drive")
    files_response = _list_response(
        [
            {
                "id": "file-doc-1",
                "name": "Doc",
                "mimeType": "application/vnd.google-apps.document",
                "modifiedTime": "2026-04-10T10:00:00.000Z",
                "webViewLink": "https://docs.google.com/d/1",
                "size": None,
                "owners": [],
                "permissions": [],
            },
            {
                "id": "file-sheet-1",
                "name": "Sheet",
                "mimeType": "application/vnd.google-apps.spreadsheet",
                "modifiedTime": "2026-04-10T10:00:00.000Z",
                "webViewLink": "https://sheets.google.com/d/1",
                "size": None,
                "owners": [],
                "permissions": [],
            },
        ]
    )

    with patch.object(gdrive_adapter, "_list_files", AsyncMock(return_value=files_response)):
        refs = await gdrive_adapter.list_documents(connector, cursor_context=None)

    assert len(refs) == 2


async def test_list_documents_google_doc_filter_restricts_to_docs(
    gdrive_adapter: Any,
) -> None:
    """content_types=['google_doc'] → Drive API q includes doc mimeType predicate, no others."""
    connector = _make_alias_connector(
        connector_type="google_docs",
        # connector_type alone injects the preset — no explicit content_types needed
    )
    async def fake_list_files(conn: Any, folder_id: Any, **kwargs: Any) -> dict[str, Any]:  # noqa: ARG001
        return _list_response(
            [
                {
                    "id": "file-doc-1",
                    "name": "Doc",
                    "mimeType": "application/vnd.google-apps.document",
                    "modifiedTime": "2026-04-10T10:00:00.000Z",
                    "webViewLink": "https://docs.google.com/d/1",
                    "size": None,
                    "owners": [{"emailAddress": "owner@example.com"}],
                    "permissions": [],
                }
            ]
        )

    with patch.object(gdrive_adapter, "_list_files", side_effect=fake_list_files):
        refs = await gdrive_adapter.list_documents(connector, cursor_context=None)

    assert len(refs) == 1
    assert refs[0].content_type == "google_doc"


async def test_list_documents_google_sheet_filter(gdrive_adapter: Any) -> None:
    """content_types=['google_sheet'] → only spreadsheets returned."""
    connector = _make_alias_connector(connector_type="google_sheets")

    mixed_files = _list_response(
        [
            {
                "id": "file-sheet-1",
                "name": "Sheet",
                "mimeType": "application/vnd.google-apps.spreadsheet",
                "modifiedTime": "2026-04-10T10:00:00.000Z",
                "webViewLink": "https://sheets.google.com/d/1",
                "size": None,
                "owners": [],
                "permissions": [],
            },
            {
                "id": "file-doc-1",
                "name": "Doc",
                "mimeType": "application/vnd.google-apps.document",
                "modifiedTime": "2026-04-10T10:00:00.000Z",
                "webViewLink": "https://docs.google.com/d/1",
                "size": None,
                "owners": [],
                "permissions": [],
            },
        ]
    )

    with patch.object(gdrive_adapter, "_list_files", AsyncMock(return_value=mixed_files)):
        refs = await gdrive_adapter.list_documents(connector, cursor_context=None)

    assert len(refs) == 1
    assert refs[0].content_type == "google_sheet"


async def test_list_documents_google_slides_filter(gdrive_adapter: Any) -> None:
    """content_types=['google_slides'] → only presentations returned."""
    connector = _make_alias_connector(connector_type="google_slides")

    mixed_files = _list_response(
        [
            {
                "id": "file-slides-1",
                "name": "Slides",
                "mimeType": "application/vnd.google-apps.presentation",
                "modifiedTime": "2026-04-10T10:00:00.000Z",
                "webViewLink": "https://slides.google.com/d/1",
                "size": None,
                "owners": [],
                "permissions": [],
            },
            {
                "id": "file-doc-1",
                "name": "Doc",
                "mimeType": "application/vnd.google-apps.document",
                "modifiedTime": "2026-04-10T10:00:00.000Z",
                "webViewLink": "https://docs.google.com/d/1",
                "size": None,
                "owners": [],
                "permissions": [],
            },
        ]
    )

    with patch.object(gdrive_adapter, "_list_files", AsyncMock(return_value=mixed_files)):
        refs = await gdrive_adapter.list_documents(connector, cursor_context=None)

    assert len(refs) == 1
    assert refs[0].content_type == "google_slides"


async def test_list_documents_multiple_content_types(gdrive_adapter: Any) -> None:
    """content_types=['google_doc','google_sheet'] → both types returned, presentation excluded."""
    connector = _make_alias_connector(
        connector_type="google_drive",
        content_types=["google_doc", "google_sheet"],
    )

    mixed_files = _list_response(
        [
            {
                "id": "file-doc-1",
                "name": "Doc",
                "mimeType": "application/vnd.google-apps.document",
                "modifiedTime": "2026-04-10T10:00:00.000Z",
                "webViewLink": "https://docs.google.com/d/1",
                "size": None,
                "owners": [],
                "permissions": [],
            },
            {
                "id": "file-sheet-1",
                "name": "Sheet",
                "mimeType": "application/vnd.google-apps.spreadsheet",
                "modifiedTime": "2026-04-10T10:00:00.000Z",
                "webViewLink": "https://sheets.google.com/d/1",
                "size": None,
                "owners": [],
                "permissions": [],
            },
            {
                "id": "file-slides-1",
                "name": "Slides",
                "mimeType": "application/vnd.google-apps.presentation",
                "modifiedTime": "2026-04-10T10:00:00.000Z",
                "webViewLink": "https://slides.google.com/d/1",
                "size": None,
                "owners": [],
                "permissions": [],
            },
        ]
    )

    with patch.object(gdrive_adapter, "_list_files", AsyncMock(return_value=mixed_files)):
        refs = await gdrive_adapter.list_documents(connector, cursor_context=None)

    content_types = {r.content_type for r in refs}
    assert content_types == {"google_doc", "google_sheet"}


# ---------------------------------------------------------------------------
# 11. list_documents — incremental (changes) path with content_types filter
# ---------------------------------------------------------------------------


async def test_list_changes_filters_client_side_if_content_types_set(
    gdrive_adapter: Any,
) -> None:
    """Incremental sync with content_types set → non-matching mimeTypes excluded client-side."""
    connector = _make_alias_connector(connector_type="google_sheets")
    cursor = {"page_token": "cursor-abc"}

    changes_response = {
        "changes": [
            {
                "fileId": "file-sheet-1",
                "file": {
                    "id": "file-sheet-1",
                    "name": "Sheet",
                    "mimeType": "application/vnd.google-apps.spreadsheet",
                    "modifiedTime": "2026-04-10T10:00:00.000Z",
                    "webViewLink": "https://sheets.google.com/d/1",
                    "size": None,
                    "owners": [],
                    "permissions": [],
                },
            },
            {
                "fileId": "file-doc-1",
                "file": {
                    "id": "file-doc-1",
                    "name": "Doc",
                    "mimeType": "application/vnd.google-apps.document",
                    "modifiedTime": "2026-04-10T10:00:00.000Z",
                    "webViewLink": "https://docs.google.com/d/1",
                    "size": None,
                    "owners": [],
                    "permissions": [],
                },
            },
        ],
        "newStartPageToken": "next-cursor",
    }

    with patch.object(gdrive_adapter, "_list_changes", AsyncMock(return_value=changes_response)):
        refs = await gdrive_adapter.list_documents(connector, cursor_context=cursor)

    assert len(refs) == 1
    assert refs[0].ref == "file-sheet-1"
    assert refs[0].content_type == "google_sheet"


# ---------------------------------------------------------------------------
# 12. DocumentRef identifier population (SPEC R5.5)
# ---------------------------------------------------------------------------


def _file_payload(
    file_id: str = "file-1",
    mime_type: str = "application/vnd.google-apps.document",
    owners: list[dict[str, Any]] | None = None,
    permissions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": file_id,
        "name": "Test Doc",
        "mimeType": mime_type,
        "modifiedTime": "2026-04-10T10:00:00.000Z",
        "webViewLink": f"https://docs.google.com/d/{file_id}",
        "size": None,
        "owners": owners if owners is not None else [],
        "permissions": permissions if permissions is not None else [],
    }


async def test_document_ref_sender_email_from_owner(gdrive_adapter: Any) -> None:
    """owners[0].emailAddress → DocumentRef.sender_email."""
    connector = _make_alias_connector(connector_type="google_drive")
    payload = _file_payload(owners=[{"emailAddress": "owner@example.com", "displayName": "Owner"}])

    with patch.object(
        gdrive_adapter, "_list_files", AsyncMock(return_value=_list_response([payload]))
    ):
        refs = await gdrive_adapter.list_documents(connector, cursor_context=None)

    assert len(refs) == 1
    assert refs[0].sender_email == "owner@example.com"


async def test_document_ref_sender_email_empty_when_owners_missing(gdrive_adapter: Any) -> None:
    """Missing owners → sender_email == ''."""
    connector = _make_alias_connector(connector_type="google_drive")
    payload = _file_payload(owners=[])

    with patch.object(
        gdrive_adapter, "_list_files", AsyncMock(return_value=_list_response([payload]))
    ):
        refs = await gdrive_adapter.list_documents(connector, cursor_context=None)

    assert refs[0].sender_email == ""


async def test_document_ref_mentioned_emails_from_writer_permissions(
    gdrive_adapter: Any,
) -> None:
    """Permissions with role=writer or commenter → included in mentioned_emails."""
    connector = _make_alias_connector(connector_type="google_drive")
    payload = _file_payload(
        owners=[{"emailAddress": "owner@example.com"}],
        permissions=[
            {"role": "writer", "emailAddress": "writer@example.com"},
            {"role": "commenter", "emailAddress": "commenter@example.com"},
        ],
    )

    with patch.object(
        gdrive_adapter, "_list_files", AsyncMock(return_value=_list_response([payload]))
    ):
        refs = await gdrive_adapter.list_documents(connector, cursor_context=None)

    assert "writer@example.com" in refs[0].mentioned_emails
    assert "commenter@example.com" in refs[0].mentioned_emails


async def test_document_ref_mentioned_emails_excludes_readers(gdrive_adapter: Any) -> None:
    """Permissions with role=reader → NOT in mentioned_emails."""
    connector = _make_alias_connector(connector_type="google_drive")
    payload = _file_payload(
        owners=[{"emailAddress": "owner@example.com"}],
        permissions=[
            {"role": "reader", "emailAddress": "reader@example.com"},
        ],
    )

    with patch.object(
        gdrive_adapter, "_list_files", AsyncMock(return_value=_list_response([payload]))
    ):
        refs = await gdrive_adapter.list_documents(connector, cursor_context=None)

    assert "reader@example.com" not in refs[0].mentioned_emails


async def test_document_ref_mentioned_emails_dedupes(gdrive_adapter: Any) -> None:
    """Duplicate email addresses in permissions → deduplicated in mentioned_emails."""
    connector = _make_alias_connector(connector_type="google_drive")
    payload = _file_payload(
        owners=[{"emailAddress": "owner@example.com"}],
        permissions=[
            {"role": "writer", "emailAddress": "writer@example.com"},
            {"role": "commenter", "emailAddress": "writer@example.com"},  # duplicate
        ],
    )

    with patch.object(
        gdrive_adapter, "_list_files", AsyncMock(return_value=_list_response([payload]))
    ):
        refs = await gdrive_adapter.list_documents(connector, cursor_context=None)

    assert refs[0].mentioned_emails.count("writer@example.com") == 1


async def test_document_ref_mentioned_emails_excludes_sender(gdrive_adapter: Any) -> None:
    """Owner email that also appears in permissions is NOT duplicated in mentioned_emails."""
    connector = _make_alias_connector(connector_type="google_drive")
    payload = _file_payload(
        owners=[{"emailAddress": "owner@example.com"}],
        permissions=[
            {"role": "owner", "emailAddress": "owner@example.com"},
            {"role": "writer", "emailAddress": "writer@example.com"},
        ],
    )

    with patch.object(
        gdrive_adapter, "_list_files", AsyncMock(return_value=_list_response([payload]))
    ):
        refs = await gdrive_adapter.list_documents(connector, cursor_context=None)

    assert "owner@example.com" not in refs[0].mentioned_emails
    assert "writer@example.com" in refs[0].mentioned_emails


# ---------------------------------------------------------------------------
# 13. Drive API fields — owners and permissions included in requested fields
# ---------------------------------------------------------------------------


async def test_list_request_includes_owners_and_permissions_fields(
    gdrive_adapter: Any,
) -> None:
    """_LIST_FIELDS constant must include owners(emailAddress) and permissions(role,emailAddress)."""
    from app.adapters import google_drive as gd_module

    assert "owners" in gd_module._LIST_FIELDS
    assert "permissions" in gd_module._LIST_FIELDS
