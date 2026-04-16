"""Google Sheets connector adapter.

Syncs Google Sheets spreadsheets as knowledge documents via the
Sheets API v4. Each sheet (tab) within a spreadsheet becomes a
separate document, formatted as CSV-like text. Uses service account
authentication via JWT.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from app.adapters._google_auth import get_google_access_token
from app.adapters.base import BaseAdapter, DocumentRef
from app.core.config import Settings

logger = structlog.get_logger(__name__)

_SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"

_SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def _rows_to_text(values: list[list[str]]) -> str:
    """Convert a 2D array of cell values to CSV-like text."""
    lines: list[str] = []
    for row in values:
        lines.append(",".join(str(cell) for cell in row))
    return "\n".join(lines)


class GoogleSheetsAdapter(BaseAdapter):
    """Google Sheets connector adapter.

    Authenticates via a service account JSON key. Lists sheets (tabs)
    from specified spreadsheets and fetches their contents as text.

    Config fields (from connector.config):
        service_account_json (required): JSON string of the service account key.
        spreadsheet_ids (required): List of spreadsheet IDs to sync.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def aclose(self) -> None:
        """No persistent resources to close."""

    @staticmethod
    def _extract_config(connector: Any) -> dict[str, Any]:
        """Extract and validate Google Sheets config."""
        config: dict[str, Any] = connector.config
        sa_json = config.get("service_account_json", "")
        spreadsheet_ids = config.get("spreadsheet_ids", [])
        if not sa_json:
            raise ValueError("Google Sheets config missing 'service_account_json'")
        if not spreadsheet_ids:
            raise ValueError("Google Sheets config missing 'spreadsheet_ids'")
        return {
            "service_account_json": sa_json,
            "spreadsheet_ids": spreadsheet_ids,
        }

    async def list_documents(
        self,
        connector: Any,
        cursor_context: dict[str, Any] | None = None,
    ) -> list[DocumentRef]:
        """List sheets as documents.

        Each sheet (tab) in each spreadsheet becomes a separate document.
        """
        cfg = self._extract_config(connector)
        sa_json: str = cfg["service_account_json"]
        spreadsheet_ids: list[str] = cfg["spreadsheet_ids"]

        access_token = await get_google_access_token(sa_json, scopes=_SHEETS_SCOPES)

        refs: list[DocumentRef] = []

        async with httpx.AsyncClient(
            timeout=30.0,
            headers={"Authorization": f"Bearer {access_token}"},
        ) as client:
            for spreadsheet_id in spreadsheet_ids:
                resp = await client.get(
                    f"{_SHEETS_API}/{spreadsheet_id}",
                    params={"fields": "spreadsheetId,properties.title,sheets.properties"},
                )
                resp.raise_for_status()
                data = resp.json()

                spreadsheet_title = data.get("properties", {}).get("title", spreadsheet_id)
                sheets = data.get("sheets", [])

                for sheet in sheets:
                    props = sheet.get("properties", {})
                    sheet_title = props.get("title", "Sheet")
                    sheet_id = props.get("sheetId", 0)

                    doc_id = f"{spreadsheet_id}:{sheet_title}"
                    refs.append(
                        DocumentRef(
                            path=f"{spreadsheet_title}/{sheet_title}",
                            ref=doc_id,
                            size=0,  # Unknown until fetched.
                            content_type="structured_data",
                            source_ref=doc_id,
                            source_url=(
                                f"https://docs.google.com/spreadsheets/d/"
                                f"{spreadsheet_id}/edit#gid={sheet_id}"
                            ),
                            last_edited="",
                        )
                    )

        logger.info("Listed Google Sheets documents", count=len(refs))
        return refs

    async def fetch_document(self, ref: DocumentRef, connector: Any) -> bytes:
        """Fetch sheet content as CSV-like text.

        Reads all cell values from the specified sheet and formats
        them as comma-separated rows.
        """
        cfg = self._extract_config(connector)
        sa_json: str = cfg["service_account_json"]

        # Parse doc_id: "spreadsheet_id:sheet_title"
        parts = ref.ref.split(":", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid Google Sheets ref: {ref.ref}")
        spreadsheet_id, sheet_title = parts

        access_token = await get_google_access_token(sa_json, scopes=_SHEETS_SCOPES)

        async with httpx.AsyncClient(
            timeout=30.0,
            headers={"Authorization": f"Bearer {access_token}"},
        ) as client:
            resp = await client.get(
                f"{_SHEETS_API}/{spreadsheet_id}/values/{sheet_title}",
                params={"valueRenderOption": "FORMATTED_VALUE"},
            )
            resp.raise_for_status()
            data = resp.json()

        values: list[list[str]] = data.get("values", [])
        content = _rows_to_text(values)
        return content.encode("utf-8")

    async def get_cursor_state(self, connector: Any) -> dict[str, Any]:
        """Return cursor with current time (Sheets API has no change feed)."""
        return {"last_synced_at": datetime.now(UTC).isoformat()}
