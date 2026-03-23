"""Pydantic v2 schemas for connector CRUD operations."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class ConnectorCreate(BaseModel):
    """Schema for creating a new connector."""

    name: str
    connector_type: Literal["github"]  # MVP: only github
    config: dict[str, Any]
    # For GitHub: {"installation_id": int, "repo_owner": str, "repo_name": str,
    #              "branch": str, "path_filter": str | None, "kb_slug": str}
    schedule: str | None = None  # cron expression


class ConnectorUpdate(BaseModel):
    """Schema for updating an existing connector."""

    name: str | None = None
    config: dict[str, Any] | None = None
    schedule: str | None = None
    is_enabled: bool | None = None


class ConnectorResponse(BaseModel):
    """Schema for connector API responses.

    Note: ``credentials_enc`` is NEVER included in any response.
    """

    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    connector_type: str
    config: dict[str, Any]
    schedule: str | None
    is_enabled: bool
    last_sync_at: datetime | None
    last_sync_status: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
