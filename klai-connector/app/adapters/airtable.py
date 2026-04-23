"""Airtable connector adapter using the pyairtable SDK.

Syncs Airtable records as knowledge documents. Supports full-scan sync via
record iteration over configured tables. Each record is flattened to
key-value plain text for downstream ingestion.

SDK note: pyairtable is synchronous. All blocking calls are wrapped with
asyncio.to_thread() per the klai Python async pattern (lang/python.md).
"""

# @MX:ANCHOR: BaseAdapter implementation -- SPEC-KB-CONNECTORS-001 Phase 2
# @MX:SPEC: SPEC-KB-CONNECTORS-001

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, cast

from pyairtable import Api
from pyairtable.api.types import RecordDict

from app.adapters.base import BaseAdapter, DocumentRef
from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _is_collaborator(value: Any) -> bool:
    """Return True if *value* looks like an Airtable collaborator object.

    A collaborator object is a dict with a non-empty ``email`` string key.
    """
    if not isinstance(value, dict):
        return False
    as_dict: dict[str, Any] = cast(dict[str, Any], value)
    email: Any = as_dict.get("email")
    return isinstance(email, str) and email != ""


def _extract_collab_emails(fields: dict[str, Any]) -> list[str]:
    """Walk *fields* and collect email strings from collaborator-shaped values.

    Handles both single collaborator dicts and lists of collaborator dicts.
    """
    emails: list[str] = []
    for value in fields.values():
        if _is_collaborator(value):
            emails.append(cast(dict[str, Any], value)["email"])
        elif isinstance(value, list):
            for item in cast(list[Any], value):
                if _is_collaborator(item):
                    emails.append(cast(dict[str, Any], item)["email"])
    return emails


def _flatten_record(fields: dict[str, Any]) -> str:
    """Flatten *fields* to a plain-text ``key: value`` string.

    Keys are sorted alphabetically so that the flattened representation is
    deterministic and suitable for content-hash deduplication (SPEC R3.3).
    """
    lines: list[str] = []
    for key in sorted(fields.keys()):
        raw: Any = fields[key]
        if isinstance(raw, list):
            value_str = ", ".join(str(cast(object, elem)) for elem in cast(list[Any], raw))
        elif isinstance(raw, dict):
            value_str = str(cast(object, raw))
        else:
            value_str = str(raw)
        lines.append(f"{key}: {value_str}")
    return "\n".join(lines)


class AirtableAdapter(BaseAdapter):
    """Airtable connector adapter.

    Authenticates via a personal access token (PAT) stored in connector.config.
    Iterates all records in the configured tables and flattens each record to
    plain text for ingestion by the knowledge pipeline.

    Does NOT hold a persistent Api instance — the API key is per-tenant and the
    adapter instance is a singleton per process. Api is constructed inside each
    async operation from the connector config.
    """

    def __init__(self, settings: Settings) -> None:
        # Settings kept for interface compatibility; no global Airtable credentials.
        self._settings = settings

    async def aclose(self) -> None:
        """No persistent resources to close."""
        return None

    # -- Config helpers -------------------------------------------------------

    @staticmethod
    def _extract_config(connector: Any) -> dict[str, Any]:
        """Extract and validate Airtable config from connector.config.

        Required fields:
            api_key: Airtable personal access token (``pat...``) or legacy key.
            base_id: Airtable base ID (must start with ``app``).
            table_names: Non-empty list of table names (or IDs) to sync.

        Optional fields:
            view_name: View name to filter records (default: None = all records).

        Raises:
            ValueError: If any required field is missing or empty.
        """
        config: dict[str, Any] = connector.config

        api_key: str | None = config.get("api_key")
        if not api_key:
            raise ValueError(
                "Airtable connector config missing required field 'api_key'. "
                "Provide a personal access token in connector.config.api_key."
            )

        base_id: str | None = config.get("base_id")
        if not base_id:
            raise ValueError(
                "Airtable connector config missing required field 'base_id'. "
                "Provide the Airtable base ID (starts with 'app') in connector.config.base_id."
            )

        table_names: list[str] | None = config.get("table_names")
        if not table_names:
            raise ValueError(
                "Airtable connector config missing required field 'table_names'. "
                "Provide a non-empty list of table names in connector.config.table_names."
            )

        return {
            "api_key": api_key,
            "base_id": base_id,
            "table_names": table_names,
            "view_name": config.get("view_name"),
        }

    # -- BaseAdapter implementation -------------------------------------------

    async def list_documents(
        self,
        connector: Any,
        cursor_context: dict[str, Any] | None = None,
    ) -> list[DocumentRef]:
        """List all documents available for sync from Airtable.

        Iterates all records in each configured table using the pyairtable
        synchronous SDK wrapped via asyncio.to_thread(). Full-scan only —
        Airtable's native API does not provide an incremental cursor; the
        sync engine reconciles using last_edited on each DocumentRef.

        Args:
            connector: Connector model instance with Airtable config.
            cursor_context: Previous cursor state (not used for filtering;
                kept for interface compatibility).

        Returns:
            A list of DocumentRef instances, one per Airtable record.
        """
        cfg = self._extract_config(connector)
        api_key: str = cfg["api_key"]
        base_id: str = cfg["base_id"]
        table_names: list[str] = cfg["table_names"]
        view_name: str | None = cfg["view_name"]

        refs: list[DocumentRef] = []

        for table_name in table_names:
            table_records = await asyncio.to_thread(
                self._fetch_all_records,
                api_key=api_key,
                base_id=base_id,
                table_name=table_name,
                view_name=view_name,
            )

            for raw_record in table_records:
                record = cast(dict[str, Any], raw_record)
                record_id: str = record["id"]
                fields: dict[str, Any] = record.get("fields") or {}

                content = _flatten_record(fields)
                size = len(content.encode("utf-8"))

                last_edited: str = (
                    record.get("_modifiedTime")
                    or record.get("createdTime")
                    or ""
                )

                created_by: dict[str, Any] = record.get("createdBy") or {}
                sender_email: str = created_by.get("email") or ""

                # Collect mentioned emails from collaborator fields
                collab_emails = _extract_collab_emails(fields)
                # Include lastModifiedBy if present
                last_modified_by: dict[str, Any] = record.get("lastModifiedBy") or {}
                last_modifier_email: str = last_modified_by.get("email") or ""
                if last_modifier_email:
                    collab_emails.append(last_modifier_email)

                # Dedupe and exclude sender
                seen: set[str] = set()
                mentioned: list[str] = []
                for email in collab_emails:
                    if email and email not in seen and email != sender_email:
                        seen.add(email)
                        mentioned.append(email)

                refs.append(
                    DocumentRef(
                        path=f"{table_name}/{record_id}",
                        ref=record_id,
                        size=size,
                        content_type="text/plain",
                        source_ref=f"{base_id}/{table_name}/{record_id}",
                        source_url=f"https://airtable.com/{base_id}/{table_name}/{record_id}",
                        last_edited=last_edited,
                        sender_email=sender_email,
                        mentioned_emails=mentioned,
                    )
                )

        logger.info(
            "Airtable list_documents complete: base=%s tables=%s records=%d",
            base_id,
            table_names,
            len(refs),
        )
        return refs

    async def fetch_document(self, ref: DocumentRef, connector: Any) -> bytes:
        """Fetch a single Airtable record and return it as UTF-8 encoded bytes.

        The record fields are flattened to a ``key: value`` string with keys
        sorted alphabetically (SPEC R3.3) for deterministic content hashing.

        Args:
            ref: DocumentRef with ``ref`` set to the Airtable record ID and
                ``path`` set to ``{table_name}/{record_id}``.
            connector: Connector model instance with Airtable config.

        Returns:
            UTF-8 encoded flattened representation of the record fields.
        """
        cfg = self._extract_config(connector)
        api_key: str = cfg["api_key"]
        base_id: str = cfg["base_id"]

        # Extract table_name from path: "{table_name}/{record_id}"
        path_parts = ref.path.split("/", 1)
        table_name = path_parts[0]
        record_id = ref.ref

        raw_record = await asyncio.to_thread(
            self._fetch_single_record,
            api_key=api_key,
            base_id=base_id,
            table_name=table_name,
            record_id=record_id,
        )

        record = cast(dict[str, Any], raw_record)
        fields: dict[str, Any] = record.get("fields") or {}
        content = _flatten_record(fields)
        return content.encode("utf-8")

    async def get_cursor_state(self, connector: Any) -> dict[str, Any]:
        """Return the current cursor state for incremental sync.

        Airtable does not have a native incremental cursor. A full scan is
        performed each time; the sync engine reconciles using last_edited on
        each DocumentRef (knowledge.md "Incremental cursor reset" pattern).

        Returns:
            Dict with ``last_run_at`` ISO 8601 timestamp.
        """
        return {"last_run_at": datetime.now(UTC).isoformat()}

    # -- Synchronous helpers (run via asyncio.to_thread) ----------------------

    @staticmethod
    def _fetch_all_records(
        *,
        api_key: str,
        base_id: str,
        table_name: str,
        view_name: str | None,
    ) -> list[RecordDict]:
        """Fetch all records from a single Airtable table (synchronous).

        Wrapped via asyncio.to_thread() in async callers. Converts the
        lazy iterator to a list inside the thread to avoid generator leakage
        across thread boundaries.
        """
        api = Api(api_key)
        table = api.table(base_id, table_name)

        iterate_kwargs: dict[str, Any] = {"page_size": 100}
        if view_name:
            iterate_kwargs["view"] = view_name

        records: list[RecordDict] = []
        for page in table.iterate(**iterate_kwargs):
            records.extend(page)

        return records

    @staticmethod
    def _fetch_single_record(
        *,
        api_key: str,
        base_id: str,
        table_name: str,
        record_id: str,
    ) -> RecordDict:
        """Fetch a single Airtable record by ID (synchronous).

        Wrapped via asyncio.to_thread() in async callers.
        """
        api = Api(api_key)
        table = api.table(base_id, table_name)
        return table.get(record_id)
