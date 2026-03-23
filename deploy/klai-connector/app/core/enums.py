"""Shared enumerations for klai-connector."""

import enum


class SyncStatus(str, enum.Enum):
    """Sync run and connector sync status values."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    AUTH_ERROR = "auth_error"
