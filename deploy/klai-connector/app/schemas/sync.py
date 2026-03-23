"""Pydantic v2 schemas for sync run operations."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class SyncRunResponse(BaseModel):
    """Schema for sync run API responses."""

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

    model_config = ConfigDict(from_attributes=True)
