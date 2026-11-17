"""Helpers for interpreting knowledge-ingest crawl sync status payloads."""

from __future__ import annotations

from typing import Any

from app.core.enums import SyncStatus

COMPLETED_REMOTE_CRAWL_STATUS = "completed"
FAILED_REMOTE_CRAWL_STATUSES = frozenset({"failed", "failed_partial"})
CONTINUES_NEXT_NIGHT_STOPS = frozenset({"page_limit", "attempt_limit", "site_rate_limit"})
TERMINAL_REMOTE_CRAWL_STATUSES = FAILED_REMOTE_CRAWL_STATUSES | {COMPLETED_REMOTE_CRAWL_STATUS}


def is_terminal_remote_crawl_status(status: str) -> bool:
    return status in TERMINAL_REMOTE_CRAWL_STATUSES


def remote_crawl_coverage(live: dict[str, Any]) -> dict[str, Any] | None:
    """The coverage object of a finished crawl, None for jobs from before it existed."""
    coverage = live.get("coverage")
    return coverage if isinstance(coverage, dict) else None


def remote_crawl_succeeded(live: dict[str, Any]) -> bool:
    """Whether a finished crawl is recorded as a succeeded sync.

    A crawl that read pages but could not reach every address stops at a limit and
    continues the next night, so it is normal operation and not a failure. Nothing
    read, a block, a cancel and a login that is gone stay failures. Without a
    coverage object (a job from before it existed) only ``completed`` succeeds.
    """
    if str(live.get("status")) == COMPLETED_REMOTE_CRAWL_STATUS:
        return True
    coverage = remote_crawl_coverage(live)
    if coverage is None or str(live.get("error") or "").startswith("login_required:"):
        return False
    return (
        str(live.get("status")) in FAILED_REMOTE_CRAWL_STATUSES
        and int(coverage.get("pages_read") or 0) > 0
        and coverage.get("stopped_by") in CONTINUES_NEXT_NIGHT_STOPS
    )


def is_failed_remote_crawl_status(status: str) -> bool:
    return status in FAILED_REMOTE_CRAWL_STATUSES


def remote_crawl_documents_changed(live: dict[str, Any]) -> int | None:
    """Pages whose knowledge changed during the crawl, from the status payload.

    Not ``pages_done``: that also counts pages knowledge-ingest skipped as
    unchanged. None for a job created before knowledge-ingest counted changes,
    which the portal treats as "spend nothing on support reanalysis".
    """
    return live.get("pages_changed")


def remote_crawl_failure_status(error: str) -> SyncStatus:
    """The run status for a failed remote crawl.

    ``login_required:`` means knowledge-ingest found the site's saved
    session gone before the crawl. Only the owner can fix that by logging in again,
    which is what AUTH_ERROR tells the portal. Both the resolver and the
    reaper finalise runs, and a nightly run is usually finalised by the reaper.
    """
    return SyncStatus.AUTH_ERROR if error.startswith("login_required:") else SyncStatus.FAILED


def remote_crawl_failure_error(live: dict[str, Any]) -> str:
    error = live.get("error")
    if error:
        return str(error)
    status = str(live.get("status") or "")
    if status in FAILED_REMOTE_CRAWL_STATUSES:
        return status
    return "unknown"
