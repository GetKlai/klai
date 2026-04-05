"""Notion connector adapter using the official notion-client SDK.

Syncs Notion pages as knowledge documents. Supports full and incremental
sync via cursor_context with last_synced_at timestamp.
"""

# @MX:ANCHOR: BaseAdapter implementation -- SPEC-KB-019
# @MX:SPEC: SPEC-KB-019

from __future__ import annotations

import asyncio
from typing import Any

from notion_client import APIResponseError, AsyncClient

from app.adapters.base import BaseAdapter, DocumentRef
from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Notion API rate limit: 3 req/s -- use semaphore to stay within budget.
_RATE_LIMIT_CONCURRENCY = 3

# Maximum retry attempts for rate-limited (429) requests.
_MAX_RETRIES = 5

# Base delay (seconds) for exponential backoff on 429.
_BACKOFF_BASE = 1.0


class NotionAdapter(BaseAdapter):
    """Notion connector adapter.

    Authenticates via an integration access_token stored in connector.config.
    Uses the Notion Search API to discover pages and the Blocks API to
    retrieve page content as plain text.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._semaphore = asyncio.Semaphore(_RATE_LIMIT_CONCURRENCY)

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

    def _build_client(self, access_token: str) -> AsyncClient:
        """Create a Notion AsyncClient without logging the token."""
        return AsyncClient(auth=access_token)

    # -- Notion API wrappers (mockable seams) ---------------------------------

    async def _search_pages(
        self,
        client: AsyncClient,
        start_cursor: str | None = None,
        page_size: int = 100,
    ) -> dict[str, Any]:
        """Search all accessible pages via the Notion Search API.

        This is the primary seam for testing -- callers patch this method.
        """
        async with self._semaphore:
            kwargs: dict[str, Any] = {
                "filter": {"value": "page", "property": "object"},
                "page_size": page_size,
            }
            if start_cursor:
                kwargs["start_cursor"] = start_cursor
            return await client.search(**kwargs)  # type: ignore[no-any-return]

    async def _get_page_blocks(
        self,
        client: AsyncClient,
        page_id: str,
        start_cursor: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve child blocks of a page.

        This is the primary seam for testing -- callers patch this method.
        """
        async with self._semaphore:
            kwargs: dict[str, Any] = {}
            if start_cursor:
                kwargs["start_cursor"] = start_cursor
            return await client.blocks.children.list(  # type: ignore[no-any-return]
                block_id=page_id,
                **kwargs,
            )

    # -- Retry wrapper --------------------------------------------------------

    async def _with_retry(
        self,
        fn: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Call *fn* with exponential backoff on 429 rate-limit errors."""
        for attempt in range(_MAX_RETRIES):
            try:
                return await fn(*args, **kwargs)
            except APIResponseError as exc:
                if exc.code == "rate_limited" and attempt < _MAX_RETRIES - 1:
                    delay = _BACKOFF_BASE * (2**attempt)
                    logger.warning(
                        "Notion rate limited, retrying (attempt=%d, delay=%.1fs)",
                        attempt + 1, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise
        # Unreachable, but keeps type checkers happy.
        raise RuntimeError("Retry loop exited unexpectedly")  # pragma: no cover

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
        client = self._build_client(cfg["access_token"])
        max_pages: int = cfg["max_pages"]
        last_synced_at: str | None = (cursor_context or {}).get("last_synced_at")

        connector_id = str(getattr(connector, "id", ""))
        org_id = str(getattr(connector, "org_id", ""))
        logger.info(
            "Listing Notion pages (connector=%s, org=%s, incremental=%s)",
            connector_id, org_id, last_synced_at is not None,
        )

        try:
            refs: list[DocumentRef] = []
            next_cursor: str | None = None

            while True:
                response = await self._with_retry(
                    self._search_pages,
                    client,
                    start_cursor=next_cursor,
                )

                for page in response.get("results", []):
                    if page.get("object") != "page":
                        continue
                    if page.get("archived", False):
                        continue

                    page_id: str = page["id"]
                    last_edited: str = page.get("last_edited_time", "")

                    # Incremental filter: skip pages not edited since last sync.
                    if last_synced_at and last_edited <= last_synced_at:
                        continue

                    title = self._extract_title(page)
                    refs.append(
                        DocumentRef(
                            path=title or page_id,
                            ref=page_id,
                            size=0,
                            content_type="notion_page",
                            source_ref=page_id,
                        )
                    )

                    if len(refs) >= max_pages:
                        break

                if len(refs) >= max_pages:
                    break
                if not response.get("has_more"):
                    break
                next_cursor = response.get("next_cursor")

            logger.info(
                "Listed %d Notion pages (connector=%s)",
                len(refs), connector_id,
            )
            return refs

        finally:
            await client.aclose()

    async def fetch_document(self, ref: DocumentRef, connector: Any) -> bytes:
        """Fetch page content as plain-text bytes via the Blocks API.

        Args:
            ref: DocumentRef with page ID in ref field.
            connector: Connector model instance.

        Returns:
            UTF-8 encoded plain text of the page blocks.
        """
        cfg = self._extract_config(connector)
        client = self._build_client(cfg["access_token"])

        connector_id = str(getattr(connector, "connector_id", "") or getattr(connector, "id", ""))
        logger.info(
            "Fetching Notion page %s (connector=%s)",
            ref.ref, connector_id,
        )

        try:
            blocks_text = await self._fetch_all_block_text(client, ref.ref)
            content = "\n".join(blocks_text)
            return content.encode("utf-8")

        finally:
            await client.aclose()

    async def get_cursor_state(self, connector: Any) -> dict[str, Any]:
        """Return cursor state based on max(last_edited_time) of accessible pages.

        Args:
            connector: Connector model instance.

        Returns:
            Dict with last_synced_at as ISO 8601 string.
        """
        cfg = self._extract_config(connector)
        client = self._build_client(cfg["access_token"])

        try:
            max_edited: str = ""
            next_cursor: str | None = None

            while True:
                response = await self._with_retry(
                    self._search_pages,
                    client,
                    start_cursor=next_cursor,
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

            return {"last_synced_at": max_edited}

        finally:
            await client.aclose()

    # -- Recursive block fetching ---------------------------------------------

    # Block types that have children rendered as separate pages — don't recurse into them.
    _SKIP_CHILD_TYPES: frozenset[str] = frozenset({"child_page", "child_database"})

    async def _fetch_all_block_text(
        self,
        client: AsyncClient,
        block_id: str,
        depth: int = 0,
    ) -> list[str]:
        """Recursively fetch plain text from all blocks under *block_id*.

        Recurses into blocks with has_children=True up to depth 4. Skips
        child_page and child_database blocks (they are separate Notion pages).

        Args:
            client: Notion AsyncClient.
            block_id: Page or block UUID.
            depth: Current recursion depth (guards against pathological nesting).

        Returns:
            List of non-empty text strings extracted from all nested blocks.
        """
        if depth > 4:
            return []

        texts: list[str] = []
        next_cursor: str | None = None

        while True:
            response = await self._with_retry(
                self._get_page_blocks,
                client,
                block_id,
                start_cursor=next_cursor,
            )

            for block in response.get("results", []):
                text = self._extract_block_text(block)
                if text:
                    texts.append(text)

                block_type = block.get("type", "")
                if block.get("has_children") and block_type not in self._SKIP_CHILD_TYPES:
                    child_texts = await self._fetch_all_block_text(
                        client, block["id"], depth + 1
                    )
                    texts.extend(child_texts)

            if not response.get("has_more"):
                break
            next_cursor = response.get("next_cursor")

        return texts

    # -- Text extraction helpers ----------------------------------------------

    @staticmethod
    def _extract_title(page: dict[str, Any]) -> str:
        """Extract the title string from a Notion page object."""
        props = page.get("properties", {})
        for prop in props.values():
            if prop.get("type") == "title":
                title_parts = prop.get("title", [])
                return "".join(
                    part.get("text", {}).get("content", "") for part in title_parts
                )
        return ""

    @staticmethod
    def _extract_block_text(block: dict[str, Any]) -> str:
        """Extract plain text from a single Notion block."""
        block_type = block.get("type", "")
        block_data = block.get(block_type, {})
        rich_text = block_data.get("rich_text", [])
        return "".join(
            part.get("text", {}).get("content", "") for part in rich_text
        )
