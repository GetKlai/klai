"""Confluence Cloud connector adapter.

Syncs Confluence Cloud pages as knowledge documents via the REST API v2.
Supports optional space filtering and incremental sync via cursor_context.
"""

from __future__ import annotations

import re
from base64 import b64encode
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from app.adapters.base import BaseAdapter, DocumentRef
from app.core.config import Settings

logger = structlog.get_logger(__name__)


def _strip_html_tags(html: str) -> str:
    """Strip HTML tags from a string, returning plain text."""
    text = re.sub(r"<[^>]+>", " ", html)
    # Collapse whitespace.
    text = re.sub(r"\s+", " ", text).strip()
    return text


class ConfluenceAdapter(BaseAdapter):
    """Confluence Cloud connector adapter.

    Authenticates via basic auth (email + API token) against the
    Confluence Cloud REST API v2. Lists pages with their storage-format
    bodies and converts HTML to plain text for ingestion.

    Config fields (from connector.config):
        base_url (required): Atlassian instance URL, e.g. "https://company.atlassian.net"
        email (required): Atlassian account email.
        api_token (required): Atlassian API token.
        space_keys (optional): List of space keys to filter. Empty = all spaces.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._http_client: httpx.AsyncClient | None = None

    async def _get_client(self, connector: Any) -> httpx.AsyncClient:
        """Return a configured httpx client with basic auth headers."""
        if self._http_client is None:
            config = connector.config
            email = config.get("email", "")
            api_token = config.get("api_token", "")
            credentials = b64encode(f"{email}:{api_token}".encode()).decode()
            self._http_client = httpx.AsyncClient(
                timeout=30.0,
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Accept": "application/json",
                },
            )
        return self._http_client

    async def aclose(self) -> None:
        """Close the HTTP client."""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    @staticmethod
    def _extract_config(connector: Any) -> dict[str, Any]:
        """Extract and validate Confluence config."""
        config: dict[str, Any] = connector.config
        base_url = config.get("base_url", "").rstrip("/")
        email = config.get("email", "")
        api_token = config.get("api_token", "")

        if not base_url:
            raise ValueError("Confluence config missing 'base_url'")
        if not email or not api_token:
            raise ValueError("Confluence config missing 'email' or 'api_token'")

        return {
            "base_url": base_url,
            "email": email,
            "api_token": api_token,
            "space_keys": config.get("space_keys", []),
        }

    async def _get_space_ids(
        self, client: httpx.AsyncClient, base_url: str, space_keys: list[str],
    ) -> list[str]:
        """Resolve space keys to space IDs via the v2 API."""
        if not space_keys:
            return []

        space_ids: list[str] = []
        for key in space_keys:
            resp = await client.get(
                f"{base_url}/wiki/api/v2/spaces",
                params={"keys": key, "limit": 1},
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            if results:
                space_ids.append(results[0]["id"])
            else:
                logger.warning("Confluence space key not found", space_key=key)
        return space_ids

    async def _list_pages(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        space_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch all pages, optionally filtered by space ID, with pagination."""
        pages: list[dict[str, Any]] = []
        params: dict[str, Any] = {
            "body-format": "storage",
            "limit": 50,
        }
        url = (
            f"{base_url}/wiki/api/v2/spaces/{space_id}/pages"
            if space_id
            else f"{base_url}/wiki/api/v2/pages"
        )

        while True:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            pages.extend(data.get("results", []))

            # Cursor-based pagination: Confluence v2 returns _links.next.
            next_link = data.get("_links", {}).get("next")
            if not next_link:
                break
            # next_link is a relative path.
            url = f"{base_url}{next_link}"
            params = {}  # params are encoded in the next_link

        return pages

    async def list_documents(
        self,
        connector: Any,
        cursor_context: dict[str, Any] | None = None,
    ) -> list[DocumentRef]:
        """List all Confluence pages with body content.

        Always returns the full set of pages. The sync engine handles
        reconciliation using last_edited timestamps.
        """
        cfg = self._extract_config(connector)
        client = await self._get_client(connector)
        base_url: str = cfg["base_url"]
        space_keys: list[str] = cfg["space_keys"]

        all_pages: list[dict[str, Any]] = []

        if space_keys:
            space_ids = await self._get_space_ids(client, base_url, space_keys)
            for sid in space_ids:
                pages = await self._list_pages(client, base_url, space_id=sid)
                all_pages.extend(pages)
        else:
            all_pages = await self._list_pages(client, base_url)

        # Cache page bodies for fetch_document.
        self._body_cache: dict[str, str] = {}

        refs: list[DocumentRef] = []
        for page in all_pages:
            page_id = page.get("id", "")
            title = page.get("title", page_id)
            version = page.get("version", {})
            last_edited = version.get("createdAt", "") if isinstance(version, dict) else ""

            # Cache the storage body HTML.
            body_html = page.get("body", {}).get("storage", {}).get("value", "")
            self._body_cache[page_id] = body_html

            refs.append(
                DocumentRef(
                    path=title,
                    ref=page_id,
                    size=len(body_html.encode("utf-8")),
                    content_type="kb_article",
                    source_ref=page_id,
                    source_url=f"{base_url}/wiki/spaces/{page.get('spaceId', '')}/pages/{page_id}",
                    last_edited=last_edited,
                )
            )

        logger.info("Listed Confluence pages", count=len(refs))
        return refs

    async def fetch_document(self, ref: DocumentRef, connector: Any) -> bytes:
        """Return page content as plain text bytes.

        Uses cached body from list_documents if available, otherwise
        fetches from the API.
        """
        cached = getattr(self, "_body_cache", {}).get(ref.ref)
        if cached is not None:
            text = _strip_html_tags(cached)
            return text.encode("utf-8")

        # Fallback: fetch directly.
        cfg = self._extract_config(connector)
        client = await self._get_client(connector)
        base_url: str = cfg["base_url"]

        resp = await client.get(
            f"{base_url}/wiki/api/v2/pages/{ref.ref}",
            params={"body-format": "storage"},
        )
        resp.raise_for_status()
        body_html = resp.json().get("body", {}).get("storage", {}).get("value", "")
        text = _strip_html_tags(body_html)
        return text.encode("utf-8")

    async def get_cursor_state(self, connector: Any) -> dict[str, Any]:
        """Return cursor with current time as the sync marker."""
        return {"last_synced_at": datetime.now(UTC).isoformat()}

    async def post_sync(self, connector: Any) -> None:
        """Clear cached page bodies after sync."""
        self._body_cache = {}
