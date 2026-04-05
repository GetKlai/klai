"""Notion connector adapter using notion-sync-lib.

Syncs Notion pages as knowledge documents. Supports full and incremental
sync via cursor_context with last_synced_at timestamp.

Block fetching uses notion-sync-lib's fetch_blocks_recursive, which handles
all nested block types (toggles, callouts, columns, tables, synced blocks)
and has built-in rate limiting with exponential backoff.
"""

# @MX:ANCHOR: BaseAdapter implementation -- SPEC-KB-019
# @MX:SPEC: SPEC-KB-019

from __future__ import annotations

import asyncio
from typing import Any

from notion_client import Client
from notion_sync import fetch_blocks_recursive
from notion_sync.client import RateLimitedNotionClient
from notion_sync.extract import extract_block_text

from app.adapters.base import BaseAdapter, DocumentRef
from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class NotionAdapter(BaseAdapter):
    """Notion connector adapter.

    Authenticates via an integration access_token stored in connector.config.
    Uses the Notion Search API to discover pages and notion-sync-lib's
    fetch_blocks_recursive to retrieve full page content as plain text,
    including all nested blocks (toggles, columns, tables, callouts).
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def aclose(self) -> None:
        """No persistent resources to close."""

    # -- Config helpers -------------------------------------------------------

    @staticmethod
    def _extract_config(connector: Any) -> dict[str, Any]:
        """Extract and validate Notion config from connector.config.

        Returns a normalised config dict with defaults applied.

        Raises:
            ValueError: If access_token is missing.
        """
        config: dict[str, Any] = connector.config
        access_token = config.get("access_token")
        if not access_token:
            raise ValueError(
                "Notion connector config missing required field 'access_token'. "
                "Provide a Notion integration token in connector.config.access_token."
            )
        return {
            "access_token": access_token,
            "database_ids": config.get("database_ids", []),
            "max_pages": config.get("max_pages", 500),
        }

    @staticmethod
    def _build_sync_client(access_token: str) -> RateLimitedNotionClient:
        """Create a rate-limited notion-sync-lib client."""
        return RateLimitedNotionClient(Client(auth=access_token))

    # -- Search helper (sync, runs in thread pool) ----------------------------

    @staticmethod
    def _search_all_pages(
        client: RateLimitedNotionClient,
        last_synced_at: str | None,
        max_pages: int,
    ) -> list[dict[str, Any]]:
        """Fetch all accessible Notion pages via the Search API.

        Runs synchronously — call via asyncio.to_thread.

        Args:
            client: Rate-limited Notion client.
            last_synced_at: ISO 8601 timestamp; skip pages last edited before this.
            max_pages: Maximum number of pages to return.

        Returns:
            List of Notion page objects.
        """
        pages: list[dict[str, Any]] = []
        next_cursor: str | None = None

        while True:
            kwargs: dict[str, Any] = {
                "filter": {"value": "page", "property": "object"},
                "page_size": 100,
            }
            if next_cursor:
                kwargs["start_cursor"] = next_cursor

            response = client._execute_with_retry(  # noqa: SLF001
                "search pages",
                client.notion.search,
                **kwargs,
            )

            for page in response.get("results", []):
                if page.get("object") != "page":
                    continue
                if page.get("archived", False):
                    continue
                last_edited: str = page.get("last_edited_time", "")
                if last_synced_at and last_edited <= last_synced_at:
                    continue
                pages.append(page)
                if len(pages) >= max_pages:
                    break

            if len(pages) >= max_pages or not response.get("has_more"):
                break
            next_cursor = response.get("next_cursor")

        return pages

    @staticmethod
    def _get_max_edited(client: RateLimitedNotionClient) -> str:
        """Return the maximum last_edited_time across all accessible pages.

        Runs synchronously — call via asyncio.to_thread.
        """
        max_edited = ""
        next_cursor: str | None = None

        while True:
            kwargs: dict[str, Any] = {
                "filter": {"value": "page", "property": "object"},
                "page_size": 100,
            }
            if next_cursor:
                kwargs["start_cursor"] = next_cursor

            response = client._execute_with_retry(  # noqa: SLF001
                "search pages for cursor",
                client.notion.search,
                **kwargs,
            )

            for page in response.get("results", []):
                if page.get("object") != "page":
                    continue
                edited = page.get("last_edited_time", "")
                if edited > max_edited:
                    max_edited = edited

            if not response.get("has_more"):
                break
            next_cursor = response.get("next_cursor")

        return max_edited

    # -- BaseAdapter interface ------------------------------------------------

    async def list_documents(
        self,
        connector: Any,
        cursor_context: dict[str, Any] | None = None,
    ) -> list[DocumentRef]:
        """List accessible Notion pages, optionally filtered by last_synced_at.

        Args:
            connector: Connector model instance with config JSONB.
            cursor_context: Previous cursor state with optional last_synced_at.

        Returns:
            List of DocumentRef, one per Notion page.
        """
        cfg = self._extract_config(connector)
        client = self._build_sync_client(cfg["access_token"])
        max_pages: int = cfg["max_pages"]
        last_synced_at: str | None = (cursor_context or {}).get("last_synced_at")

        connector_id = str(getattr(connector, "connector_id", "") or getattr(connector, "id", ""))
        logger.info(
            "Listing Notion pages",
            connector_id=connector_id,
            incremental=last_synced_at is not None,
        )

        pages = await asyncio.to_thread(
            self._search_all_pages, client, last_synced_at, max_pages
        )

        refs = [
            DocumentRef(
                path=self._extract_title(page) or page["id"],
                ref=page["id"],
                size=0,
                content_type="notion_page",
                source_ref=page["id"],
            )
            for page in pages
        ]

        logger.info("Listed Notion pages", count=len(refs), connector_id=connector_id)
        return refs

    async def fetch_document(self, ref: DocumentRef, connector: Any) -> bytes:
        """Fetch page content as plain-text bytes using notion-sync-lib.

        Uses fetch_blocks_recursive to get all nested content (toggles,
        callouts, columns, tables, synced blocks) then extracts plain text
        using extract_block_text (30+ block types supported).

        Args:
            ref: DocumentRef with Notion page ID in ref field.
            connector: Connector model instance.

        Returns:
            UTF-8 encoded plain text of the full page content.
        """
        cfg = self._extract_config(connector)
        client = self._build_sync_client(cfg["access_token"])

        connector_id = str(getattr(connector, "connector_id", "") or getattr(connector, "id", ""))
        logger.info("Fetching Notion page", page_id=ref.ref, connector_id=connector_id)

        blocks = await asyncio.to_thread(fetch_blocks_recursive, client, ref.ref)
        texts = _flatten_block_texts(blocks)
        content = "\n".join(texts)
        return content.encode("utf-8")

    async def get_cursor_state(self, connector: Any) -> dict[str, Any]:
        """Return cursor state based on max(last_edited_time) of accessible pages.

        Args:
            connector: Connector model instance.

        Returns:
            Dict with last_synced_at as ISO 8601 string.
        """
        cfg = self._extract_config(connector)
        client = self._build_sync_client(cfg["access_token"])
        max_edited = await asyncio.to_thread(self._get_max_edited, client)
        return {"last_synced_at": max_edited}

    # -- Text extraction helpers ----------------------------------------------

    @staticmethod
    def _extract_title(page: dict[str, Any]) -> str:
        """Extract the title string from a Notion page object."""
        props = page.get("properties", {})
        for prop in props.values():
            if prop.get("type") == "title":
                title_parts = prop.get("title", [])
                return "".join(
                    part.get("plain_text", part.get("text", {}).get("content", ""))
                    for part in title_parts
                )
        return ""


def _flatten_block_texts(blocks: list[dict[str, Any]]) -> list[str]:
    """Recursively extract plain text from blocks and their _children.

    notion-sync-lib stores nested blocks under the '_children' key.

    Args:
        blocks: List of block dicts from fetch_blocks_recursive.

    Returns:
        Flat list of non-empty text strings.
    """
    texts: list[str] = []
    for block in blocks:
        text = extract_block_text(block)
        if text:
            texts.append(text)
        children = block.get("_children", [])
        if children:
            texts.extend(_flatten_block_texts(children))
    return texts
