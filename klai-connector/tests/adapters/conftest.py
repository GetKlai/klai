"""Shared fixtures for adapter tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.core.config import Settings


@pytest.fixture
def mock_settings() -> Settings:
    """Minimal Settings mock with required fields for adapter construction."""
    s = MagicMock(spec=Settings)
    s.github_app_id = "fake"
    s.github_app_private_key = "fake"
    s.database_url = "sqlite+aiosqlite://"
    s.encryption_key = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    s.zitadel_introspection_url = "https://example.com"
    s.zitadel_client_id = "fake"
    s.zitadel_client_secret = "fake"
    s.knowledge_ingest_url = "http://localhost"
    s.knowledge_ingest_secret = ""
    s.cors_origins = ""
    s.crawl4ai_api_url = "http://localhost"
    s.crawl4ai_internal_key = ""
    s.portal_api_url = "http://localhost"
    s.portal_internal_secret = ""
    s.portal_caller_secret = ""
    s.log_level = "INFO"
    return s


def _make_connector(config: dict[str, Any]) -> SimpleNamespace:
    """Build a lightweight connector-like object with a config dict."""
    return SimpleNamespace(
        id="conn-001",
        org_id="org-001",
        config=config,
    )


@pytest.fixture
def mock_connector() -> SimpleNamespace:
    """Connector stub with a valid Notion config."""
    return _make_connector(
        {
            "access_token": "secret_abc123notiontoken",
            "database_ids": [],
            "max_pages": 500,
        }
    )


@pytest.fixture
def mock_connector_with_databases() -> SimpleNamespace:
    """Connector stub with specific database_ids."""
    return _make_connector(
        {
            "access_token": "secret_abc123notiontoken",
            "database_ids": ["db-aaa", "db-bbb"],
            "max_pages": 100,
        }
    )


@pytest.fixture
def mock_connector_no_token() -> SimpleNamespace:
    """Connector stub missing the required access_token."""
    return _make_connector({"database_ids": []})


@pytest.fixture
def notion_adapter(mock_settings: Settings) -> Any:
    """Create a NotionAdapter instance for testing."""
    from app.adapters.notion import NotionAdapter

    return NotionAdapter(mock_settings)


# -- Notion API response factories ----------------------------------------


def make_page(
    page_id: str,
    title: str = "Test Page",
    last_edited: str = "2026-04-01T10:00:00.000Z",
) -> dict[str, Any]:
    """Build a minimal Notion page object as returned by the Search API."""
    return {
        "object": "page",
        "id": page_id,
        "created_time": "2026-01-01T00:00:00.000Z",
        "last_edited_time": last_edited,
        "archived": False,
        "url": f"https://www.notion.so/{page_id.replace('-', '')}",
        "properties": {
            "title": {
                "id": "title",
                "type": "title",
                "title": [{"type": "text", "text": {"content": title}}],
            }
        },
        "parent": {"type": "workspace", "workspace": True},
    }


# Note: make_search_response / make_blocks_children_response used to live here.
# They wrapped pages/blocks in the Notion API envelope for the original async-first
# tests that mocked the adapter's now-removed `_search_pages` / `_get_page_blocks`
# methods. The refactored adapter returns already-unwrapped lists, so both helpers
# became dead code and were removed.
