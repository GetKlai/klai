"""Microsoft 365 (SharePoint + OneDrive) connector adapter (SPEC-KB-MS-DOCS-001).

Syncs OneDrive personal drives and SharePoint document libraries as knowledge
documents. Supports:

- First sync via ``/me/drive/root/delta`` (or per-site / per-drive variant),
  which enumerates the entire drive and returns an ``@odata.deltaLink`` for
  incremental syncs.
- Incremental sync via the persisted ``@odata.deltaLink`` — we call that URL
  verbatim and Microsoft Graph returns only the items that changed since.
- Office-binaries (DOCX, XLSX, PPTX) are downloaded as-is via
  ``/drive/items/{id}/content``; knowledge-ingest handles the parsing.
- OAuth access tokens are refreshed against Microsoft's token endpoint when
  cached tokens expire. On refresh, Microsoft often rotates the refresh_token;
  we write both back to the portal via ``PortalClient.update_credentials`` so
  the encrypted row stays valid across restarts.

Design notes (see SPEC-KB-MS-DOCS-001 for the full rationale):

- D1: Mirrors ``GoogleDriveAdapter`` methode-voor-methode — same OAuthAdapterBase,
  same delta-cursor pattern, same writeback flow.
- D2: Direct ``httpx`` calls (no ``msgraph-sdk``, no ``MSAL``, no
  ``unstructured-ingest[sharepoint]``) for pattern-consistency with
  ``GoogleDriveAdapter`` and minimal transitive deps.
- D3: Delegated permissions only; multi-tenant Azure AD app.
- D4: One connector-type, resolution-volgorde ``drive_id > site_url > /me/drive``.
- D5: ``site_url`` is resolved server-side via ``/sites/{hostname}:/{path}``
  once per process and cached.
- D6: No PDF-conversion in v1 — Office binaries are already ingestible.
- D7: ``source_url = driveItem.webUrl``.
- D9: ``sender_email`` / ``mentioned_emails[]`` from ``createdBy`` / ``lastModifiedBy``.
- R2.11: 429/503 → single retry using ``Retry-After`` (capped at 30s).
"""

# @MX:ANCHOR: [AUTO] BaseAdapter implementation for Microsoft 365 — SPEC-KB-MS-DOCS-001.
# @MX:REASON: External integration point (Microsoft Graph + Azure AD OAuth).
#             Delta-cursor + refresh-rotation + permissions must stay aligned.
# @MX:SPEC: SPEC-KB-MS-DOCS-001

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from app.adapters.base import BaseAdapter, DocumentRef
from app.adapters.oauth_base import OAuthAdapterBase
from app.core.config import Settings
from app.core.logging import get_logger
from app.services.portal_client import PortalClient

logger = get_logger(__name__)


# Microsoft Graph v1.0 endpoints (constants — never secrets).
_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_ME_DRIVE_DELTA = f"{_GRAPH_BASE}/me/drive/root/delta"

# OAuth scopes for delegated access (SPEC-KB-MS-DOCS-001 D3 / R1.2).
_MS_SCOPES = "offline_access User.Read Files.Read.All Sites.Read.All"

# 429/503 retry cap — never block a sync run longer than this on throttle backoff.
_RETRY_AFTER_CAP_SECONDS = 30.0

# MIME → content_type mapping (R2.6).
_MIME_CONTENT_TYPES: dict[str, str] = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "word_document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "excel_document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "powerpoint_document",
    "application/pdf": "pdf_document",
}


def _ms_token_url(tenant_id: str) -> str:
    """Construct the tenant-scoped OAuth token endpoint."""
    return f"https://login.microsoftonline.com/{tenant_id or 'common'}/oauth2/v2.0/token"


def _content_type_for_mime(mime: str) -> str:
    """Map a Graph driveItem mimeType to our internal content_type label."""
    return _MIME_CONTENT_TYPES.get(mime, "kb_article")


def _parse_site_url(site_url: str) -> tuple[str, str]:
    """Parse a SharePoint site URL into (hostname, path) segments.

    Input: ``https://contoso.sharepoint.com/sites/marketing``
    Output: ``("contoso.sharepoint.com", "/sites/marketing")``
    """
    parsed = urlparse(site_url.rstrip("/"))
    return parsed.netloc, parsed.path


class MsDocsAdapter(OAuthAdapterBase, BaseAdapter):
    """Microsoft 365 (SharePoint + OneDrive) connector adapter.

    Args:
        settings: App settings (reads ``ms_docs_client_id`` /
            ``ms_docs_client_secret`` / ``ms_docs_tenant_id`` for token refresh).
        portal_client: Used to write refreshed access + rotated refresh tokens
            back to the portal so subsequent restarts pick up the new values.
    """

    def __init__(self, settings: Settings, portal_client: PortalClient) -> None:
        super().__init__(settings=settings, portal_client=portal_client)
        # connector_id -> latest ``@odata.deltaLink`` captured during list_documents.
        self._latest_delta_link: dict[str, str] = {}
        # connector_id -> resolved SharePoint site id (from site_url lookup).
        self._resolved_sites: dict[str, str] = {}
        # DocumentRef identity -> adapter-owned metadata (sender_email, mentioned_emails).
        # Keyed by object id since DocumentRef is frozen.
        self._ref_metadata: dict[int, dict[str, Any]] = {}

    async def aclose(self) -> None:
        """No persistent resources to close."""

    # -- Config helpers -------------------------------------------------------

    @staticmethod
    def _extract_config(connector: Any) -> dict[str, Any]:
        """Normalise config dict with safe defaults."""
        config: dict[str, Any] = connector.config or {}
        return {
            "drive_id": (config.get("drive_id") or "").strip() or None,
            "site_url": (config.get("site_url") or "").strip() or None,
        }

    # -- OAuth refresh (SPEC-KB-MS-DOCS-001 R2.1) -----------------------------

    async def _refresh_oauth_token(
        self, connector: Any, refresh_token: str,
    ) -> dict[str, Any]:
        """Exchange a refresh_token for a new access_token against Microsoft.

        Microsoft rotates refresh_tokens periodically; when the response
        contains a new ``refresh_token``, ``OAuthAdapterBase.ensure_token``
        handles the writeback + in-memory rotation (see R9.2).

        Args:
            connector: Connector model (unused — kept for the OAuth base contract).
            refresh_token: Long-lived refresh token from the encrypted config.

        Returns:
            Raw JSON dict from Microsoft's token endpoint.
        """
        # @MX:NOTE: [AUTO] NEVER log the refresh_token or the returned access_token.
        payload = {
            "client_id": self._settings.ms_docs_client_id,
            "client_secret": self._settings.ms_docs_client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
            "scope": _MS_SCOPES,
        }
        token_url = _ms_token_url(self._settings.ms_docs_tenant_id)
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(token_url, data=payload)
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, dict):
            return {}
        # data is a genuinely untyped JSON dict; the caller treats keys as Any.
        result: dict[str, Any] = dict(data)  # pyright: ignore[reportUnknownArgumentType]
        return result

    # -- BaseAdapter interface ------------------------------------------------

    async def list_documents(
        self,
        connector: Any,
        cursor_context: dict[str, Any] | None = None,
    ) -> list[DocumentRef]:
        """List Graph drive items as DocumentRefs.

        First sync (no ``cursor_context.delta_link``) → call the appropriate
        ``/root/delta`` endpoint; paginate via ``@odata.nextLink`` until
        ``@odata.deltaLink`` appears, which we persist as the cursor.

        Incremental sync (has ``delta_link``) → call that URL verbatim; Graph
        returns only items changed since the previous deltaLink.

        Args:
            connector: Connector model with ``id`` and ``config``.
            cursor_context: Previous sync's cursor_state. ``delta_link`` drives
                incremental mode.

        Returns:
            DocumentRefs for items discovered this run. Adapter-owned metadata
            (sender_email, mentioned_emails) is stored in ``_ref_metadata``.
        """
        connector_id = str(connector.id)
        delta_link = (cursor_context or {}).get("delta_link")

        if delta_link:
            start_url: str = delta_link
            logger.info(
                "Listing MS drive changes since cursor (connector=%s)",
                connector_id,
            )
        else:
            start_url = await self._build_delta_root_url(connector)
            logger.info(
                "Listing MS drive items (first sync, connector=%s)",
                connector_id,
            )

        items, latest_delta = await self._drain_delta(start_url)
        if latest_delta:
            self._latest_delta_link[connector_id] = latest_delta

        refs: list[DocumentRef] = []
        for item in items:
            ref = self._item_to_document_ref(item)
            if ref is None:
                continue
            # Adapter-owned metadata (identifier capture, R2.5)
            meta = self._extract_metadata(item)
            self._ref_metadata[id(ref)] = meta
            refs.append(ref)

        logger.info(
            "Listed %d MS drive items (connector=%s, incremental=%s)",
            len(refs),
            connector_id,
            delta_link is not None,
        )
        return refs

    async def fetch_document(self, ref: DocumentRef, connector: Any) -> bytes:
        """Download a single drive item as bytes via ``/drive/items/{id}/content``.

        Args:
            ref: DocumentRef returned by ``list_documents``.
            connector: Connector model (for token refresh context).
        """
        url = f"{_GRAPH_BASE}/drive/items/{quote(ref.ref, safe='')}/content"
        return await self._graph_get_bytes(url, connector=connector)

    async def get_cursor_state(self, connector: Any) -> dict[str, Any]:
        """Return the stored deltaLink, or bootstrap one via a single Graph call.

        If a first sync hasn't produced a deltaLink yet, we call the correct
        ``/root/delta`` endpoint once to obtain one. This mirrors
        ``GoogleDriveAdapter.get_cursor_state`` which bootstraps via
        ``startPageToken``.
        """
        connector_id = str(connector.id)
        cached = self._latest_delta_link.get(connector_id)
        if cached:
            return {"delta_link": cached}

        start_url = await self._build_delta_root_url(connector)
        _items, latest_delta = await self._drain_delta(start_url)
        if latest_delta:
            self._latest_delta_link[connector_id] = latest_delta
            return {"delta_link": latest_delta}
        return {}

    # -- Public helper for tests + sync engine integration --------------------

    def _get_metadata_for_ref(self, ref: DocumentRef) -> dict[str, Any]:
        """Return adapter-owned metadata (sender_email, mentioned_emails) for a ref.

        The sync engine reads this after ``list_documents`` to populate the
        ``extra`` JSONB passthrough into knowledge-ingest (R2.10).
        """
        return self._ref_metadata.get(id(ref), {"sender_email": "", "mentioned_emails": []})

    # -- Delta URL construction + pagination ---------------------------------

    async def _build_delta_root_url(self, connector: Any) -> str:
        """Resolve the correct ``/root/delta`` endpoint for this connector.

        Resolution order (D4):
          1. ``config.drive_id`` → ``/drives/{drive_id}/root/delta``
          2. ``config.site_url`` → resolve to site-id, then ``/sites/{id}/drive/root/delta``
          3. default → ``/me/drive/root/delta``
        """
        cfg = self._extract_config(connector)

        if cfg["drive_id"]:
            return f"{_GRAPH_BASE}/drives/{quote(cfg['drive_id'], safe='!')}/root/delta"

        if cfg["site_url"]:
            site_id = await self._resolve_site_id(connector, cfg["site_url"])
            return f"{_GRAPH_BASE}/sites/{site_id}/drive/root/delta"

        return _ME_DRIVE_DELTA

    async def _resolve_site_id(self, connector: Any, site_url: str) -> str:
        """Resolve a SharePoint site URL to a Graph site-id; cache per connector.

        Calls ``/sites/{hostname}:/{path}`` → response.id. The id is cached in
        ``self._resolved_sites`` for the lifetime of this adapter instance.

        Raises:
            Generic exceptions propagated from the Graph call (403 admin-consent
            missing, 404 site not found) so the sync run's error handler can
            surface a helpful message.
        """
        connector_id = str(connector.id)
        cached = self._resolved_sites.get(connector_id)
        if cached:
            return cached

        hostname, path = _parse_site_url(site_url)
        resolve_url = f"{_GRAPH_BASE}/sites/{hostname}:{path}"
        logger.info(
            "Resolving SharePoint site (connector=%s, hostname=%s)",
            connector_id,
            hostname,
        )
        response = await self._graph_get_json(resolve_url, connector=connector)
        site_id = str(response.get("id", ""))
        if not site_id:
            raise ValueError(f"SharePoint site resolution returned empty id for {site_url}")
        self._resolved_sites[connector_id] = site_id
        return site_id

    async def _drain_delta(
        self, start_url: str,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Follow ``@odata.nextLink`` pages until ``@odata.deltaLink`` appears.

        Returns:
            (items, final_delta_link) — items from all pages concatenated,
            delta_link from the final page (or None if the response lacked one).
        """
        items: list[dict[str, Any]] = []
        delta_link: str | None = None
        url: str | None = start_url
        while url:
            page = await self._graph_get_json(url)
            items.extend(page.get("value", []))
            delta_link = page.get("@odata.deltaLink") or delta_link
            next_link = page.get("@odata.nextLink")
            # Graph can return the literal ``false`` for empty-change responses
            url = next_link if isinstance(next_link, str) else None
        return items, delta_link

    # -- DocumentRef + metadata extraction -----------------------------------

    def _item_to_document_ref(self, item: dict[str, Any]) -> DocumentRef | None:
        """Convert a Graph driveItem JSON payload to a DocumentRef.

        Skips items without a ``file`` facet (folders, packages) since we only
        ingest leaf documents. Returns None for those.
        """
        if "file" not in item:
            return None

        mime = str(item.get("file", {}).get("mimeType", ""))
        size_raw = item.get("size", 0)
        try:
            size = int(size_raw) if size_raw is not None else 0
        except (TypeError, ValueError):
            size = 0

        return DocumentRef(
            path=str(item.get("name", "") or item.get("id", "")),
            ref=str(item.get("id", "")),
            size=size,
            content_type=_content_type_for_mime(mime),
            source_ref=str(item.get("id", "")),
            source_url=str(item.get("webUrl", "")),
            last_edited=str(item.get("lastModifiedDateTime", "")),
        )

    @staticmethod
    def _extract_metadata(item: dict[str, Any]) -> dict[str, Any]:
        """Extract identifier-capture metadata from a Graph driveItem (R2.5 / R2.10).

        sender_email = lastModifiedBy.user.email (fallback to createdBy.user.email).
        mentioned_emails = deduped [createdBy.email, lastModifiedBy.email], empties filtered.
        """
        created_email = (
            item.get("createdBy", {}).get("user", {}).get("email", "") or ""
        )
        modified_email = (
            item.get("lastModifiedBy", {}).get("user", {}).get("email", "") or ""
        )
        sender = modified_email or created_email
        mentioned = [e for e in {created_email, modified_email} if e]
        return {"sender_email": sender, "mentioned_emails": mentioned}

    # -- Graph HTTP helpers (auth + retry) -----------------------------------

    # @MX:ANCHOR: Single HTTP request codepath for all Graph calls.
    # @MX:REASON: fan_in >= 3 (list, resolve, get_cursor_state).
    #             Auth header + Retry-After + 429 handling must stay identical.
    async def _graph_get_json(
        self, url: str, connector: Any | None = None,
    ) -> dict[str, Any]:
        """GET a JSON response from the Graph API with auth + 429 retry.

        One retry on 429/503 using ``Retry-After`` (capped at
        ``_RETRY_AFTER_CAP_SECONDS``); after a second failure, the exception
        propagates and the scheduler does exponential backoff (R2.11).

        Args:
            url: Fully-qualified Graph URL (or a deltaLink echoed from Graph).
            connector: Optional — needed for token refresh when called outside
                list_documents. In practice ensure_token uses the last-seen
                connector via the cache.
        """
        headers = await self._auth_headers(connector)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
            if response.status_code in (429, 503):
                retry_after = self._parse_retry_after(response)
                logger.warning(
                    "Graph throttled (status=%s, retry_after=%.1fs, url=%s)",
                    response.status_code, retry_after, url,
                )
                await asyncio.sleep(retry_after)
                response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, dict):
            return {}
        # data is a genuinely untyped JSON dict; the caller treats keys as Any.
        result: dict[str, Any] = dict(data)  # pyright: ignore[reportUnknownArgumentType]
        return result

    async def _graph_get_bytes(
        self, url: str, connector: Any | None = None,
    ) -> bytes:
        """GET binary content from the Graph API (used by fetch_document).

        Follows 302 redirects to preauthenticated download URLs that Graph may
        return for large files.
        """
        headers = await self._auth_headers(connector)
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            if response.status_code in (429, 503):
                retry_after = self._parse_retry_after(response)
                logger.warning(
                    "Graph throttled on content fetch (status=%s, retry_after=%.1fs)",
                    response.status_code, retry_after,
                )
                await asyncio.sleep(retry_after)
                response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.content

    async def _auth_headers(self, connector: Any | None) -> dict[str, str]:
        """Return an ``Authorization: Bearer <token>`` header for Graph calls.

        Uses the most-recently cached access token. If ``connector`` is given
        and its token has expired, ``ensure_token`` refreshes first.
        """
        if connector is not None:
            token = await self.ensure_token(connector)
        else:
            # Called without connector context (e.g. following a deltaLink within
            # a single list_documents run where ensure_token already ran). Fall
            # back to any cached token — callers that have no cached token
            # never reach this path.
            cached = next(iter(self._token_cache.values()), None)
            token = cached[0] if cached else ""
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def _parse_retry_after(response: httpx.Response) -> float:
        """Parse the ``Retry-After`` header, capped at _RETRY_AFTER_CAP_SECONDS."""
        raw = response.headers.get("Retry-After", "1")
        try:
            seconds = float(raw)
        except (TypeError, ValueError):
            seconds = 1.0
        return min(max(seconds, 0.0), _RETRY_AFTER_CAP_SECONDS)
