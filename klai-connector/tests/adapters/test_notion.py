"""Specification tests for NotionAdapter -- SPEC-KB-019.

RED phase: these tests define expected behavior before implementation exists.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.adapters.base import DocumentRef

from .conftest import make_blocks_children_response, make_page, make_search_response

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
    mock_search = AsyncMock(return_value=make_search_response(pages))

    with patch.object(notion_adapter, "_search_pages", mock_search):
        refs = await notion_adapter.list_documents(mock_connector, cursor_context=None)

    assert len(refs) == 2
    assert all(isinstance(r, DocumentRef) for r in refs)
    assert refs[0].ref == "page-001"
    assert refs[0].source_ref == "page-001"
    assert refs[0].content_type == "notion_page"
    assert refs[0].path != ""


# ---------------------------------------------------------------------------
# 2. list_documents -- incremental sync
# ---------------------------------------------------------------------------


async def test_list_documents_incremental_sync(
    notion_adapter: Any,
    mock_connector: SimpleNamespace,
) -> None:
    """R8: Incremental sync only returns pages edited after last_synced_at."""
    old_page = make_page("page-old", "Old", "2026-03-01T00:00:00.000Z")
    new_page = make_page("page-new", "New", "2026-04-03T15:00:00.000Z")

    # The adapter should filter based on cursor_context
    mock_search = AsyncMock(return_value=make_search_response([old_page, new_page]))

    cursor = {"last_synced_at": "2026-04-01T00:00:00.000Z"}
    with patch.object(notion_adapter, "_search_pages", mock_search):
        refs = await notion_adapter.list_documents(mock_connector, cursor_context=cursor)

    # Only the new page should be returned
    assert len(refs) == 1
    assert refs[0].ref == "page-new"


# ---------------------------------------------------------------------------
# 3. list_documents -- respects max_pages
# ---------------------------------------------------------------------------


async def test_list_documents_respects_max_pages(
    notion_adapter: Any,
) -> None:
    """R10: max_pages config limits how many pages are returned."""
    connector = SimpleNamespace(
        id="conn-001",
        org_id="org-001",
        config={"access_token": "secret_tok", "database_ids": [], "max_pages": 2},
    )

    pages = [make_page(f"page-{i}", f"Page {i}") for i in range(5)]
    mock_search = AsyncMock(return_value=make_search_response(pages))

    with patch.object(notion_adapter, "_search_pages", mock_search):
        refs = await notion_adapter.list_documents(connector, cursor_context=None)

    assert len(refs) <= 2


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

    blocks_resp = make_blocks_children_response()
    mock_children_list = AsyncMock(return_value=blocks_resp)

    with patch.object(notion_adapter, "_get_page_blocks", mock_children_list):
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
    pages = [
        make_page("p1", "A", "2026-04-01T10:00:00.000Z"),
        make_page("p2", "B", "2026-04-03T15:30:00.000Z"),
    ]
    mock_search = AsyncMock(return_value=make_search_response(pages))

    with patch.object(notion_adapter, "_search_pages", mock_search):
        state = await notion_adapter.get_cursor_state(mock_connector)

    assert "last_synced_at" in state
    # Should be the max last_edited_time
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
    mock_search = AsyncMock(return_value=make_search_response(pages))

    with caplog.at_level(logging.DEBUG), patch.object(notion_adapter, "_search_pages", mock_search):
        await notion_adapter.list_documents(mock_connector)

    full_log = caplog.text
    assert "secret_abc123notiontoken" not in full_log


# ---------------------------------------------------------------------------
# 9. Rate limit backoff on 429
# ---------------------------------------------------------------------------


async def test_rate_limit_backoff(
    notion_adapter: Any,
    mock_connector: SimpleNamespace,
) -> None:
    """NF: 429 response triggers retry with exponential backoff."""
    from httpx import Headers
    from notion_client import APIResponseError

    pages = [make_page("page-001")]

    mock_search = AsyncMock(
        side_effect=[
            APIResponseError(
                code="rate_limited",
                status=429,
                message="Rate limited",
                headers=Headers({"Retry-After": "1"}),
                raw_body_text="",
            ),
            make_search_response(pages),
        ]
    )

    with patch.object(notion_adapter, "_search_pages", mock_search):
        refs = await notion_adapter.list_documents(mock_connector)

    assert len(refs) == 1
    assert mock_search.call_count == 2
