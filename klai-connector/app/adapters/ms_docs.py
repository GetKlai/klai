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
from app.adapters.oauth_base import (
    ConnectorLike,
    OAuthAdapterBase,
    check_invalid_grant_and_raise,
)
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

# Per-file upper bound. Mirrors GitHubAdapter's _MAX_FILE_SIZE pattern.
# Files larger than this are skipped during list_documents and never
# downloaded; ingest-side parsing of very large Office binaries blows
# memory and rarely produces useful chunks for retrieval.
_MAX_FILE_SIZE = 200 * 1024 * 1024  # 200 MB

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
        # DocumentRef.ref (Graph driveItem id, stable) -> adapter-owned metadata
        # (sender_email, mentioned_emails). Keyed by the Graph id rather than
        # Python id() so entries survive GC and can be looked up safely even
        # if a caller rebuilds DocumentRef instances.
        self._ref_metadata: dict[str, dict[str, Any]] = {}

    async def aclose(self) -> None:
        """No persistent resources to close."""

    # -- Config helpers -------------------------------------------------------

    @staticmethod
    def _extract_config(connector: Any) -> dict[str, Any]:
        """Normalise config dict with safe defaults.

        Scoping options, in priority order (first match wins):

        1. ``item_ids: list[str]`` — pinned-item mode. Sync ONLY those
           specific driveItems by id, no delta endpoint involved. Used
           when the user multi-selects files in the picker.
        2. ``folder_id: str`` — subtree mode. Sync everything under that
           folder via ``/items/{id}/delta``.
        3. Neither set — whole-drive mode via ``/root/delta``.

        ``drive_id`` / ``site_url`` only resolve WHICH drive to act on;
        they compose with any of the three scoping modes above.
        """
        config: dict[str, Any] = connector.config or {}
        raw_item_ids = config.get("item_ids") or []
        if not isinstance(raw_item_ids, list):
            raw_item_ids = []
        item_ids: list[str] = [str(i).strip() for i in raw_item_ids if isinstance(i, (str, int)) and str(i).strip()]
        return {
            "drive_id": (config.get("drive_id") or "").strip() or None,
            "site_url": (config.get("site_url") or "").strip() or None,
            "folder_id": (config.get("folder_id") or "").strip() or None,
            "item_ids": item_ids,
        }

    # -- OAuth refresh (SPEC-KB-MS-DOCS-001 R2.1) -----------------------------

    async def _refresh_oauth_token(
        self,
        connector: ConnectorLike,
        refresh_token: str,
    ) -> dict[str, Any]:
        """Exchange a refresh_token for a new access_token against Microsoft.

        Microsoft rotates refresh_tokens periodically; when the response
        contains a new ``refresh_token``, ``OAuthAdapterBase.ensure_token``
        handles the writeback + in-memory rotation (see R9.2).

        Args:
            connector: Connector model (used for connector_id in error messages).
            refresh_token: Long-lived refresh token from the encrypted config.

        Returns:
            Raw JSON dict from Microsoft's token endpoint.

        Raises:
            OAuthReconnectRequiredError: Microsoft returned ``invalid_grant``
                (refresh_token revoked by user password change, admin consent
                revoke, or past grace window after rotation). The caller
                (sync engine) should mark the connector as auth_error so the
                portal can surface a "Reconnect Microsoft" affordance.
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
            # Shared helper translates 400 + error=invalid_grant to a typed
            # OAuthReconnectRequiredError; other 400s fall through to
            # raise_for_status (generic HTTPStatusError).
            check_invalid_grant_and_raise(
                response,
                provider="Microsoft",
                connector_id=connector.id,
            )
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
        cfg = self._extract_config(connector)

        # Pinned-item mode: skip delta entirely, fetch each id directly.
        # Trades off incremental efficiency for absolute precision —
        # appropriate when the user multi-selected specific files.
        if cfg["item_ids"]:
            refs = await self._list_pinned_items(connector, cfg["item_ids"])
            logger.info("Listed %d MS pinned items (connector=%s)", len(refs), connector_id)
            return refs

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

        items, latest_delta = await self._drain_delta(start_url, connector=connector)
        if latest_delta:
            self._latest_delta_link[connector_id] = latest_delta

        refs: list[DocumentRef] = []
        for item in items:
            ref = self._item_to_document_ref(item)
            if ref is None:
                continue
            # Adapter-owned metadata (identifier capture, R2.5)
            meta = self._extract_metadata(item)
            self._ref_metadata[ref.ref] = meta
            refs.append(ref)

        logger.info(
            "Listed %d MS drive items (connector=%s, incremental=%s)",
            len(refs),
            connector_id,
            delta_link is not None,
        )
        return refs

    async def _list_pinned_items(self, connector: Any, item_ids: list[str]) -> list[DocumentRef]:
        """Fetch a set of specific driveItems by id, ignore delta.

        Used when ``config.item_ids`` is set (the user multi-selected
        individual files in the picker). Each sync re-fetches all pinned
        items — there is no incremental cursor in this mode. Items that
        404 (file deleted) are silently skipped.
        """
        cfg = self._extract_config(connector)
        # Resolve the drive-prefix once; the actual item resolution by id
        # mirrors ``fetch_document``.
        if cfg["drive_id"]:
            drive_prefix = f"{_GRAPH_BASE}/drives/{quote(cfg['drive_id'], safe='!')}"
        elif cfg["site_url"]:
            site_id = await self._resolve_site_id(connector, cfg["site_url"])
            drive_prefix = f"{_GRAPH_BASE}/sites/{site_id}/drive"
        else:
            drive_prefix = f"{_GRAPH_BASE}/me/drive"

        refs: list[DocumentRef] = []
        for raw_id in item_ids:
            item_id = quote(raw_id, safe="")
            url = f"{drive_prefix}/items/{item_id}"
            try:
                item = await self._graph_get_json(url, connector=connector)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    logger.warning("Pinned MS item not found, skipping (id=%s)", raw_id)
                    continue
                raise
            ref = self._item_to_document_ref(item)
            if ref is None:
                continue
            self._ref_metadata[ref.ref] = self._extract_metadata(item)
            refs.append(ref)
        return refs

    async def fetch_document(self, ref: DocumentRef, connector: Any) -> bytes:
        """Download a single drive item as bytes.

        Resolution order mirrors ``_build_delta_root_url`` (D4):
          1. ``config.drive_id`` → ``/drives/{drive_id}/items/{id}/content``
          2. ``config.site_url`` → resolve to site-id, then
             ``/sites/{site_id}/drive/items/{id}/content``
          3. default → ``/me/drive/items/{id}/content``

        The bare ``/drive/items/{id}/content`` (no ``/me/`` prefix) is NOT a
        valid Graph endpoint and returns 404. Bug shipped in initial
        SPEC-KB-MS-DOCS-001 implementation; fixed on first end-to-end
        production test 2026-05-13.

        Args:
            ref: DocumentRef returned by ``list_documents``.
            connector: Connector model (for token refresh context).
        """
        item_id = quote(ref.ref, safe="")
        cfg = self._extract_config(connector)

        if cfg["drive_id"]:
            url = f"{_GRAPH_BASE}/drives/{quote(cfg['drive_id'], safe='!')}/items/{item_id}/content"
        elif cfg["site_url"]:
            site_id = await self._resolve_site_id(connector, cfg["site_url"])
            url = f"{_GRAPH_BASE}/sites/{site_id}/drive/items/{item_id}/content"
        else:
            url = f"{_GRAPH_BASE}/me/drive/items/{item_id}/content"

        return await self._graph_get_bytes(url, connector=connector)

    async def get_cursor_state(self, connector: Any) -> dict[str, Any]:
        """Return the stored deltaLink, or bootstrap one via a single Graph call.

        If a first sync hasn't produced a deltaLink yet, we call the correct
        ``/root/delta`` endpoint once to obtain one. This mirrors
        ``GoogleDriveAdapter.get_cursor_state`` which bootstraps via
        ``startPageToken``.

        Pinned-item mode (``config.item_ids`` set) has no delta cursor —
        each sync fetches the same N items unconditionally. Return ``{}``
        so the sync engine doesn't persist a stale deltaLink.
        """
        cfg = self._extract_config(connector)
        if cfg["item_ids"]:
            return {}

        connector_id = str(connector.id)
        cached = self._latest_delta_link.get(connector_id)
        if cached:
            return {"delta_link": cached}

        start_url = await self._build_delta_root_url(connector)
        _items, latest_delta = await self._drain_delta(start_url, connector=connector)
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
        return self._ref_metadata.get(ref.ref, {"sender_email": "", "mentioned_emails": []})

    async def list_folders(self, connector: Any, parent_id: str | None = None) -> list[dict[str, Any]]:
        """List children under ``parent_id`` (root when None).

        Powers the post-OAuth picker in the portal. Returns BOTH folders
        and files so the user can see what's inside each subtree before
        picking a folder. Only folders are selectable in v1 — files come
        along automatically when their parent folder is chosen.

        Args:
            connector: Connector model (for token + drive resolution).
            parent_id: Graph driveItem id of the parent folder, or ``None``
                / empty string for the drive root.

        Returns:
            ``[{"id", "name", "kind": "folder"|"file", "child_count": int}, ...]``.
            ``child_count`` is 0 for files. Sort: folders first by name,
            then files by name (mirrors OneDrive's default).

        Raises:
            httpx.HTTPStatusError: Graph error propagated for the caller to
                map to a 4xx/5xx HTTP response.
        """
        cfg = self._extract_config(connector)
        anchor = f"items/{quote(parent_id, safe='')}" if parent_id else "root"

        if cfg["drive_id"]:
            base = f"{_GRAPH_BASE}/drives/{quote(cfg['drive_id'], safe='!')}/{anchor}"
        elif cfg["site_url"]:
            site_id = await self._resolve_site_id(connector, cfg["site_url"])
            base = f"{_GRAPH_BASE}/sites/{site_id}/drive/{anchor}"
        else:
            base = f"{_GRAPH_BASE}/me/drive/{anchor}"

        # ``$select`` keeps the payload small; ``$top=200`` is the page cap
        # the picker UX supports without pagination. If a folder turns out
        # to have >200 children in practice, add nextLink-following.
        url = f"{base}/children?$select=id,name,folder,file&$top=200"
        data = await self._graph_get_json(url, connector=connector)
        items = data.get("value", []) if isinstance(data, dict) else []

        folders: list[dict[str, Any]] = []
        files: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", ""))
            item_id = str(item.get("id", ""))
            if "folder" in item:
                folder_facet = item.get("folder") or {}
                child_count_raw = folder_facet.get("childCount", 0) if isinstance(folder_facet, dict) else 0
                try:
                    child_count = int(child_count_raw) if child_count_raw is not None else 0
                except (TypeError, ValueError):
                    child_count = 0
                folders.append(
                    {
                        "id": item_id,
                        "name": name,
                        "kind": "folder",
                        "child_count": child_count,
                    }
                )
            elif "file" in item:
                files.append(
                    {
                        "id": item_id,
                        "name": name,
                        "kind": "file",
                        "child_count": 0,
                    }
                )
            # Items without folder or file facet (packages, etc.) are skipped.

        folders.sort(key=lambda x: x["name"].lower())
        files.sort(key=lambda x: x["name"].lower())
        return folders + files

    # -- Delta URL construction + pagination ---------------------------------

    async def _build_delta_root_url(self, connector: Any) -> str:
        """Resolve the correct ``/root/delta`` endpoint for this connector.

        Resolution order (D4):
          1. ``config.drive_id`` → ``/drives/{drive_id}/root/delta``
          2. ``config.site_url`` → resolve to site-id, then ``/sites/{id}/drive/root/delta``
          3. default → ``/me/drive/root/delta``

        When ``config.folder_id`` is set, the ``/root`` segment is replaced
        by ``/items/{folder_id}`` so the delta is scoped to that subtree.
        Graph supports this on every drive variant (personal, SharePoint,
        drive-id).
        """
        cfg = self._extract_config(connector)
        folder_id = cfg["folder_id"]
        # Anchor segment: ``/root`` for whole drive, ``/items/{id}`` when scoped.
        anchor = f"items/{quote(folder_id, safe='')}" if folder_id else "root"

        if cfg["drive_id"]:
            return f"{_GRAPH_BASE}/drives/{quote(cfg['drive_id'], safe='!')}/{anchor}/delta"

        if cfg["site_url"]:
            site_id = await self._resolve_site_id(connector, cfg["site_url"])
            return f"{_GRAPH_BASE}/sites/{site_id}/drive/{anchor}/delta"

        return f"{_GRAPH_BASE}/me/drive/{anchor}/delta"

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
        self,
        start_url: str,
        connector: ConnectorLike | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Follow ``@odata.nextLink`` pages until ``@odata.deltaLink`` appears.

        Args:
            start_url: First Graph delta URL to fetch.
            connector: Required on the first call after process start so
                ``ensure_token`` can refresh and populate the token cache.
                Without it, the first Graph call emits ``Authorization: Bearer ``
                (empty token) and httpx rejects it locally with
                ``LocalProtocolError: Illegal header value`` -- same fail-mode
                tracked in pitfalls/process-rules.md as ``empty-secret-fail-open``.

        Returns:
            (items, final_delta_link) -- items from all pages concatenated,
            delta_link from the final page (or None if the response lacked one).
        """
        items: list[dict[str, Any]] = []
        delta_link: str | None = None
        url: str | None = start_url
        while url:
            page = await self._graph_get_json(url, connector=connector)
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

        Also skips files larger than ``_MAX_FILE_SIZE`` (200 MB) — downloading
        and parsing them blows ingest memory and rarely produces useful
        retrieval chunks. The skipped item is logged so operators can spot
        it without having to read the database.
        """
        if "file" not in item:
            return None

        mime = str(item.get("file", {}).get("mimeType", ""))
        size_raw = item.get("size", 0)
        try:
            size = int(size_raw) if size_raw is not None else 0
        except (TypeError, ValueError):
            size = 0

        if size > _MAX_FILE_SIZE:
            logger.warning(
                "Skipping oversized MS drive item (id=%s, name=%s, size=%d bytes, limit=%d bytes)",
                item.get("id"),
                item.get("name"),
                size,
                _MAX_FILE_SIZE,
            )
            return None

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
        created_email = item.get("createdBy", {}).get("user", {}).get("email", "") or ""
        modified_email = item.get("lastModifiedBy", {}).get("user", {}).get("email", "") or ""
        sender = modified_email or created_email
        mentioned = [e for e in {created_email, modified_email} if e]
        return {"sender_email": sender, "mentioned_emails": mentioned}

    # -- Graph HTTP helpers (auth + retry) -----------------------------------

    # @MX:ANCHOR: Single HTTP request codepath for all Graph calls.
    # @MX:REASON: fan_in >= 3 (list, resolve, get_cursor_state, fetch_document).
    #             Auth header + Retry-After + 429 handling must stay identical.
    async def _graph_request(
        self,
        url: str,
        *,
        connector: ConnectorLike | None = None,
        timeout: float = 30.0,
        follow_redirects: bool = False,
    ) -> httpx.Response:
        """Issue a GET against the Graph API with auth + one 429/503 retry.

        Single codepath used by both JSON and byte-stream consumers. One
        retry on 429/503 using ``Retry-After`` (capped at
        ``_RETRY_AFTER_CAP_SECONDS``); after a second failure the exception
        propagates so the scheduler does exponential backoff (R2.11).

        Args:
            url: Fully-qualified Graph URL (or a deltaLink echoed from Graph).
            connector: Optional — needed for token refresh when called outside
                list_documents. In practice ensure_token uses the last-seen
                connector via the cache.
            timeout: httpx timeout; callers raise it for content downloads.
            follow_redirects: Graph may 302 content requests to
                preauthenticated URLs; set True for binary fetches.

        Returns:
            The raised-for-status httpx Response. Caller decides ``.json()``
            vs ``.content`` shape.
        """
        headers = await self._auth_headers(connector)
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=follow_redirects,
        ) as client:
            response = await client.get(url, headers=headers)
            if response.status_code in (429, 503):
                retry_after = self._parse_retry_after(response)
                logger.warning(
                    "Graph throttled (status=%s, retry_after=%.1fs, url=%s)",
                    response.status_code,
                    retry_after,
                    url,
                )
                await asyncio.sleep(retry_after)
                response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response

    async def _graph_get_json(
        self,
        url: str,
        connector: ConnectorLike | None = None,
    ) -> dict[str, Any]:
        """Thin wrapper over ``_graph_request`` returning a JSON dict."""
        response = await self._graph_request(url, connector=connector)
        data = response.json()
        if not isinstance(data, dict):
            return {}
        # data is a genuinely untyped JSON dict; the caller treats keys as Any.
        result: dict[str, Any] = dict(data)  # pyright: ignore[reportUnknownArgumentType]
        return result

    async def _graph_get_bytes(
        self,
        url: str,
        connector: ConnectorLike | None = None,
    ) -> bytes:
        """Thin wrapper over ``_graph_request`` returning raw bytes.

        Uses a longer timeout + follow_redirects since Graph can 302 binary
        downloads to preauthenticated blob URLs for large files.
        """
        response = await self._graph_request(
            url,
            connector=connector,
            timeout=60.0,
            follow_redirects=True,
        )
        return response.content

    async def _auth_headers(self, connector: ConnectorLike | None) -> dict[str, str]:
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
