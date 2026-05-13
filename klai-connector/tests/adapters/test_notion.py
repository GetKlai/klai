"""Specification tests for NotionAdapter -- SPEC-KB-019.

Originally written RED-phase against an async-first prototype adapter.
The adapter has since been refactored:
  * Page search uses `_search_all_pages` (sync, called via `asyncio.to_thread`)
  * Block fetch uses module-level `fetch_blocks_recursive`
  * Cursor helper is `_get_max_edited` (sync)
  * Rate-limit retry moved into `RateLimitedNotionClient._execute_with_retry`
    (external `notion_sync_lib` package -- not covered here by design)
  * `list_documents` always returns the full page set; the sync engine
    handles reconciliation against `cursor_context`.

Tests below target the current adapter contract.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.adapters.base import DocumentRef

from .conftest import make_page

# ---------------------------------------------------------------------------
# 1. list_documents -- first sync (no cursor_context)
# ---------------------------------------------------------------------------


async def test_list_documents_first_sync(
    notion_adapter: Any,
    mock_connector: SimpleNamespace,
) -> None:
    """R7: First sync (no cursor_context) returns all accessible pages."""
    pages = [
        make_page("page-001", "Page One", "2026-04-01T10:00:00.000Z"),
        make_page("page-002", "Page Two", "2026-04-02T12:00:00.000Z"),
    ]
    mock_search = MagicMock(return_value=pages)

    with patch.object(notion_adapter, "_search_all_pages", mock_search):
        refs = await notion_adapter.list_documents(mock_connector, cursor_context=None)

    assert len(refs) == 2
    assert all(isinstance(r, DocumentRef) for r in refs)
    assert refs[0].ref == "page-001"
    assert refs[0].source_ref == "page-001"
    assert refs[0].content_type == "notion_page"
    assert refs[0].path != ""


# ---------------------------------------------------------------------------
# 2. list_documents -- cursor_context ignored at adapter layer
# ---------------------------------------------------------------------------


async def test_list_documents_incremental_sync(
    notion_adapter: Any,
    mock_connector: SimpleNamespace,
) -> None:
    """R8: Adapter returns the full page set with last_edited populated.

    The sync engine handles reconciliation against cursor_context by comparing
    each ref's last_edited timestamp. The adapter itself no longer filters.
    """
    old_page = make_page("page-old", "Old", "2026-03-01T00:00:00.000Z")
    new_page = make_page("page-new", "New", "2026-04-03T15:00:00.000Z")

    mock_search = MagicMock(return_value=[old_page, new_page])

    cursor = {"last_synced_at": "2026-04-01T00:00:00.000Z"}
    with patch.object(notion_adapter, "_search_all_pages", mock_search):
        refs = await notion_adapter.list_documents(mock_connector, cursor_context=cursor)

    assert len(refs) == 2
    assert {r.ref for r in refs} == {"page-old", "page-new"}
    by_ref = {r.ref: r for r in refs}
    assert by_ref["page-old"].last_edited == "2026-03-01T00:00:00.000Z"
    assert by_ref["page-new"].last_edited == "2026-04-03T15:00:00.000Z"


# ---------------------------------------------------------------------------
# 3. list_documents -- respects max_pages
# ---------------------------------------------------------------------------


async def test_list_documents_respects_max_pages(
    notion_adapter: Any,
) -> None:
    """R10: max_pages config is forwarded to _search_all_pages.

    _search_all_pages enforces the limit internally. Here we verify the
    configured value is passed through and that list_documents returns
    whatever the helper produced.
    """
    connector = SimpleNamespace(
        id="conn-001",
        org_id="org-001",
        config={"access_token": "secret_tok", "database_ids": [], "max_pages": 2},
    )

    pages = [make_page(f"page-{i}", f"Page {i}") for i in range(2)]
    mock_search = MagicMock(return_value=pages)

    with patch.object(notion_adapter, "_search_all_pages", mock_search):
        refs = await notion_adapter.list_documents(connector, cursor_context=None)

    assert len(refs) == 2
    # signature: _search_all_pages(client, max_pages, database_ids)
    args, _ = mock_search.call_args
    assert args[1] == 2


# ---------------------------------------------------------------------------
# 4. fetch_document -- returns bytes
# ---------------------------------------------------------------------------


async def test_fetch_document_returns_bytes(
    notion_adapter: Any,
    mock_connector: SimpleNamespace,
) -> None:
    """R3: fetch_document returns page content as bytes."""
    ref = DocumentRef(
        path="Page One",
        ref="page-001",
        size=0,
        content_type="notion_page",
        source_ref="page-001",
    )

    blocks = [
        {
            "object": "block",
            "id": "block-001",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": "Hello world"}}]
            },
        }
    ]

    with patch("app.adapters.notion.fetch_blocks_recursive", return_value=blocks):
        content = await notion_adapter.fetch_document(ref, mock_connector)

    assert isinstance(content, bytes)
    assert len(content) > 0


# ---------------------------------------------------------------------------
# 5. get_cursor_state -- returns ISO8601
# ---------------------------------------------------------------------------


async def test_get_cursor_state_returns_iso8601(
    notion_adapter: Any,
    mock_connector: SimpleNamespace,
) -> None:
    """R4/R9: get_cursor_state returns {last_synced_at: ISO8601}."""
    mock_max_edited = MagicMock(return_value="2026-04-03T15:30:00.000Z")

    with patch.object(notion_adapter, "_get_max_edited", mock_max_edited):
        state = await notion_adapter.get_cursor_state(mock_connector)

    assert "last_synced_at" in state
    assert state["last_synced_at"] == "2026-04-03T15:30:00.000Z"


# ---------------------------------------------------------------------------
# 6. Config validation -- missing token
# ---------------------------------------------------------------------------


async def test_config_validation_missing_token(
    notion_adapter: Any,
    mock_connector_no_token: SimpleNamespace,
) -> None:
    """R10: Missing access_token raises ValueError with clear message."""
    with pytest.raises(ValueError, match="access_token"):
        await notion_adapter.list_documents(mock_connector_no_token)


# ---------------------------------------------------------------------------
# 7. Config validation -- defaults
# ---------------------------------------------------------------------------


def test_config_validation_uses_defaults(notion_adapter: Any) -> None:
    """R10: max_pages defaults to 500, database_ids defaults to empty list."""
    connector = SimpleNamespace(
        id="c1",
        org_id="o1",
        config={"access_token": "secret_x"},
    )
    config = notion_adapter._extract_config(connector)
    assert config["max_pages"] == 500
    assert config["database_ids"] == []


# ---------------------------------------------------------------------------
# 8. Token not logged
# ---------------------------------------------------------------------------


async def test_token_not_logged(
    notion_adapter: Any,
    mock_connector: SimpleNamespace,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """NF: access_token MUST NOT appear in log output."""
    pages = [make_page("page-001")]
    mock_search = MagicMock(return_value=pages)

    with (
        caplog.at_level(logging.DEBUG),
        patch.object(notion_adapter, "_search_all_pages", mock_search),
    ):
        await notion_adapter.list_documents(mock_connector)

    full_log = caplog.text
    assert "secret_abc123notiontoken" not in full_log


# ---------------------------------------------------------------------------
# 9. Rate limit backoff on 429 -- covered by external notion_sync_lib
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason=(
        "Retry / 429 backoff lives in RateLimitedNotionClient._execute_with_retry "
        "inside the external notion_sync_lib package. Industry-standard practice "
        "is to trust the upstream dependency's own test suite rather than mock "
        "its internals here."
    )
)
async def test_rate_limit_backoff(
    notion_adapter: Any,
    mock_connector: SimpleNamespace,
) -> None:
    """Placeholder: retry behavior covered in the external notion_sync_lib."""


# ---------------------------------------------------------------------------
# N. fetch_document populates ref.images directly (no side-channel cache)
# ---------------------------------------------------------------------------


def _image_block(block_id: str, url: str, caption: str = "") -> dict[str, Any]:
    """Build a Notion image block as returned by the blocks.children API."""
    return {
        "object": "block",
        "id": block_id,
        "type": "image",
        "image": {
            "type": "external",
            "external": {"url": url},
            "caption": (
                [{"type": "text", "text": {"content": caption}, "plain_text": caption}]
                if caption
                else []
            ),
        },
    }


async def test_fetch_document_sets_ref_images_directly(
    notion_adapter: Any,
    mock_connector: SimpleNamespace,
) -> None:
    """Image blocks discovered during fetch are attached to the DocumentRef."""
    ref = DocumentRef(
        path="Page One.md",
        ref="page-abc-123",
        size=0,
        content_type="notion_page",
        source_ref="page-abc-123",
    )
    blocks = [
        _image_block("img-1", "https://cdn.notion.com/img/one.png", caption="first"),
        _image_block("img-2", "https://cdn.notion.com/img/two.png"),
    ]

    with patch("app.adapters.notion.fetch_blocks_recursive", return_value=blocks):
        await notion_adapter.fetch_document(ref, mock_connector)

    assert ref.images is not None
    assert len(ref.images) == 2
    urls = [img.url for img in ref.images]
    assert urls == [
        "https://cdn.notion.com/img/one.png",
        "https://cdn.notion.com/img/two.png",
    ]
    assert ref.images[0].alt == "first"
    assert ref.images[0].source_path == "img-1"


async def test_fetch_document_leaves_ref_images_none_when_no_images(
    notion_adapter: Any,
    mock_connector: SimpleNamespace,
) -> None:
    """A page without image blocks leaves DocumentRef.images unset."""
    ref = DocumentRef(
        path="Plain.md",
        ref="page-plain",
        size=0,
        content_type="notion_page",
        source_ref="page-plain",
    )
    blocks = [
        {
            "object": "block",
            "id": "p-1",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": "hi"}}]},
        },
    ]

    with patch("app.adapters.notion.fetch_blocks_recursive", return_value=blocks):
        await notion_adapter.fetch_document(ref, mock_connector)

    assert ref.images is None


def test_adapter_has_no_image_cache_attribute(notion_adapter: Any) -> None:
    """The legacy _image_cache side-channel must remain removed."""
    assert not hasattr(notion_adapter, "_image_cache")
    assert not hasattr(notion_adapter, "get_cached_images")
