"""Shared enumerations for klai-connector."""

import enum


class SyncStatus(enum.StrEnum):
    """Sync run and connector sync status values.

    StrEnum (Python 3.11+) is the modern equivalent of ``(str, enum.Enum)`` —
    auto-resolves the value as a string, equal to the bare string, and
    JSON-serialises to the string form.
    """

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    AUTH_ERROR = "auth_error"
    PENDING = "pending"
