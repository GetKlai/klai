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
- SPEC-KB-CONNECTORS-001 Phase 4: ``content_types`` filter restricts results
  to specific Google Workspace mime types.  Three user-facing aliases
  (``google_docs``, ``google_sheets``, ``google_slides``) are wired in
  ``main.py``; the adapter self-injects the matching preset when
  ``connector.connector_type`` is one of those aliases.

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
from app.adapters.oauth_base import ConnectorLike, OAuthAdapterBase, check_invalid_grant_and_raise
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
# owners and permissions are added for SPEC-KB-CONNECTORS-001 R5.5 (identifier capture).
_FILE_FIELDS = (
    "id, name, mimeType, modifiedTime, webViewLink, size, "
    "owners(emailAddress, displayName), permissions(role, emailAddress)"
)
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

# SPEC-KB-CONNECTORS-001 R5.1 — mapping from user-facing content_type label
# to Drive API mimeType string.  Used for the q= filter and validation.
_CONTENT_TYPE_TO_MIME: dict[str, str] = {
    "google_doc": "application/vnd.google-apps.document",
    "google_sheet": "application/vnd.google-apps.spreadsheet",
    "google_slides": "application/vnd.google-apps.presentation",
}

# Roles whose email addresses are included in DocumentRef.mentioned_emails.
# "reader" is excluded per SPEC-KB-CONNECTORS-001 R5.5.
_WRITER_ROLES = {"owner", "writer", "commenter"}


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
    def _content_types_for_connector_type(connector_type: str) -> list[str] | None:
        """Return the implicit content_types preset for alias connector types.

        Used by ``_extract_config`` to inject the correct mimeType filter when
        a ``google_docs`` / ``google_sheets`` / ``google_slides`` connector row
        has no explicit ``content_types`` in its config.  This keeps the sync
        engine untouched: the adapter is self-aware of alias semantics.

        Returns ``None`` for ``google_drive`` (base type) or any unrecognised
        connector_type — preserving existing all-types behaviour.
        """
        mapping: dict[str, list[str]] = {
            "google_docs": ["google_doc"],
            "google_sheets": ["google_sheet"],
            "google_slides": ["google_slides"],
        }
        return mapping.get(connector_type)

    @staticmethod
    def _extract_config(connector: Any) -> dict[str, Any]:
        """Normalise config dict with safe defaults.

        SPEC-KB-CONNECTORS-001 R5.1–R5.3:
        - ``content_types``: list of allowed content_type labels.  Missing or
          empty → ``None`` (all types, backward-compatible for existing
          ``google_drive`` connectors).
        - Invalid entries raise ``ValueError`` listing allowed values.
        - When ``content_types`` is absent AND ``connector.connector_type`` is
          a known alias (``google_docs`` / ``google_sheets`` / ``google_slides``),
          the matching preset is injected so the adapter applies the correct
          mimeType filter without requiring sync-engine changes.
        """
        config: dict[str, Any] = connector.config or {}
        connector_type: str = getattr(connector, "connector_type", "google_drive") or "google_drive"

        # Explicit user config wins over any alias preset.
        raw_content_types = config.get("content_types")
        if raw_content_types:
            # Validate each entry.
            allowed = set(_CONTENT_TYPE_TO_MIME.keys())
            invalid = [ct for ct in raw_content_types if ct not in allowed]
            if invalid:
                raise ValueError(
                    f"Invalid content_type(s): {invalid!r}. "
                    f"Allowed values: {sorted(allowed)!r}"
                )
            content_types: list[str] | None = list(raw_content_types)
        else:
            # No explicit config — inject alias preset if applicable.
            content_types = GoogleDriveAdapter._content_types_for_connector_type(connector_type)

        return {
            "folder_id": config.get("folder_id") or None,
            "max_files": int(config.get("max_files") or _DEFAULT_MAX_FILES),
            "content_types": content_types,
        }

    # -- OAuth refresh --------------------------------------------------------

    async def _refresh_oauth_token(
        self, connector: ConnectorLike, refresh_token: str,
    ) -> dict[str, Any]:
        """Exchange a refresh_token for a new access_token against Google.

        Args:
            connector: Connector model (used for connector_id in error messages).
            refresh_token: Long-lived refresh token from the encrypted config.

        Returns:
            Raw JSON dict from Google's token endpoint.

        Raises:
            OAuthReconnectRequiredError: Google returned ``invalid_grant``
                (user revoked consent, password change, or refresh_token
                expired from inactivity >6 months). The sync engine catches
                this and marks the connector as auth_error so the portal
                can surface a "Reconnect Google" affordance.
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
            # Shared helper translates 400 + error=invalid_grant to a typed
            # OAuthReconnectRequiredError; other 400s fall through to
            # raise_for_status (generic HTTPStatusError).
            check_invalid_grant_and_raise(
                response, provider="Google", connector_id=connector.id,
            )
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

        SPEC-KB-CONNECTORS-001 R5.1–R5.3: When ``cfg["content_types"]`` is
        set, only files whose ``mimeType`` matches the allowed set are
        returned.  For ``files.list``, the filter is embedded in the Drive
        API ``q`` parameter.  For ``changes.list``, filtering is applied
        client-side (the changes API does not support ``q`` filtering).

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
        content_types: list[str] | None = cfg.get("content_types")
        page_token = (cursor_context or {}).get("page_token") if cursor_context else None

        # Build the set of allowed mimeTypes (None = all types).
        allowed_mimes: set[str] | None = None
        if content_types:
            allowed_mimes = {_CONTENT_TYPE_TO_MIME[ct] for ct in content_types}

        if page_token:
            logger.info(
                "Listing Google Drive changes since cursor (connector=%s)",
                connector_id,
            )
            response = await self._list_changes(connector, page_token=page_token)
            raw_files = [c.get("file") for c in response.get("changes", []) if c.get("file")]
            # Client-side mimeType filter for incremental path (changes API has no q= support).
            if allowed_mimes is not None:
                raw_files = [f for f in raw_files if f and f.get("mimeType") in allowed_mimes]
            new_cursor = response.get("newStartPageToken")
            if new_cursor:
                self._latest_page_token[connector_id] = new_cursor
        else:
            logger.info(
                "Listing Google Drive files (first sync, connector=%s, folder=%s)",
                connector_id,
                cfg["folder_id"],
            )
            response = await self._list_files(
                connector,
                folder_id=cfg["folder_id"],
                allowed_mimes=allowed_mimes,
            )
            raw_files = list(response.get("files", []))
            # Client-side guard: the q= filter is applied in _list_files, but
            # when _list_files is mocked in tests it may return any files.
            # Applying the filter here keeps list_documents correct regardless.
            if allowed_mimes is not None:
                raw_files = [f for f in raw_files if f and f.get("mimeType") in allowed_mimes]

        refs: list[DocumentRef] = []
        for file in raw_files[:max_files]:
            if not file:
                continue
            mime = file.get("mimeType", "")
            size_str = file.get("size")
            try:
                size = int(size_str) if size_str else 0
            except (TypeError, ValueError):
                size = 0

            # SPEC-KB-CONNECTORS-001 R5.5 — identifier capture.
            owners: list[Any] = list(file.get("owners") or [])
            sender_email: str = str(owners[0].get("emailAddress") or "") if owners else ""

            permissions: list[Any] = list(file.get("permissions") or [])
            mentioned_seen: set[str] = set()
            mentioned_emails: list[str] = []
            for perm in permissions:
                if perm.get("role") not in _WRITER_ROLES:
                    continue  # skip readers per SPEC
                raw_email = perm.get("emailAddress") or ""
                email: str = str(raw_email)
                if not email or email == sender_email or email in mentioned_seen:
                    continue
                mentioned_seen.add(email)
                mentioned_emails.append(email)

            refs.append(
                DocumentRef(
                    path=file.get("name", "") or file.get("id", ""),
                    ref=file.get("id", ""),
                    size=size,
                    content_type=_content_type_for_mime(mime),
                    source_ref=file.get("id", ""),
                    source_url=file.get("webViewLink", ""),
                    last_edited=file.get("modifiedTime", ""),
                    sender_email=sender_email,
                    mentioned_emails=mentioned_emails,
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
        self,
        connector: Any,
        folder_id: str | None,
        allowed_mimes: set[str] | None = None,
    ) -> dict[str, Any]:
        """Call ``files.list`` with pagination rolled up into one response dict.

        Args:
            connector: Connector model (for token refresh).
            folder_id: Optional parent folder to restrict results.
            allowed_mimes: When set, a ``mimeType in (...)`` predicate is
                AND-ed into the Drive API ``q`` parameter (SPEC-KB-CONNECTORS-001
                R5.2).  ``None`` preserves existing all-types behaviour.
        """
        token = await self.ensure_token(connector)
        params: dict[str, Any] = {
            "pageSize": 100,
            "fields": _LIST_FIELDS,
            "spaces": "drive",
            "includeItemsFromAllDrives": "true",
            "supportsAllDrives": "true",
        }

        # Build the q= query string.
        base_q = f"'{folder_id}' in parents and trashed = false" if folder_id else "trashed = false"

        if allowed_mimes:
            mime_clauses = " or ".join(f"mimeType='{m}'" for m in sorted(allowed_mimes))
            params["q"] = f"{base_q} and ({mime_clauses})"
        else:
            params["q"] = base_q

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
