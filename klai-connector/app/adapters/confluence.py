"""Confluence Cloud connector adapter using atlassian-python-api.

Syncs Confluence pages as knowledge documents. Supports full-scan sync via
page iteration over configured spaces. Page content is converted from
Confluence storage format (XHTML) to plain text via html2text.

SDK note: atlassian-python-api is synchronous. All blocking calls are wrapped
with asyncio.to_thread() per the klai Python async pattern (lang/python.md).

API version (GetKlai/klai#1137): this adapter targets the Confluence Cloud
**v2** REST API through ``atlassian.ConfluenceV2``. atlassian-python-api v5
turned ``atlassian.Confluence`` into a dispatcher whose Cloud implementation
no longer exposes ``get_page_by_id``, and whose remaining v1 compatibility
shims build URLs without the REST api-root prefix. ``ConfluenceV2`` is the
supported Cloud client: it resolves ``<base>/wiki/api/v2/...`` and keeps a
typed ``get_page_by_id`` / ``get_pages`` / ``get_spaces`` surface.

v2 differences that are visible in the DocumentRefs this adapter produces:
    * Pages are addressed by numeric **space id**, not by space key, so the
      space listing is resolved into a key -> id map before page iteration.
    * ``version.by.email`` is gone. v2 returns only ``version.authorId``
      (an Atlassian account id), so ``sender_email`` is always empty for
      Confluence. It stays an optional field downstream — the ingest client
      only writes it into ``extra`` when non-empty.

Image carve-out (SPEC-KB-CONNECTORS-001 R4.4):
    Shape A (external URL, <ri:url>): extracted into ref.images.
    Shape B (attachment, <ri:attachment>): silently dropped with info log.
    Reason: sync_engine._image_http is a plain httpx.AsyncClient with no
    per-adapter auth headers. Downloading Confluence attachment URLs requires
    Bearer or Basic auth; downloading them without auth returns 401/403.
    A future SPEC will add per-adapter auth header support to the image
    download pipeline.
"""

# @MX:ANCHOR: BaseAdapter implementation -- SPEC-KB-CONNECTORS-001 Phase 3
# @MX:SPEC: SPEC-KB-CONNECTORS-001

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, cast

import html2text
from atlassian import ConfluenceV2
from bs4 import BeautifulSoup

from app.adapters.base import BaseAdapter, DocumentRef, ImageRef
from app.core.config import Settings
from app.core.logging import get_logger
from app.services.url_guard import validate_confluence_base_url_strict

logger = get_logger(__name__)

# Maximum number of spaces to iterate when space_keys is empty.
_MAX_SPACES = 100

# Maximum number of pages synced per space. Preserves the effective bound of
# the pre-v5 call (``get_all_pages_from_space(space, start=0, limit=100)``
# returned a single 100-item page). ``ConfluenceV2.get_pages`` follows the v2
# cursor to the end of the space, so the bound is applied here instead — and
# logged when it truncates, so a silently half-synced space is impossible.
_MAX_PAGES_PER_SPACE = 100

# Per-request page size for the v2 cursor pagination.
_PAGE_BATCH = 100


def _build_confluence_client(
    base_url: str,
    email: str,
    api_token: str,
) -> Any:
    """Construct a Confluence Cloud v2 client (sync, run via asyncio.to_thread).

    ``ConfluenceV2`` appends the ``/wiki`` context path to *base_url* itself
    and is idempotent when the caller already supplied one.
    """
    return ConfluenceV2(
        url=base_url,
        username=email,
        password=api_token,
        cloud=True,
    )


class ConfluenceAdapter(BaseAdapter):
    """Confluence Cloud connector adapter.

    Authenticates via email + API token stored in connector.config.
    Iterates pages in configured spaces and converts each page's storage-format
    HTML to plain text for ingestion by the knowledge pipeline.

    Does NOT hold a persistent Confluence client — credentials are per-tenant
    and the adapter instance is a singleton per process. The client is
    constructed inside each async operation from the connector config.
    """

    def __init__(self, settings: Settings) -> None:
        # Settings kept for interface compatibility; no global Confluence credentials.
        self._settings = settings

    async def aclose(self) -> None:
        """No persistent resources to close."""
        return None

    # -- Config helpers -------------------------------------------------------

    @staticmethod
    def _extract_config(connector: Any) -> dict[str, Any]:
        """Extract and validate Confluence config from connector.config.

        Required fields:
            base_url: Atlassian Cloud base URL (e.g. https://company.atlassian.net).
                Trailing slash is stripped.
            email: Atlassian account email address.
            api_token: Atlassian API token.

        Optional fields:
            space_keys: List of space keys to sync (default: [] = all spaces).

        Raises:
            ValueError: If any required field is missing or empty.
        """
        config: dict[str, Any] = connector.config

        base_url: str | None = config.get("base_url")
        if not base_url:
            raise ValueError(
                "Confluence connector config missing required field 'base_url'. "
                "Provide the Atlassian Cloud URL in connector.config.base_url."
            )
        base_url = base_url.rstrip("/")
        # SPEC-SEC-SSRF-001 REQ-8.4 / AC-21: re-validate a persisted
        # base_url against the Atlassian allowlist + SSRF reject-list.
        # Legacy rows predating REQ-8 may still hold a docker-internal
        # or private-IP URL. If validation fails the helper raises
        # ``PersistedUrlRejectedError`` which propagates to the sync runner
        # to mark the run failed with
        # ``error="ssrf_blocked_persisted_confluence_base_url"``
        # WITHOUT instantiating an ``atlassian.Confluence`` client
        # (avoiding the blind-SSRF + Basic-auth-leak primitive).
        connector_id = getattr(connector, "id", None)
        validate_confluence_base_url_strict(
            base_url,
            connector_id=str(connector_id) if connector_id else None,
        )

        email: str | None = config.get("email")
        if not email:
            raise ValueError(
                "Confluence connector config missing required field 'email'. "
                "Provide the Atlassian account email in connector.config.email."
            )

        api_token: str | None = config.get("api_token")
        if not api_token:
            raise ValueError(
                "Confluence connector config missing required field 'api_token'. "
                "Provide the Atlassian API token in connector.config.api_token."
            )

        space_keys: list[str] = config.get("space_keys") or []

        return {
            "base_url": base_url,
            "email": email,
            "api_token": api_token,
            "space_keys": space_keys,
        }

    # -- BaseAdapter implementation -------------------------------------------

    async def list_documents(
        self,
        connector: Any,
        cursor_context: dict[str, Any] | None = None,
    ) -> list[DocumentRef]:
        """List all documents available for sync from Confluence.

        Iterates pages in each configured space using the atlassian-python-api
        synchronous SDK wrapped via asyncio.to_thread(). Full-scan only —
        Confluence does not provide a cheap incremental cursor; the sync engine
        reconciles using last_edited on each DocumentRef.

        The v2 page endpoint filters by numeric space id, not by space key, so
        the spaces are resolved into a key -> id map first — via the v2 ``keys``
        filter when space_keys is configured, otherwise a full listing capped
        at _MAX_SPACES. Keys the token cannot see are logged and skipped rather
        than failing the whole run.

        Args:
            connector: Connector model instance with Confluence config.
            cursor_context: Previous cursor state (not used for filtering;
                kept for interface compatibility).

        Returns:
            A list of DocumentRef instances, one per Confluence page.
        """
        cfg = self._extract_config(connector)
        base_url: str = cfg["base_url"]
        email: str = cfg["email"]
        api_token: str = cfg["api_token"]
        space_keys: list[str] = cfg["space_keys"]

        client = await asyncio.to_thread(
            _build_confluence_client, base_url, email, api_token
        )

        space_ids_by_key = await asyncio.to_thread(
            self._discover_all_spaces, client, space_keys
        )

        if space_keys:
            missing = [key for key in space_keys if key not in space_ids_by_key]
            if missing:
                logger.warning(
                    "Confluence list_documents: configured space key(s) %s not "
                    "visible to this token — skipped",
                    missing,
                )
            # Preserve the configured order rather than the API listing order.
            selected = {
                key: space_ids_by_key[key]
                for key in space_keys
                if key in space_ids_by_key
            }
        else:
            selected = space_ids_by_key

        refs: list[DocumentRef] = []

        for space_key, space_id in selected.items():
            pages = await asyncio.to_thread(
                self._fetch_all_pages_in_space, client, space_key, space_id
            )
            for page in pages:
                page_id: str = str(page.get("id", ""))
                version: dict[str, Any] = cast(dict[str, Any], page.get("version") or {})
                # v2 exposes the last version's timestamp; fall back to the
                # page creation timestamp when ``version`` was not returned.
                last_edited: str = version.get("createdAt") or page.get("createdAt") or ""

                source_url = f"{base_url}/wiki/spaces/{space_key}/pages/{page_id}"

                refs.append(
                    DocumentRef(
                        path=f"{space_key}/{page_id}",
                        ref=page_id,
                        size=0,
                        content_type="text/plain",
                        source_ref=source_url,
                        source_url=source_url,
                        last_edited=last_edited,
                        # v2 returns ``version.authorId`` (account id) only —
                        # there is no email on the page payload. See module
                        # docstring.
                        sender_email="",
                        mentioned_emails=[],
                    )
                )

        logger.info(
            "Confluence list_documents complete: spaces=%s pages=%d",
            list(selected),
            len(refs),
        )
        return refs

    async def fetch_document(self, ref: DocumentRef, connector: Any) -> bytes:
        """Fetch a single Confluence page and return it as UTF-8 encoded bytes.

        Retrieves the page in storage format (XHTML) via the v2
        ``GET /wiki/api/v2/pages/{id}?body-format=storage`` endpoint, extracts
        external images into ref.images (Shape A only — see module docstring
        for carve-out), strips Confluence-specific ac:* tags, and converts to
        plain text via html2text.

        Args:
            ref: DocumentRef with ``ref`` set to the Confluence page ID.
            connector: Connector model instance with Confluence config.

        Returns:
            UTF-8 encoded plain text representation of the page.
        """
        cfg = self._extract_config(connector)
        base_url: str = cfg["base_url"]
        email: str = cfg["email"]
        api_token: str = cfg["api_token"]

        page_id = ref.ref
        client = await asyncio.to_thread(
            _build_confluence_client, base_url, email, api_token
        )

        page = await asyncio.to_thread(
            client.get_page_by_id,
            page_id,
            body_format="storage",
        )

        storage_xml: str = (
            cast(dict[str, Any], page.get("body") or {})
            .get("storage", {})
            .get("value", "")
        )

        # Extract images (mutate ref.images in place, matching github.py pattern)
        external_images, skipped_attachments = _extract_confluence_images(storage_xml)
        if skipped_attachments:
            logger.info(
                "Confluence fetch_document: skipped %d attachment image(s) on page %s "
                "(Shape B carve-out — sync_engine HTTP client has no per-adapter auth)",
                skipped_attachments,
                page_id,
            )
        if external_images:
            ref.images = external_images

        # Convert storage format to plain text
        text = _storage_to_text(storage_xml)
        return text.encode("utf-8")

    async def get_cursor_state(self, connector: Any) -> dict[str, Any]:
        """Return the current cursor state for incremental sync.

        Confluence does not have a native incremental cursor that maps cleanly
        to the sync engine's reconciliation model. A full scan is performed each
        time; the sync engine reconciles using last_edited on each DocumentRef.

        Returns:
            Dict with ``last_run_at`` ISO 8601 timestamp.
        """
        return {"last_run_at": datetime.now(UTC).isoformat()}

    # -- Synchronous helpers (run via asyncio.to_thread) ----------------------

    @staticmethod
    def _discover_all_spaces(
        client: Any,
        space_keys: list[str] | None = None,
    ) -> dict[str, str]:
        """Map Confluence space keys to their v2 space ids (synchronous).

        Wrapped via asyncio.to_thread() in async callers.

        When *space_keys* is given, the v2 ``keys`` filter resolves exactly
        those spaces. That matters for a tenant with more spaces than
        _MAX_SPACES: an unfiltered listing could stop before reaching a
        configured space and silently sync nothing for it.

        When *space_keys* is empty the full listing is used, capped at
        _MAX_SPACES. ``get_spaces`` follows the v2 cursor to the end, so the
        cap is applied to the materialised result and logged when it actually
        truncates.

        Returns:
            Mapping of space key -> space id, in listing order.
        """
        if space_keys:
            raw: Any = client.get_spaces(keys=space_keys, limit=_MAX_SPACES)
        else:
            raw = client.get_spaces(limit=_MAX_SPACES)

        results: list[Any]
        if isinstance(raw, dict):
            raw_dict: dict[str, Any] = cast(dict[str, Any], raw)
            results = cast(list[Any], raw_dict.get("results") or [])
        elif isinstance(raw, list):
            results = cast(list[Any], raw)
        else:
            results = list(raw)  # pyright: ignore[reportUnknownArgumentType]

        ids_by_key: dict[str, str] = {}
        for space in results:
            if not isinstance(space, dict):
                continue
            space_dict: dict[str, Any] = cast(dict[str, Any], space)
            key: Any = space_dict.get("key")
            space_id: Any = space_dict.get("id")
            if key and space_id and str(key) not in ids_by_key:
                ids_by_key[str(key)] = str(space_id)

        if not space_keys and len(ids_by_key) > _MAX_SPACES:
            logger.warning(
                "Confluence: %d spaces visible — truncated to _MAX_SPACES=%d; "
                "the remainder is not synced",
                len(ids_by_key),
                _MAX_SPACES,
            )
            ids_by_key = dict(list(ids_by_key.items())[:_MAX_SPACES])
        return ids_by_key

    @staticmethod
    def _fetch_all_pages_in_space(
        client: Any,
        space_key: str,
        space_id: str,
    ) -> list[dict[str, Any]]:
        """Fetch pages from a single Confluence space by space id (synchronous).

        Wrapped via asyncio.to_thread() in async callers. Converts the
        result to a list inside the thread to avoid generator leakage, then
        truncates to _MAX_PAGES_PER_SPACE.

        *space_key* is carried only for logging — the v2 page endpoint filters
        on *space_id*.
        """
        try:
            raw: Any = client.get_pages(
                space_id=space_id,
                limit=_PAGE_BATCH,
            )
            pages: list[dict[str, Any]]
            if isinstance(raw, list):
                pages = cast(list[dict[str, Any]], raw)
            elif isinstance(raw, dict):
                raw_dict: dict[str, Any] = cast(dict[str, Any], raw)
                pages = cast(list[dict[str, Any]], raw_dict.get("results") or [])
            else:
                pages = cast(list[dict[str, Any]], list(raw))
        except Exception:
            logger.warning(
                "Confluence: failed to list pages in space %s (id=%s)",
                space_key,
                space_id,
                exc_info=True,
            )
            return []

        if len(pages) > _MAX_PAGES_PER_SPACE:
            logger.warning(
                "Confluence: space %s returned %d pages — truncated to "
                "_MAX_PAGES_PER_SPACE=%d; the remainder is not synced",
                space_key,
                len(pages),
                _MAX_PAGES_PER_SPACE,
            )
            pages = pages[:_MAX_PAGES_PER_SPACE]
        return pages


# -- Module-level helpers (pure functions, no adapter state) ------------------


def _extract_confluence_images(
    storage_xml: str,
) -> tuple[list[ImageRef], int]:
    """Extract image references from Confluence storage-format XML.

    Parses <ac:image> elements and classifies them:
    - Shape A: <ri:url ri:value="https://..."/> → added to result list.
    - Shape B: <ri:attachment ri:filename="..."/> → counted as skipped.

    Args:
        storage_xml: Raw Confluence storage-format XHTML string.

    Returns:
        Tuple of (list_of_external_images, count_of_skipped_attachments).
    """
    if not storage_xml:
        return [], 0

    soup = BeautifulSoup(storage_xml, "lxml")
    external_images: list[ImageRef] = []
    skipped_attachments = 0

    for img_tag in soup.find_all("ac:image"):
        # Determine image shape by inspecting the first ri:* child tag.
        ri_url = img_tag.find("ri:url")
        ri_attachment = img_tag.find("ri:attachment")

        if ri_url is not None:
            # Shape A — external URL, safe to download without auth.
            url: str | list[str] | None = ri_url.get("ri:value")
            url_str = str(url) if url else ""
            if url_str:
                # Try to extract alt text from ac:caption child element.
                caption_tag = img_tag.find("ac:caption")
                alt = ""
                if caption_tag is not None:
                    alt = caption_tag.get_text(separator=" ", strip=True)
                external_images.append(
                    ImageRef(url=url_str, alt=alt, source_path="")
                )
        elif ri_attachment is not None:
            # Shape B — requires Confluence auth to download.
            # @MX:TODO: Shape B attachment image support blocked by sync_engine
            # @MX:REASON: sync_engine._image_http is a plain httpx.AsyncClient with
            # no per-adapter auth headers. Downloading attachment URLs requires
            # Atlassian Basic or Bearer auth and returns 401/403 without it.
            # Future SPEC: extend sync_engine to pass per-adapter auth context to
            # download_and_upload_adapter_images so attachments can be fetched.
            skipped_attachments += 1

    return external_images, skipped_attachments


def _storage_to_text(storage_xml: str) -> str:
    """Convert Confluence storage-format XHTML to plain text.

    Steps:
    1. Parse with BeautifulSoup (lxml parser — already a transitive dependency).
    2. Remove or unwrap Confluence-specific <ac:*> and <ri:*> tags that
       html2text does not understand.
    3. Convert cleaned HTML to plain text via html2text.

    Args:
        storage_xml: Raw Confluence storage-format XHTML string.

    Returns:
        Plain text representation of the page content.
    """
    if not storage_xml:
        return ""

    soup = BeautifulSoup(storage_xml, "lxml")

    # Remove Confluence macro/structured-content tags entirely.
    # These contain code blocks, parameters, and other non-prose content
    # that produces noise when converted by html2text.
    ac_remove_tags = [
        "ac:structured-macro",
        "ac:parameter",
        "ac:plain-text-body",
        "ac:rich-text-body",
        "ac:image",
        "ri:url",
        "ri:attachment",
        "ri:page",
        "ri:space",
    ]
    for tag_name in ac_remove_tags:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    # Unwrap any remaining ac:* / ri:* tags (preserve their text content).
    for tag in soup.find_all(True):
        if tag.name and (tag.name.startswith("ac:") or tag.name.startswith("ri:")):
            tag.unwrap()

    # Get the cleaned HTML string.
    cleaned_html = str(soup)

    # Convert to plain text.
    h = html2text.HTML2Text()
    h.ignore_images = True
    h.ignore_links = False
    h.body_width = 0  # Do not wrap lines — let downstream chunking handle it.

    return h.handle(cleaned_html)
