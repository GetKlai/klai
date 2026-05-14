"""Helpers for interpreting knowledge-ingest crawl sync status payloads."""

from __future__ import annotations

from typing import Any

COMPLETED_REMOTE_CRAWL_STATUS = "completed"
FAILED_REMOTE_CRAWL_STATUSES = frozenset({"failed", "failed_partial"})
TERMINAL_REMOTE_CRAWL_STATUSES = FAILED_REMOTE_CRAWL_STATUSES | {COMPLETED_REMOTE_CRAWL_STATUS}


def is_terminal_remote_crawl_status(status: str) -> bool:
    return status in TERMINAL_REMOTE_CRAWL_STATUSES


def is_completed_remote_crawl_status(status: str) -> bool:
    return status == COMPLETED_REMOTE_CRAWL_STATUS


def is_failed_remote_crawl_status(status: str) -> bool:
    return status in FAILED_REMOTE_CRAWL_STATUSES


def remote_crawl_failure_error(live: dict[str, Any]) -> str:
    error = live.get("error")
    if error:
        return str(error)
    status = str(live.get("status") or "")
    if status in FAILED_REMOTE_CRAWL_STATUSES:
        return status
    return "unknown"
