"""Google Drive connector adapter (SPEC-KB-025).

Syncs Google Drive files as knowledge documents. Supports:

- Full sync via ``files.list`` with optional folder/mimeType filtering.
- Incremental sync via the ``changes.list`` API, using the
  ``newStartPageToken`` returned on the previous run as a cursor.
- Google-native documents (Docs, Sheets, Slides) are exported as DOCX /
  XLSX / PPTX binaries via ``files.export``; everything else is downloaded
  with ``alt=media``.
- OAuth access tokens are refreshed against Google's token endpoint when
  cached tokens expire. New access tokens are written back to the portal
  via ``PortalClient.update_credentials`` so the encrypted row stays fresh
  across restarts.

Design notes:
- ``_token_cache`` holds only short-lived access tokens. The refresh token
  lives in the encrypted connector config and is NEVER logged.
- ``_latest_page_token`` records the ``newStartPageToken`` from the most
  recent sync, which the sync engine persists via ``get_cursor_state``.
- The full Drive v3 API surface (startPageToken + changes + files) is kept
  behind small private helpers that can be swapped out in tests.
"""

# @MX:ANCHOR: [AUTO] BaseAdapter implementation for Google Drive -- SPEC-KB-025.
# @MX:REASON: External integration point (Google OAuth + Drive v3).
#             Auth + pagination + export/mime mapping must stay consistent.
# @MX:SPEC: SPEC-KB-025

from __future__ import annotations

from typing import Any

import httpx

from app.adapters.base import BaseAdapter, DocumentRef
from app.adapters.oauth_base import OAuthAdapterBase
from app.core.config import Settings
from app.core.logging import get_logger
from app.services.portal_client import PortalClient

logger = get_logger(__name__)


# Google Drive v3 endpoints (constants -- never secrets).
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
_DRIVE_CHANGES_URL = "https://www.googleapis.com/drive/v3/changes"
_DRIVE_START_PAGE_TOKEN_URL = "https://www.googleapis.com/drive/v3/changes/startPageToken"

# Fields requested for files.list / changes.list. Kept minimal — we only need
# what the DocumentRef contract requires.
_FILE_FIELDS = "id, name, mimeType, modifiedTime, webViewLink, size"
_LIST_FIELDS = f"nextPageToken, files({_FILE_FIELDS})"
_CHANGES_FIELDS = f"newStartPageToken, nextPageToken, changes(fileId, removed, file({_FILE_FIELDS}))"

# Mime-type → export mime mapping for Google-native file types. We prefer
# Office formats since the knowledge-ingest pipeline understands them well.
_GOOGLE_EXPORT_MIMES: dict[str, str] = {
    "application/vnd.google-apps.document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ),
    "application/vnd.google-apps.presentation": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    ),
}

# Which mime types are Google-native (require export) vs binary downloads.
_GOOGLE_NATIVE_MIMES = set(_GOOGLE_EXPORT_MIMES.keys())

# Fallback max_files when config omits it — keeps single-run latency bounded.
_DEFAULT_MAX_FILES = 500


def _content_type_for_mime(mime: str) -> str:
    """Map a Google Drive mimeType to our internal content_type label."""
    if mime == "application/vnd.google-apps.document":
        return "google_doc"
    if mime == "application/vnd.google-apps.spreadsheet":
        return "google_sheet"
    if mime == "application/vnd.google-apps.presentation":
        return "google_slides"
    if mime == "application/pdf":
        return "pdf_document"
    return "kb_article"


class GoogleDriveAdapter(OAuthAdapterBase, BaseAdapter):
    """Google Drive connector adapter backed by OAuth 2.0 + Drive v3.

    Args:
        settings: App settings (reads ``google_drive_client_id`` /
            ``google_drive_client_secret`` for token refresh).
        portal_client: Used to write refreshed access tokens back to the
            portal so subsequent restarts pick up the new value.
    """

    def __init__(self, settings: Settings, portal_client: PortalClient) -> None:
        super().__init__(settings=settings, portal_client=portal_client)
        # connector_id -> latest ``newStartPageToken`` returned by changes.list
        self._latest_page_token: dict[str, str] = {}

    async def aclose(self) -> None:
        """No persistent resources to close."""

    # -- Config helpers -------------------------------------------------------

    @staticmethod
    def _extract_config(connector: Any) -> dict[str, Any]:
        """Normalise config dict with safe defaults."""
        config: dict[str, Any] = connector.config or {}
        return {
            "folder_id": config.get("folder_id") or None,
            "max_files": int(config.get("max_files") or _DEFAULT_MAX_FILES),
        }

    # -- OAuth refresh --------------------------------------------------------

    async def _refresh_oauth_token(
        self, connector: Any, refresh_token: str,
    ) -> dict[str, Any]:
        """Exchange a refresh_token for a new access_token against Google.

        Args:
            connector: Connector model (unused here — kept for the OAuth base
                contract so other providers can include tenant context).
            refresh_token: Long-lived refresh token from the encrypted config.

        Returns:
            Raw JSON dict from Google's token endpoint.
        """
        # @MX:NOTE: [AUTO] NEVER log the refresh_token or the returned access_token.
        payload = {
            "client_id": self._settings.google_drive_client_id,
            "client_secret": self._settings.google_drive_client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(_GOOGLE_TOKEN_URL, data=payload)
            response.raise_for_status()
            data = response.json()
        return data if isinstance(data, dict) else {}

    # -- BaseAdapter interface ------------------------------------------------

    async def list_documents(
        self,
        connector: Any,
        cursor_context: dict[str, Any] | None = None,
    ) -> list[DocumentRef]:
        """List Drive files as DocumentRefs.

        First sync (no ``cursor_context.page_token``) → ``files.list``.
        Incremental sync (has cursor) → ``changes.list``.

        Args:
            connector: Connector model with ``id`` and ``config``.
            cursor_context: Previous sync's cursor_state. ``page_token`` drives
                incremental mode.

        Returns:
            DocumentRefs for files discovered this run, capped to
            ``config.max_files``.
        """
        cfg = self._extract_config(connector)
        connector_id = str(connector.id)
        max_files: int = cfg["max_files"]
        page_token = (cursor_context or {}).get("page_token") if cursor_context else None

        if page_token:
            logger.info(
                "Listing Google Drive changes since cursor (connector=%s)",
                connector_id,
            )
            response = await self._list_changes(connector, page_token=page_token)
            files = [c.get("file") for c in response.get("changes", []) if c.get("file")]
            new_cursor = response.get("newStartPageToken")
            if new_cursor:
                self._latest_page_token[connector_id] = new_cursor
        else:
            logger.info(
                "Listing Google Drive files (first sync, connector=%s, folder=%s)",
                connector_id,
                cfg["folder_id"],
            )
            response = await self._list_files(connector, folder_id=cfg["folder_id"])
            files = list(response.get("files", []))

        refs: list[DocumentRef] = []
        for file in files[:max_files]:
            if not file:
                continue
            mime = file.get("mimeType", "")
            size_str = file.get("size")
            try:
                size = int(size_str) if size_str else 0
            except (TypeError, ValueError):
                size = 0
            refs.append(
                DocumentRef(
                    path=file.get("name", "") or file.get("id", ""),
                    ref=file.get("id", ""),
                    size=size,
                    content_type=_content_type_for_mime(mime),
                    source_ref=file.get("id", ""),
                    source_url=file.get("webViewLink", ""),
                    last_edited=file.get("modifiedTime", ""),
                )
            )

        logger.info(
            "Listed %d Google Drive files (connector=%s, incremental=%s)",
            len(refs),
            connector_id,
            page_token is not None,
        )
        return refs

    async def fetch_document(self, ref: DocumentRef, connector: Any) -> bytes:
        """Download a single Drive file as bytes.

        Google-native docs → ``files.export`` with the DOCX / XLSX / PPTX
        mime. Everything else → ``files.get?alt=media``.

        Args:
            ref: DocumentRef returned by ``list_documents``.
            connector: Connector model (for token refresh context).
        """
        if ref.content_type in {"google_doc", "google_sheet", "google_slides"}:
            mime_map = {
                "google_doc": _GOOGLE_EXPORT_MIMES["application/vnd.google-apps.document"],
                "google_sheet": _GOOGLE_EXPORT_MIMES["application/vnd.google-apps.spreadsheet"],
                "google_slides": _GOOGLE_EXPORT_MIMES["application/vnd.google-apps.presentation"],
            }
            export_mime = mime_map[ref.content_type]
            return await self._export_file(connector, file_id=ref.ref, mime_type=export_mime)
        return await self._download_file(connector, file_id=ref.ref)

    async def get_cursor_state(self, connector: Any) -> dict[str, Any]:
        """Return the stored ``page_token`` (if any) for the next run.

        The value comes from ``newStartPageToken`` captured during the most
        recent ``list_documents`` call. If a first sync hasn't happened yet
        (or returned no cursor), we bootstrap by calling
        ``startPageToken`` against the Drive API so the NEXT run can use
        incremental mode.
        """
        connector_id = str(connector.id)
        cached = self._latest_page_token.get(connector_id)
        if cached:
            return {"page_token": cached}
        token = await self._fetch_start_page_token(connector)
        if token:
            self._latest_page_token[connector_id] = token
            return {"page_token": token}
        return {}

    # -- Drive API helpers ---------------------------------------------------

    async def _list_files(
        self, connector: Any, folder_id: str | None,
    ) -> dict[str, Any]:
        """Call ``files.list`` with pagination rolled up into one response dict."""
        token = await self.ensure_token(connector)
        params: dict[str, Any] = {
            "pageSize": 100,
            "fields": _LIST_FIELDS,
            "spaces": "drive",
            "includeItemsFromAllDrives": "true",
            "supportsAllDrives": "true",
        }
        if folder_id:
            # Restrict to files whose immediate parent matches the configured folder.
            params["q"] = f"'{folder_id}' in parents and trashed = false"
        else:
            params["q"] = "trashed = false"

        all_files: list[dict[str, Any]] = []
        next_token: str | None = None
        async with httpx.AsyncClient(timeout=30.0) as client:
            while True:
                if next_token:
                    params["pageToken"] = next_token
                response = await client.get(
                    _DRIVE_FILES_URL,
                    headers={"Authorization": f"Bearer {token}"},
                    params=params,
                )
                response.raise_for_status()
                data = response.json()
                all_files.extend(data.get("files", []))
                next_token = data.get("nextPageToken")
                if not next_token:
                    break
        return {"files": all_files}

    async def _list_changes(
        self, connector: Any, page_token: str,
    ) -> dict[str, Any]:
        """Call ``changes.list`` starting at ``page_token``, rolling up pages."""
        token = await self.ensure_token(connector)
        params: dict[str, Any] = {
            "pageToken": page_token,
            "pageSize": 100,
            "fields": _CHANGES_FIELDS,
            "includeRemoved": "false",
            "includeItemsFromAllDrives": "true",
            "supportsAllDrives": "true",
        }

        all_changes: list[dict[str, Any]] = []
        new_start_page_token: str | None = None
        next_token: str | None = None
        async with httpx.AsyncClient(timeout=30.0) as client:
            while True:
                if next_token:
                    params["pageToken"] = next_token
                response = await client.get(
                    _DRIVE_CHANGES_URL,
                    headers={"Authorization": f"Bearer {token}"},
                    params=params,
                )
                response.raise_for_status()
                data = response.json()
                all_changes.extend(data.get("changes", []))
                if data.get("newStartPageToken"):
                    new_start_page_token = data["newStartPageToken"]
                next_token = data.get("nextPageToken")
                if not next_token:
                    break
        result: dict[str, Any] = {"changes": all_changes}
        if new_start_page_token is not None:
            result["newStartPageToken"] = new_start_page_token
        return result

    async def _fetch_start_page_token(self, connector: Any) -> str:
        """Bootstrap a starting cursor via ``changes/startPageToken``."""
        token = await self.ensure_token(connector)
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                _DRIVE_START_PAGE_TOKEN_URL,
                headers={"Authorization": f"Bearer {token}"},
                params={"supportsAllDrives": "true"},
            )
            response.raise_for_status()
            data = response.json()
        start_token = data.get("startPageToken", "") if isinstance(data, dict) else ""
        return str(start_token) if start_token else ""

    async def _export_file(
        self, connector: Any, file_id: str, mime_type: str,
    ) -> bytes:
        """Export a Google-native doc to the given Office mime type."""
        token = await self.ensure_token(connector)
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(
                f"{_DRIVE_FILES_URL}/{file_id}/export",
                headers={"Authorization": f"Bearer {token}"},
                params={"mimeType": mime_type},
            )
            response.raise_for_status()
            return response.content

    async def _download_file(self, connector: Any, file_id: str) -> bytes:
        """Download a binary Drive file via ``alt=media``."""
        token = await self.ensure_token(connector)
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(
                f"{_DRIVE_FILES_URL}/{file_id}",
                headers={"Authorization": f"Bearer {token}"},
                params={"alt": "media", "supportsAllDrives": "true"},
            )
            response.raise_for_status()
            return response.content
