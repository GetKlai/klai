"""Airtable connector adapter.

Syncs Airtable records as knowledge documents via the Airtable REST API.
Each record is flattened to key-value plain text. Supports optional table
filtering and pagination via the Airtable offset mechanism.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from app.adapters.base import BaseAdapter, DocumentRef
from app.core.config import Settings

logger = structlog.get_logger(__name__)

_AIRTABLE_API = "https://api.airtable.com/v0"


def _flatten_record(fields: dict[str, Any]) -> str:
    """Flatten Airtable record fields to a key: value text representation."""
    lines: list[str] = []
    for key, value in fields.items():
        if isinstance(value, list):
            value_str = ", ".join(str(v) for v in value)
        elif isinstance(value, dict):
            # Nested objects (e.g., collaborators, lookups).
            value_str = str(value)
        else:
            value_str = str(value)
        lines.append(f"{key}: {value_str}")
    return "\n".join(lines)


class AirtableAdapter(BaseAdapter):
    """Airtable connector adapter.

    Authenticates via a personal access token or API key against the
    Airtable REST API. Lists records from specified tables and converts
    each record to a plain-text document.

    Config fields (from connector.config):
        api_key (required): Airtable personal access token (pat...) or API key.
        base_id (required): Airtable base ID (app...).
        table_names (optional): List of table names or IDs to sync.
            Empty = must provide at least one table name.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._content_cache: dict[str, str] = {}

    async def aclose(self) -> None:
        """No persistent resources to close."""

    @staticmethod
    def _extract_config(connector: Any) -> dict[str, Any]:
        """Extract and validate Airtable config."""
        config: dict[str, Any] = connector.config
        api_key = config.get("api_key", "")
        base_id = config.get("base_id", "")
        if not api_key:
            raise ValueError("Airtable config missing 'api_key'")
        if not base_id:
            raise ValueError("Airtable config missing 'base_id'")
        table_names = config.get("table_names", [])
        if not table_names:
            raise ValueError("Airtable config missing 'table_names' (at least one required)")
        return {
            "api_key": api_key,
            "base_id": base_id,
            "table_names": table_names,
        }

    async def _list_records(
        self,
        client: httpx.AsyncClient,
        base_id: str,
        table_name: str,
    ) -> list[dict[str, Any]]:
        """Fetch all records from a table with offset-based pagination."""
        records: list[dict[str, Any]] = []
        offset: str | None = None

        while True:
            params: dict[str, Any] = {"pageSize": 100}
            if offset:
                params["offset"] = offset

            resp = await client.get(
                f"{_AIRTABLE_API}/{base_id}/{table_name}",
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
            records.extend(data.get("records", []))

            offset = data.get("offset")
            if not offset:
                break

        return records

    async def list_documents(
        self,
        connector: Any,
        cursor_context: dict[str, Any] | None = None,
    ) -> list[DocumentRef]:
        """List Airtable records as documents.

        Each record in each configured table becomes a document.
        """
        cfg = self._extract_config(connector)
        api_key: str = cfg["api_key"]
        base_id: str = cfg["base_id"]
        table_names: list[str] = cfg["table_names"]

        refs: list[DocumentRef] = []

        async with httpx.AsyncClient(
            timeout=30.0,
            headers={"Authorization": f"Bearer {api_key}"},
        ) as client:
            for table_name in table_names:
                records = await self._list_records(client, base_id, table_name)

                for record in records:
                    record_id = record.get("id", "")
                    fields = record.get("fields", {})
                    content = _flatten_record(fields)
                    doc_id = f"{table_name}:{record_id}"
                    self._content_cache[doc_id] = content

                    # Airtable doesn't expose per-record last_modified
                    # in the list endpoint. Use createdTime if available.
                    last_edited = record.get("createdTime", "")

                    refs.append(
                        DocumentRef(
                            path=f"{table_name}/{record_id}",
                            ref=doc_id,
                            size=len(content.encode("utf-8")),
                            content_type="structured_data",
                            source_ref=doc_id,
                            last_edited=last_edited,
                        )
                    )

                logger.info(
                    "Listed Airtable records",
                    table=table_name,
                    count=len(records),
                )

        logger.info("Listed Airtable documents total", count=len(refs))
        return refs

    async def fetch_document(self, ref: DocumentRef, connector: Any) -> bytes:
        """Return cached record content as UTF-8 bytes."""
        content = self._content_cache.get(ref.ref, "")
        if not content:
            logger.warning("Airtable record content not in cache", ref=ref.ref)
        return content.encode("utf-8")

    async def get_cursor_state(self, connector: Any) -> dict[str, Any]:
        """Return cursor with current time (Airtable has no global change feed)."""
        return {"last_synced_at": datetime.now(UTC).isoformat()}

    async def post_sync(self, connector: Any) -> None:
        """Clear cached record content after sync."""
        self._content_cache.clear()
