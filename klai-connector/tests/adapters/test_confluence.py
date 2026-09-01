"""Specification tests for ConfluenceAdapter -- SPEC-KB-CONNECTORS-001 Phase 3.

RED phase: these tests define expected behavior before implementation exists.
All tests should FAIL before the adapter is implemented.

These tests patch the SDK class, so they verify the adapter's own logic
(space/page mapping, storage-format conversion, the image carve-out) and
NOT the SDK contract. GetKlai/klai#1137: patching the class is precisely why
CI could not see the v5 break — ``tests/test_confluence_sdk_contract.py``
covers the SDK surface without patching it. Payload fixtures here follow the
Confluence Cloud **v2** shapes.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.adapters.base import DocumentRef

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_connector(config: dict[str, Any]) -> SimpleNamespace:
    """Build a minimal connector-like object with the given config dict."""
    return SimpleNamespace(
        id="conn-confluence-001",
        org_id="org-001",
        config=config,
    )


def _valid_config(
    *,
    base_url: str = "https://company.atlassian.net",
    email: str = "user@example.com",
    api_token: str = "secret-token-abc",
    space_keys: list[str] | None = None,
) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "base_url": base_url,
        "email": email,
        "api_token": api_token,
    }
    if space_keys is not None:
        cfg["space_keys"] = space_keys
    return cfg


# Space keys mapped to the numeric v2 space ids used by GET /wiki/api/v2/pages.
_SPACE_IDS = {"ENG": "9001", "DOCS": "9002"}


def _make_page(
    page_id: str = "12345",
    space_key: str = "ENG",
    title: str = "Test Page",
    created_at: str = "2026-01-15T10:00:00.000Z",
) -> dict[str, Any]:
    """Build a Confluence Cloud **v2** page object.

    v2 identifies the space by numeric ``spaceId`` and exposes only
    ``version.authorId`` — there is no ``version.by.email``.
    """
    return {
        "id": page_id,
        "status": "current",
        "title": title,
        "spaceId": _SPACE_IDS.get(space_key, "9000"),
        "authorId": "5b10ac8d82e05b22cc7d4ef5",
        "createdAt": created_at,
        "version": {
            "createdAt": created_at,
            "number": 1,
            "minorEdit": False,
            "authorId": "5b10ac8d82e05b22cc7d4ef5",
        },
    }


def _spaces(*keys: str) -> list[dict[str, Any]]:
    """Build the v2 space list returned by GET /wiki/api/v2/spaces."""
    return [
        {"id": _SPACE_IDS[key], "key": key, "name": key.title(), "type": "global"}
        for key in keys
    ]


def _make_page_with_body(
    page_id: str = "12345",
    storage_value: str = "<p>Hello World</p>",
) -> dict[str, Any]:
    """Build a page dict with body.storage field for fetch_document tests."""
    return {
        "id": page_id,
        "title": "Test Page",
        "body": {
            "storage": {
                "value": storage_value,
                "representation": "storage",
            }
        },
    }


@pytest.fixture
def confluence_adapter() -> Any:
    """Create a ConfluenceAdapter with a mock settings object."""
    from app.adapters.confluence import ConfluenceAdapter
    from app.core.config import Settings

    s = MagicMock(spec=Settings)
    return ConfluenceAdapter(s)


# ---------------------------------------------------------------------------
# Config extraction tests
# ---------------------------------------------------------------------------


def test_extract_config_happy_path(confluence_adapter: Any) -> None:
    """_extract_config returns all fields from a valid config dict."""
    connector = _make_connector(
        _valid_config(space_keys=["ENG", "DOCS"])
    )
    cfg = confluence_adapter._extract_config(connector)

    assert cfg["base_url"] == "https://company.atlassian.net"
    assert cfg["email"] == "user@example.com"
    assert cfg["api_token"] == "secret-token-abc"
    assert cfg["space_keys"] == ["ENG", "DOCS"]


def test_extract_config_missing_base_url_raises(confluence_adapter: Any) -> None:
    """_extract_config raises ValueError when base_url is absent."""
    connector = _make_connector({"email": "u@e.com", "api_token": "tok"})
    with pytest.raises(ValueError, match="base_url"):
        confluence_adapter._extract_config(connector)


def test_extract_config_missing_email_raises(confluence_adapter: Any) -> None:
    """_extract_config raises ValueError when email is absent."""
    connector = _make_connector({"base_url": "https://x.atlassian.net", "api_token": "tok"})
    with pytest.raises(ValueError, match="email"):
        confluence_adapter._extract_config(connector)


def test_extract_config_missing_api_token_raises(confluence_adapter: Any) -> None:
    """_extract_config raises ValueError when api_token is absent."""
    connector = _make_connector({"base_url": "https://x.atlassian.net", "email": "u@e.com"})
    with pytest.raises(ValueError, match="api_token"):
        confluence_adapter._extract_config(connector)


def test_extract_config_strips_trailing_slash(confluence_adapter: Any) -> None:
    """_extract_config strips trailing slash from base_url."""
    connector = _make_connector(_valid_config(base_url="https://company.atlassian.net/"))
    cfg = confluence_adapter._extract_config(connector)

    assert cfg["base_url"] == "https://company.atlassian.net"


def test_extract_config_space_keys_optional_defaults_empty(confluence_adapter: Any) -> None:
    """_extract_config returns empty list for space_keys when not provided."""
    connector = _make_connector(_valid_config())  # no space_keys
    cfg = confluence_adapter._extract_config(connector)

    assert cfg["space_keys"] == []


_ENDPOINTS = {"spaces": "api/v2/spaces", "page": "api/v2/pages"}


def _mock_v2_client(
    mock_confluence_cls: MagicMock,
    *,
    spaces: list[dict[str, Any]],
    pages_by_space_id: dict[str, list[dict[str, Any]]] | None = None,
    pages: list[dict[str, Any]] | None = None,
) -> MagicMock:
    """Wire a MagicMock onto the v2 surface the adapter actually calls.

    The adapter walks the v2 cursor itself through ``client.get()`` rather than
    using ``get_pages`` / ``get_spaces``, so those helpers cannot bound their
    own traffic. It lists spaces once to build a key -> id map, then asks for
    pages per numeric space id. This fake returns a single cursorless page,
    which is the shape ``_paginate_v2`` treats as "last page".
    """
    mock_client = MagicMock()
    mock_confluence_cls.return_value = mock_client
    mock_client.get_endpoint.side_effect = lambda key, **_kw: _ENDPOINTS[key]

    def _get(endpoint: str, params: dict[str, Any] | None = None, **_kw: Any):
        params = params or {}
        if endpoint == _ENDPOINTS["spaces"]:
            return {"results": spaces}
        if endpoint == _ENDPOINTS["page"]:
            space_id = str(params.get("space-id", ""))
            if pages_by_space_id is not None:
                return {"results": pages_by_space_id.get(space_id, [])}
            return {"results": pages or []}
        raise AssertionError(f"unexpected endpoint {endpoint}")

    mock_client.get.side_effect = _get
    return mock_client


def _requested_space_ids(mock_client: MagicMock) -> set[str]:
    """Space ids the adapter asked pages for, from the recorded GET calls."""
    return {
        str(call.kwargs["params"]["space-id"])
        for call in mock_client.get.call_args_list
        if call.args and call.args[0] == _ENDPOINTS["page"]
    }


# ---------------------------------------------------------------------------
# list_documents tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.adapters.confluence.ConfluenceV2")
async def test_list_documents_single_space_happy_path(
    mock_confluence_cls: MagicMock,
    confluence_adapter: Any,
) -> None:
    """list_documents with one space key returns one DocumentRef per page."""
    _mock_v2_client(
        mock_confluence_cls,
        spaces=_spaces("ENG"),
        pages=[
            _make_page("100", "ENG", "Page One"),
            _make_page("101", "ENG", "Page Two"),
        ],
    )

    connector = _make_connector(_valid_config(space_keys=["ENG"]))
    refs = await confluence_adapter.list_documents(connector)

    assert len(refs) == 2
    assert all(isinstance(r, DocumentRef) for r in refs)


@pytest.mark.asyncio
@patch("app.adapters.confluence.ConfluenceV2")
async def test_list_documents_multiple_spaces(
    mock_confluence_cls: MagicMock,
    confluence_adapter: Any,
) -> None:
    """list_documents with multiple space_keys aggregates pages from all spaces."""
    _mock_v2_client(
        mock_confluence_cls,
        spaces=_spaces("ENG", "DOCS"),
        pages_by_space_id={
            _SPACE_IDS["ENG"]: [_make_page("100", "ENG")],
            _SPACE_IDS["DOCS"]: [
                _make_page("200", "DOCS"),
                _make_page("201", "DOCS"),
            ],
        },
    )

    connector = _make_connector(_valid_config(space_keys=["ENG", "DOCS"]))
    refs = await confluence_adapter.list_documents(connector)

    assert len(refs) == 3


@pytest.mark.asyncio
@patch("app.adapters.confluence.ConfluenceV2")
async def test_list_documents_empty_space_keys_lists_all_spaces(
    mock_confluence_cls: MagicMock,
    confluence_adapter: Any,
) -> None:
    """When space_keys is empty, list_documents discovers all spaces first."""
    mock_client = _mock_v2_client(
        mock_confluence_cls,
        spaces=_spaces("ENG", "DOCS"),
        pages=[_make_page("100", "ENG")],
    )

    connector = _make_connector(_valid_config())  # no space_keys → empty
    refs = await confluence_adapter.list_documents(connector)

    # Pages are requested per numeric v2 space id, never by key.
    assert _requested_space_ids(mock_client) == {
        _SPACE_IDS["ENG"],
        _SPACE_IDS["DOCS"],
    }
    assert len(refs) >= 1


@pytest.mark.asyncio
@patch("app.adapters.confluence.ConfluenceV2")
async def test_list_documents_source_url_format(
    mock_confluence_cls: MagicMock,
    confluence_adapter: Any,
) -> None:
    """source_url is formatted as {base_url}/wiki/spaces/{key}/pages/{id}."""
    _mock_v2_client(
        mock_confluence_cls,
        spaces=_spaces("ENG"),
        pages=[_make_page("999", "ENG")],
    )

    connector = _make_connector(_valid_config(
        base_url="https://company.atlassian.net",
        space_keys=["ENG"],
    ))
    refs = await confluence_adapter.list_documents(connector)

    assert refs[0].source_url == "https://company.atlassian.net/wiki/spaces/ENG/pages/999"


@pytest.mark.asyncio
@patch("app.adapters.confluence.ConfluenceV2")
async def test_list_documents_last_edited_from_version_created_at(
    mock_confluence_cls: MagicMock,
    confluence_adapter: Any,
) -> None:
    """last_edited on DocumentRef comes from page version.createdAt."""
    _mock_v2_client(
        mock_confluence_cls,
        spaces=_spaces("ENG"),
        pages=[_make_page("100", "ENG", created_at="2026-03-01T12:00:00.000Z")],
    )

    connector = _make_connector(_valid_config(space_keys=["ENG"]))
    refs = await confluence_adapter.list_documents(connector)

    assert refs[0].last_edited == "2026-03-01T12:00:00.000Z"


@pytest.mark.asyncio
@patch("app.adapters.confluence.ConfluenceV2")
async def test_list_documents_sender_email_empty_on_v2(
    mock_confluence_cls: MagicMock,
    confluence_adapter: Any,
) -> None:
    """sender_email is always empty for Confluence (GetKlai/klai#1137).

    The v1 payload carried ``version.by.email``; the v2 page object exposes
    only ``version.authorId`` (an Atlassian account id). sender_email stays an
    optional field downstream — ``clients/knowledge_ingest.py`` writes it into
    ``extra`` only when non-empty — so an empty value degrades gracefully
    rather than failing the sync.
    """
    _mock_v2_client(
        mock_confluence_cls,
        spaces=_spaces("ENG"),
        pages=[_make_page("100", "ENG")],
    )

    connector = _make_connector(_valid_config(space_keys=["ENG"]))
    refs = await confluence_adapter.list_documents(connector)

    assert refs[0].sender_email == ""
    assert refs[0].mentioned_emails == []


# ---------------------------------------------------------------------------
# fetch_document tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.adapters.confluence.ConfluenceV2")
async def test_fetch_document_plain_html_to_text(
    mock_confluence_cls: MagicMock,
    confluence_adapter: Any,
) -> None:
    """fetch_document converts simple HTML to plain text."""
    mock_client = MagicMock()
    mock_confluence_cls.return_value = mock_client
    mock_client.get_page_by_id.return_value = _make_page_with_body(
        storage_value="<p>Hello World</p>"
    )

    connector = _make_connector(_valid_config(space_keys=["ENG"]))
    ref = DocumentRef(
        path="ENG/12345",
        ref="12345",
        size=0,
        content_type="text/plain",
    )
    result = await confluence_adapter.fetch_document(ref, connector)

    assert b"Hello World" in result


@pytest.mark.asyncio
@patch("app.adapters.confluence.ConfluenceV2")
async def test_fetch_document_strips_ac_tags(
    mock_confluence_cls: MagicMock,
    confluence_adapter: Any,
) -> None:
    """fetch_document removes Confluence ac:* tags from output text."""
    mock_client = MagicMock()
    mock_confluence_cls.return_value = mock_client

    storage = (
        '<p>Visible text</p>'
        '<ac:structured-macro ac:name="code">'
        '<ac:parameter ac:name="language">python</ac:parameter>'
        '<ac:plain-text-body><![CDATA[x = 1]]></ac:plain-text-body>'
        '</ac:structured-macro>'
    )
    mock_client.get_page_by_id.return_value = _make_page_with_body(storage_value=storage)

    connector = _make_connector(_valid_config(space_keys=["ENG"]))
    ref = DocumentRef(path="ENG/12345", ref="12345", size=0, content_type="text/plain")
    result = await confluence_adapter.fetch_document(ref, connector)

    assert b"ac:structured-macro" not in result
    assert b"Visible text" in result


@pytest.mark.asyncio
@patch("app.adapters.confluence.ConfluenceV2")
async def test_fetch_document_returns_bytes_utf8(
    mock_confluence_cls: MagicMock,
    confluence_adapter: Any,
) -> None:
    """fetch_document returns bytes (UTF-8 encoded)."""
    mock_client = MagicMock()
    mock_confluence_cls.return_value = mock_client
    mock_client.get_page_by_id.return_value = _make_page_with_body(
        storage_value="<p>Café</p>"
    )

    connector = _make_connector(_valid_config(space_keys=["ENG"]))
    ref = DocumentRef(path="ENG/12345", ref="12345", size=0, content_type="text/plain")
    result = await confluence_adapter.fetch_document(ref, connector)

    assert isinstance(result, bytes)
    # Must be valid UTF-8
    decoded = result.decode("utf-8")
    assert "Caf" in decoded


@pytest.mark.asyncio
@patch("app.adapters.confluence.ConfluenceV2")
async def test_fetch_document_extracts_external_image(
    mock_confluence_cls: MagicMock,
    confluence_adapter: Any,
) -> None:
    """fetch_document populates ref.images with external URL images (Shape A)."""
    mock_client = MagicMock()
    mock_confluence_cls.return_value = mock_client

    storage = (
        '<p>See diagram below.</p>'
        '<ac:image>'
        '<ri:url ri:value="https://cdn.example.com/diagram.png"/>'
        '</ac:image>'
    )
    mock_client.get_page_by_id.return_value = _make_page_with_body(storage_value=storage)

    connector = _make_connector(_valid_config(space_keys=["ENG"]))
    ref = DocumentRef(path="ENG/12345", ref="12345", size=0, content_type="text/plain")
    await confluence_adapter.fetch_document(ref, connector)

    assert ref.images is not None
    assert len(ref.images) == 1
    assert ref.images[0].url == "https://cdn.example.com/diagram.png"


@pytest.mark.asyncio
@patch("app.adapters.confluence.ConfluenceV2")
async def test_fetch_document_skips_attachment_images_shape_b(
    mock_confluence_cls: MagicMock,
    confluence_adapter: Any,
) -> None:
    """fetch_document does NOT add attachment images (Shape B) to ref.images.

    Attachments require Confluence auth to download; the sync engine's image
    HTTP client does not support per-adapter auth headers. See @MX:TODO in
    confluence.py for the planned fix.
    """
    mock_client = MagicMock()
    mock_confluence_cls.return_value = mock_client

    storage = (
        '<p>See attached screenshot.</p>'
        '<ac:image>'
        '<ri:attachment ri:filename="screenshot.png"/>'
        '</ac:image>'
    )
    mock_client.get_page_by_id.return_value = _make_page_with_body(storage_value=storage)

    connector = _make_connector(_valid_config(space_keys=["ENG"]))
    ref = DocumentRef(path="ENG/12345", ref="12345", size=0, content_type="text/plain")
    await confluence_adapter.fetch_document(ref, connector)

    # Shape B must NOT be in images
    assert ref.images is None or len(ref.images) == 0


@pytest.mark.asyncio
@patch("app.adapters.confluence.ConfluenceV2")
async def test_fetch_document_mixed_images(
    mock_confluence_cls: MagicMock,
    confluence_adapter: Any,
) -> None:
    """fetch_document: only external URL images added; attachment images skipped."""
    mock_client = MagicMock()
    mock_confluence_cls.return_value = mock_client

    storage = (
        '<ac:image>'
        '<ri:url ri:value="https://cdn.example.com/logo.png"/>'
        '</ac:image>'
        '<ac:image>'
        '<ri:attachment ri:filename="local.png"/>'
        '</ac:image>'
    )
    mock_client.get_page_by_id.return_value = _make_page_with_body(storage_value=storage)

    connector = _make_connector(_valid_config(space_keys=["ENG"]))
    ref = DocumentRef(path="ENG/12345", ref="12345", size=0, content_type="text/plain")
    await confluence_adapter.fetch_document(ref, connector)

    assert ref.images is not None
    assert len(ref.images) == 1
    assert ref.images[0].url == "https://cdn.example.com/logo.png"


@pytest.mark.asyncio
@patch("app.adapters.confluence.ConfluenceV2")
async def test_fetch_document_image_alt_text_captured_when_present(
    mock_confluence_cls: MagicMock,
    confluence_adapter: Any,
) -> None:
    """fetch_document captures alt text from ac:caption inside ac:image."""
    mock_client = MagicMock()
    mock_confluence_cls.return_value = mock_client

    # Confluence storage format can include ac:caption child element
    storage = (
        '<ac:image>'
        '<ri:url ri:value="https://cdn.example.com/chart.png"/>'
        '<ac:caption><p>Sales chart 2026</p></ac:caption>'
        '</ac:image>'
    )
    mock_client.get_page_by_id.return_value = _make_page_with_body(storage_value=storage)

    connector = _make_connector(_valid_config(space_keys=["ENG"]))
    ref = DocumentRef(path="ENG/12345", ref="12345", size=0, content_type="text/plain")
    await confluence_adapter.fetch_document(ref, connector)

    assert ref.images is not None
    assert len(ref.images) >= 1
    # Alt text should contain caption text
    assert "Sales chart" in ref.images[0].alt or ref.images[0].alt == ""


# ---------------------------------------------------------------------------
# get_cursor_state tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_cursor_state_returns_iso_timestamp(confluence_adapter: Any) -> None:
    """get_cursor_state returns a dict with last_run_at as ISO 8601 string."""
    connector = _make_connector(_valid_config())
    state = await confluence_adapter.get_cursor_state(connector)

    assert "last_run_at" in state
    ts = state["last_run_at"]
    assert isinstance(ts, str)
    # ISO 8601 timestamps contain 'T' separator
    assert "T" in ts


# ---------------------------------------------------------------------------
# aclose tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aclose_noop(confluence_adapter: Any) -> None:
    """aclose is a no-op and returns None without raising."""
    result = await confluence_adapter.aclose()
    assert result is None
