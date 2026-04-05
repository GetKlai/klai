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

import httpx
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

        Config fields:
            access_token (required): Notion integration token.
            page_ids (optional): List of specific Notion page IDs to sync.
                When set, only these pages are synced — search is skipped entirely.
            database_ids (optional): List of Notion database IDs. When set,
                only pages whose parent is one of these databases are synced.
                Applied as a post-fetch filter (notion_client v2 has no server-side
                database query — see knowledge rule notion_client-v2).
            max_pages (optional): Safety limit on total pages synced. Default 500.

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
            "page_ids": config.get("page_ids", []),
            "database_ids": config.get("database_ids", []),
            "max_pages": config.get("max_pages", 500),
        }

    @staticmethod
    def _build_sync_client(access_token: str) -> RateLimitedNotionClient:
        """Create a rate-limited notion-sync-lib client with a 30s timeout."""
        return RateLimitedNotionClient(
            Client(auth=access_token, client=httpx.Client(timeout=httpx.Timeout(30.0)))
        )

    # -- Search helper (sync, runs in thread pool) ----------------------------

    @staticmethod
    def _fetch_specific_pages(
        client: RateLimitedNotionClient,
        page_ids: list[str],
    ) -> list[dict[str, Any]]:
        """Fetch specific Notion pages by ID directly (no search).

        Runs synchronously — call via asyncio.to_thread.

        Args:
            client: Rate-limited Notion client.
            page_ids: List of Notion page UUIDs to fetch.

        Returns:
            List of Notion page objects (skips archived or missing pages).
        """
        pages: list[dict[str, Any]] = []
        for page_id in page_ids:
            try:
                page = client._execute_with_retry(  # noqa: SLF001
                    f"retrieve page {page_id}",
                    client.notion.pages.retrieve,
                    page_id=page_id,
                )
                if page.get("archived", False):
                    continue
                pages.append(page)
            except Exception:
                logger.warning("Failed to retrieve Notion page %s — skipping", page_id)
        return pages

    @staticmethod
    def _search_all_pages(
        client: RateLimitedNotionClient,
        last_synced_at: str | None,
        max_pages: int,
        database_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch Notion pages via the Search API with optional database filter.

        Runs synchronously — call via asyncio.to_thread.

        Args:
            client: Rate-limited Notion client.
            last_synced_at: ISO 8601 timestamp; skip pages last edited before this.
            max_pages: Safety limit on total pages returned.
            database_ids: When set, only include pages whose parent is one of
                these database IDs (post-fetch filter — notion_client v2 has no
                server-side database query endpoint).

        Returns:
            List of Notion page objects.
        """
        db_filter: set[str] = set(database_ids) if database_ids else set()
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
                if db_filter:
                    parent = page.get("parent", {})
                    if parent.get("type") != "database_id" or parent.get("database_id") not in db_filter:
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
        page_ids: list[str] = cfg["page_ids"]
        database_ids: list[str] = cfg["database_ids"]
        max_pages: int = cfg["max_pages"]
        last_synced_at: str | None = (cursor_context or {}).get("last_synced_at")

        connector_id = str(getattr(connector, "connector_id", "") or getattr(connector, "id", ""))

        if page_ids:
            logger.info(
                "Fetching %d specific Notion pages (connector=%s)",
                len(page_ids), connector_id,
            )
            pages = await asyncio.to_thread(self._fetch_specific_pages, client, page_ids)
        else:
            logger.info(
                "Listing Notion pages (connector=%s, incremental=%s, database_filter=%s)",
                connector_id, last_synced_at is not None, bool(database_ids),
            )
            pages = await asyncio.to_thread(
                self._search_all_pages, client, last_synced_at, max_pages, database_ids or None
            )

        refs = [
            DocumentRef(
                path=self._extract_title(page) or page["id"],
                ref=page["id"],
                size=0,
                content_type="notion_page",
                source_ref=page["id"],
                source_url=f"https://notion.so/{page['id'].replace('-', '')}",
            )
            for page in pages
        ]

        logger.info("Listed %d Notion pages (connector=%s)", len(refs), connector_id)
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
        logger.info("Fetching Notion page %s (connector=%s)", ref.ref, connector_id)

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
