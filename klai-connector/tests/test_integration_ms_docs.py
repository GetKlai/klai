"""End-to-end integration test for MsDocsAdapter via AdapterRegistry.

SPEC-KB-MS-DOCS-001 — verifies that a connector registered under the
``ms_docs`` key produces the right DocumentRef shape, adapter-owned
metadata, and cursor state on a full list → fetch → cursor roundtrip
against mocked Microsoft Graph responses.

Distinct from ``tests/adapters/test_ms_docs.py`` which unit-tests the
adapter in isolation. This test exercises the registration path used
by ``main.py``.
"""

# ruff: noqa: S106  -- test-only placeholder token strings

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.adapters.base import DocumentRef
from app.adapters.ms_docs import MsDocsAdapter
from app.adapters.registry import AdapterRegistry


@pytest.fixture
def settings() -> MagicMock:
    s = MagicMock()
    s.ms_docs_client_id = "placeholder-client-id"
    s.ms_docs_client_secret = "placeholder-client-secret"
    s.ms_docs_tenant_id = "common"
    return s


@pytest.fixture
def portal_client() -> MagicMock:
    pc = MagicMock()
    pc.update_credentials = AsyncMock()
    return pc


@pytest.fixture
def registry_with_ms_docs(
    settings: MagicMock, portal_client: MagicMock,
) -> tuple[AdapterRegistry, MsDocsAdapter]:
    """AdapterRegistry with ms_docs registered exactly as main.py does it."""
    registry = AdapterRegistry()
    adapter = MsDocsAdapter(settings=settings, portal_client=portal_client)
    registry.register("ms_docs", adapter)
    # Seed token cache so tests don't hit HTTP
    adapter._cache_token("msdocs-integration", "placeholder-access-value", expires_in_seconds=3600.0)
    return registry, adapter


def _make_connector(config: dict[str, Any] | None = None) -> SimpleNamespace:
    cfg: dict[str, Any] = {
        "access_token": "placeholder-access-value",
        "refresh_token": "placeholder-refresh-value",
    }
    if config:
        cfg.update(config)
    return SimpleNamespace(id="msdocs-integration", org_id="org-integration", config=cfg)


@pytest.mark.asyncio
async def test_registry_returns_ms_docs_adapter_for_connector_type(
    registry_with_ms_docs: tuple[AdapterRegistry, MsDocsAdapter],
) -> None:
    """registry.get("ms_docs") returns the registered MsDocsAdapter instance."""
    registry, adapter = registry_with_ms_docs

    result = registry.get("ms_docs")

    assert result is adapter
    assert isinstance(result, MsDocsAdapter)


@pytest.mark.asyncio
async def test_full_sync_roundtrip_produces_document_refs_with_metadata(
    registry_with_ms_docs: tuple[AdapterRegistry, MsDocsAdapter],
) -> None:
    """Full list → fetch → cursor flow via the registry.

    Verifies the sync-engine contract end-to-end:
      1. registry.get("ms_docs").list_documents(connector) returns DocumentRefs
      2. Each DocumentRef carries source_url, source_ref, last_edited from Graph
      3. adapter._get_metadata_for_ref surfaces sender_email + mentioned_emails
      4. fetch_document returns the binary content
      5. get_cursor_state returns a delta_link suitable for the next run
    """
    registry, adapter = registry_with_ms_docs
    connector = _make_connector()

    # Mock Graph responses:
    # - list_documents: one delta response with two items + deltaLink
    # - fetch_document: content bytes
    delta_response = {
        "value": [
            {
                "id": "item-docx-1",
                "name": "Q2 Report.docx",
                "webUrl": "https://contoso.sharepoint.com/personal/alice/Documents/Q2%20Report.docx",
                "size": 45678,
                "lastModifiedDateTime": "2026-04-20T10:00:00Z",
                "file": {"mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
                "createdBy": {"user": {"email": "alice@contoso.com"}},
                "lastModifiedBy": {"user": {"email": "bob@contoso.com"}},
            },
            {
                "id": "item-pdf-1",
                "name": "Policy.pdf",
                "webUrl": "https://contoso.sharepoint.com/personal/alice/Documents/Policy.pdf",
                "size": 123456,
                "lastModifiedDateTime": "2026-04-19T14:30:00Z",
                "file": {"mimeType": "application/pdf"},
                "createdBy": {"user": {"email": "carol@contoso.com"}},
                "lastModifiedBy": {"user": {"email": "carol@contoso.com"}},
            },
        ],
        "@odata.deltaLink": "https://graph.microsoft.com/v1.0/me/drive/root/delta?token=final-cursor",
    }

    async def fake_get_json(url: str, connector: Any = None) -> dict[str, Any]:
        return delta_response

    async def fake_get_bytes(url: str, connector: Any = None) -> bytes:
        assert "/drive/items/item-docx-1/content" in url
        return b"FAKE_DOCX_CONTENT"

    with (
        patch.object(adapter, "_graph_get_json", side_effect=fake_get_json),
        patch.object(adapter, "_graph_get_bytes", side_effect=fake_get_bytes),
    ):
        # 1. Resolve adapter via registry
        resolved_adapter = registry.get("ms_docs")

        # 2. List documents
        refs = await resolved_adapter.list_documents(connector, cursor_context=None)
        assert len(refs) == 2
        assert all(isinstance(r, DocumentRef) for r in refs)

        docx_ref = next(r for r in refs if r.ref == "item-docx-1")
        pdf_ref = next(r for r in refs if r.ref == "item-pdf-1")

        # DocumentRef contract (R2.5, R2.6)
        assert docx_ref.content_type == "word_document"
        assert docx_ref.source_url == "https://contoso.sharepoint.com/personal/alice/Documents/Q2%20Report.docx"
        assert docx_ref.source_ref == "item-docx-1"
        assert docx_ref.last_edited == "2026-04-20T10:00:00Z"
        assert docx_ref.size == 45678
        assert pdf_ref.content_type == "pdf_document"

        # 3. Adapter-owned metadata (R2.10 identifier capture)
        # Cast so pyright knows we have the MsDocsAdapter methods.
        assert isinstance(resolved_adapter, MsDocsAdapter)
        docx_meta = resolved_adapter._get_metadata_for_ref(docx_ref)
        assert docx_meta["sender_email"] == "bob@contoso.com"
        assert set(docx_meta["mentioned_emails"]) == {"alice@contoso.com", "bob@contoso.com"}

        pdf_meta = resolved_adapter._get_metadata_for_ref(pdf_ref)
        assert pdf_meta["sender_email"] == "carol@contoso.com"
        # createdBy and lastModifiedBy are the same → single entry after dedup
        assert pdf_meta["mentioned_emails"] == ["carol@contoso.com"]

        # 4. Fetch document content
        content = await resolved_adapter.fetch_document(docx_ref, connector)
        assert content == b"FAKE_DOCX_CONTENT"

        # 5. Cursor state after list ready for next run
        cursor = await resolved_adapter.get_cursor_state(connector)
        assert cursor == {"delta_link": "https://graph.microsoft.com/v1.0/me/drive/root/delta?token=final-cursor"}


@pytest.mark.asyncio
async def test_incremental_sync_uses_persisted_delta_link(
    registry_with_ms_docs: tuple[AdapterRegistry, MsDocsAdapter],
) -> None:
    """Second sync reads cursor from get_cursor_state and calls that URL verbatim."""
    registry, adapter = registry_with_ms_docs
    connector = _make_connector()

    stored_cursor = "https://graph.microsoft.com/v1.0/me/drive/root/delta?token=stored"
    new_cursor = "https://graph.microsoft.com/v1.0/me/drive/root/delta?token=new"

    incremental_response = {
        "value": [
            {
                "id": "item-changed",
                "name": "Updated.xlsx",
                "webUrl": "https://contoso.sharepoint.com/personal/alice/Documents/Updated.xlsx",
                "size": 9999,
                "lastModifiedDateTime": "2026-04-21T09:00:00Z",
                "file": {"mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
                "createdBy": {"user": {"email": "alice@contoso.com"}},
                "lastModifiedBy": {"user": {"email": "alice@contoso.com"}},
            },
        ],
        "@odata.deltaLink": new_cursor,
    }
    call_urls: list[str] = []

    async def fake_get_json(url: str, connector: Any = None) -> dict[str, Any]:
        call_urls.append(url)
        return incremental_response

    with patch.object(adapter, "_graph_get_json", side_effect=fake_get_json):
        resolved = registry.get("ms_docs")
        assert isinstance(resolved, MsDocsAdapter)
        refs = await resolved.list_documents(
            connector, cursor_context={"delta_link": stored_cursor}
        )

    # The persisted delta_link is called verbatim, no reconstruction
    assert call_urls == [stored_cursor]
    assert len(refs) == 1
    assert refs[0].content_type == "excel_document"
    # New cursor is persisted for next run
    assert adapter._latest_delta_link["msdocs-integration"] == new_cursor


@pytest.mark.asyncio
async def test_refresh_token_rotation_propagates_through_portal_client(
    settings: MagicMock, portal_client: MagicMock,
) -> None:
    """SPEC-KB-MS-DOCS-001 R9.2: rotated refresh_token flows from adapter → portal_client.

    This exercises the OAuthAdapterBase path via the concrete MsDocsAdapter.
    """
    adapter = MsDocsAdapter(settings=settings, portal_client=portal_client)
    registry = AdapterRegistry()
    registry.register("ms_docs", adapter)

    connector = _make_connector()

    # Mock the raw token-endpoint call inside _refresh_oauth_token
    token_response = MagicMock()
    token_response.json = MagicMock(
        return_value={
            "access_token": "placeholder-new-access",
            "expires_in": 3600,
            "refresh_token": "placeholder-rotated-refresh",
        }
    )
    token_response.raise_for_status = MagicMock(return_value=None)

    http_client = MagicMock()
    http_client.post = AsyncMock(return_value=token_response)
    http_client.__aenter__ = AsyncMock(return_value=http_client)
    http_client.__aexit__ = AsyncMock(return_value=None)

    with patch("app.adapters.ms_docs.httpx.AsyncClient", MagicMock(return_value=http_client)):
        # First ensure_token triggers refresh (cache is empty → hits token endpoint)
        resolved = registry.get("ms_docs")
        assert isinstance(resolved, MsDocsAdapter)
        access_token = await resolved.ensure_token(connector)

    # Adapter returns the new access_token
    assert access_token == "placeholder-new-access"
    # Portal writeback includes the rotated refresh_token (R9)
    portal_client.update_credentials.assert_awaited_once()
    kwargs = portal_client.update_credentials.await_args.kwargs
    assert kwargs["access_token"] == "placeholder-new-access"
    assert kwargs["refresh_token"] == "placeholder-rotated-refresh"
    # connector.config is mutated in-memory so subsequent refreshes use the new RT
    assert connector.config["refresh_token"] == "placeholder-rotated-refresh"


@pytest.mark.asyncio
async def test_registry_aclose_closes_ms_docs_adapter(
    registry_with_ms_docs: tuple[AdapterRegistry, MsDocsAdapter],
) -> None:
    """Registry shutdown calls aclose on the MsDocsAdapter (no exception)."""
    registry, _adapter = registry_with_ms_docs
    # MsDocsAdapter.aclose is a no-op but must be callable
    await registry.aclose()
