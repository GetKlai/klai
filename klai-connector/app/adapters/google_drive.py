"""Google Drive connector adapter.

Syncs Google Drive files as knowledge documents via the Drive API v3.
Supports Google Docs (export as text), Sheets (export as CSV),
Slides (export as text), PDF and text files. Uses service account
authentication via JWT.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from app.adapters._google_auth import get_google_access_token
from app.adapters.base import BaseAdapter, DocumentRef
from app.core.config import Settings

logger = structlog.get_logger(__name__)

_DRIVE_API = "https://www.googleapis.com/drive/v3"

# Google Workspace MIME types and their export targets.
_GOOGLE_EXPORT_MIMES: dict[str, tuple[str, str]] = {
    "application/vnd.google-apps.document": ("text/plain", "kb_article"),
    "application/vnd.google-apps.spreadsheet": ("text/csv", "structured_data"),
    "application/vnd.google-apps.presentation": ("text/plain", "kb_article"),
}

# Regular file MIME types we support.
_SUPPORTED_MIMES: dict[str, str] = {
    "application/pdf": "pdf_document",
    "text/plain": "kb_article",
    "text/markdown": "kb_article",
    "text/csv": "structured_data",
    "text/html": "kb_article",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "kb_article",
}

_DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


class GoogleDriveAdapter(BaseAdapter):
    """Google Drive connector adapter.

    Authenticates via a service account JSON key. Lists files from
    specified folders (or the entire drive) and fetches content.

    Config fields (from connector.config):
        service_account_json (required): JSON string of the service account key.
        folder_ids (optional): List of folder IDs to sync.
            Empty = list all accessible files.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def aclose(self) -> None:
        """No persistent resources to close."""

    @staticmethod
    def _extract_config(connector: Any) -> dict[str, Any]:
        """Extract and validate Google Drive config."""
        config: dict[str, Any] = connector.config
        sa_json = config.get("service_account_json", "")
        if not sa_json:
            raise ValueError("Google Drive config missing 'service_account_json'")
        return {
            "service_account_json": sa_json,
            "folder_ids": config.get("folder_ids", []),
        }

    async def _list_files_in_folder(
        self,
        client: httpx.AsyncClient,
        folder_id: str,
    ) -> list[dict[str, Any]]:
        """List all files in a folder (non-recursive) with pagination."""
        files: list[dict[str, Any]] = []
        page_token: str | None = None

        while True:
            params: dict[str, Any] = {
                "q": f"'{folder_id}' in parents and trashed = false",
                "fields": "nextPageToken,files(id,name,mimeType,size,modifiedTime,webViewLink)",
                "pageSize": 100,
            }
            if page_token:
                params["pageToken"] = page_token

            resp = await client.get(f"{_DRIVE_API}/files", params=params)
            resp.raise_for_status()
            data = resp.json()
            files.extend(data.get("files", []))

            page_token = data.get("nextPageToken")
            if not page_token:
                break

        return files

    async def _list_all_files(
        self, client: httpx.AsyncClient,
    ) -> list[dict[str, Any]]:
        """List all accessible files (no folder filter) with pagination."""
        files: list[dict[str, Any]] = []
        page_token: str | None = None

        while True:
            params: dict[str, Any] = {
                "q": "trashed = false",
                "fields": "nextPageToken,files(id,name,mimeType,size,modifiedTime,webViewLink)",
                "pageSize": 100,
            }
            if page_token:
                params["pageToken"] = page_token

            resp = await client.get(f"{_DRIVE_API}/files", params=params)
            resp.raise_for_status()
            data = resp.json()
            files.extend(data.get("files", []))

            page_token = data.get("nextPageToken")
            if not page_token:
                break

        return files

    def _is_supported(self, mime_type: str) -> bool:
        """Check if the file MIME type is supported for sync."""
        return mime_type in _GOOGLE_EXPORT_MIMES or mime_type in _SUPPORTED_MIMES

    def _get_content_type(self, mime_type: str) -> str:
        """Map MIME type to connector content_type."""
        if mime_type in _GOOGLE_EXPORT_MIMES:
            return _GOOGLE_EXPORT_MIMES[mime_type][1]
        return _SUPPORTED_MIMES.get(mime_type, "kb_article")

    async def list_documents(
        self,
        connector: Any,
        cursor_context: dict[str, Any] | None = None,
    ) -> list[DocumentRef]:
        """List Google Drive files as documents.

        Returns all supported files. The sync engine handles
        reconciliation using modifiedTime.
        """
        cfg = self._extract_config(connector)
        sa_json: str = cfg["service_account_json"]
        folder_ids: list[str] = cfg["folder_ids"]

        access_token = await get_google_access_token(sa_json, scopes=_DRIVE_SCOPES)

        async with httpx.AsyncClient(
            timeout=30.0,
            headers={"Authorization": f"Bearer {access_token}"},
        ) as client:
            all_files: list[dict[str, Any]] = []

            if folder_ids:
                for fid in folder_ids:
                    files = await self._list_files_in_folder(client, fid)
                    all_files.extend(files)
            else:
                all_files = await self._list_all_files(client)

        refs: list[DocumentRef] = []
        for f in all_files:
            mime_type = f.get("mimeType", "")
            if not self._is_supported(mime_type):
                continue

            file_id = f.get("id", "")
            refs.append(
                DocumentRef(
                    path=f.get("name", file_id),
                    ref=file_id,
                    size=int(f.get("size", 0)),
                    content_type=self._get_content_type(mime_type),
                    source_ref=file_id,
                    source_url=f.get("webViewLink", ""),
                    last_edited=f.get("modifiedTime", ""),
                )
            )

        logger.info("Listed Google Drive files", count=len(refs))
        return refs

    async def fetch_document(self, ref: DocumentRef, connector: Any) -> bytes:
        """Fetch file content from Google Drive.

        For Google Workspace formats (Docs, Sheets, Slides), exports
        to plain text or CSV. For regular files, downloads the content.
        """
        cfg = self._extract_config(connector)
        sa_json: str = cfg["service_account_json"]
        access_token = await get_google_access_token(sa_json, scopes=_DRIVE_SCOPES)

        async with httpx.AsyncClient(
            timeout=30.0,
            headers={"Authorization": f"Bearer {access_token}"},
        ) as client:
            # First, get the file metadata to determine MIME type.
            meta_resp = await client.get(
                f"{_DRIVE_API}/files/{ref.ref}",
                params={"fields": "mimeType"},
            )
            meta_resp.raise_for_status()
            mime_type = meta_resp.json().get("mimeType", "")

            if mime_type in _GOOGLE_EXPORT_MIMES:
                export_mime = _GOOGLE_EXPORT_MIMES[mime_type][0]
                resp = await client.get(
                    f"{_DRIVE_API}/files/{ref.ref}/export",
                    params={"mimeType": export_mime},
                )
            else:
                resp = await client.get(
                    f"{_DRIVE_API}/files/{ref.ref}",
                    params={"alt": "media"},
                )

            resp.raise_for_status()
            return resp.content

    async def get_cursor_state(self, connector: Any) -> dict[str, Any]:
        """Return cursor with current time for incremental sync."""
        from datetime import UTC, datetime

        return {"last_synced_at": datetime.now(UTC).isoformat()}
