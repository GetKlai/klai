"""Pydantic v2 schemas for sync run operations."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class SyncRunResponse(BaseModel):
    """Schema for sync run API responses.

    SPEC-CRAWLER-006 added ``pages_done`` / ``pages_total`` /
    ``live_resolution_failed`` for delegated web_crawler runs whose state
    is resolved at read time from knowledge-ingest. For non-crawler
    connectors and terminal crawler runs the live fields are ``None`` /
    ``False`` and the response shape is backward-compatible with
    pre-SPEC-006 portal callers.
    """

    id: uuid.UUID
    connector_id: uuid.UUID
    status: str
    started_at: datetime
    completed_at: datetime | None
    documents_total: int
    documents_ok: int
    documents_failed: int
    bytes_processed: int
    error_details: list[dict[str, Any]] | None
    # SPEC-CRAWLER-006 REQ-04 / REQ-08: live progress on delegated
    # crawler runs. Always None for terminal rows and non-crawler types.
    pages_done: int | None = None
    pages_total: int | None = None
    live_resolution_failed: bool = False

    model_config = ConfigDict(from_attributes=True)
