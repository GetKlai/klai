"""Tests for the needs_reconfiguration predicate (REQ-7).

SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-7 — UI badge signal.

Per the implementation note in ``api/connectors.py::ConnectorOut``, this
flag is an approximation of the SPEC's full predicate. The full SPEC
predicate joins ``portal_connectors`` to ``connector.sync_runs.error_details``
which lives in klai-connector's separate database. The proxy used here:

    needs_reconfiguration = (
        connector_type == "web_crawler"
        AND last_sync_status == "failed"
    )

Covers AC-9: the existing Redcactus connector with last_sync_status='failed'
gets surfaced. False positives (a web_crawler that failed for an unrelated
reason) are acceptable: clicking "Investigate" will still drop the user
into the wizard, which now has the REQ-2 / REQ-3 gates to surface the real
problem.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.api.connectors import _compute_needs_reconfiguration


def _conn(connector_type: str, last_sync_status: str | None) -> SimpleNamespace:
    """Minimal duck-typed PortalConnector shim — the predicate only reads
    two attributes."""
    return SimpleNamespace(
        connector_type=connector_type,
        last_sync_status=last_sync_status,
    )


def test_web_crawler_with_failed_status_is_flagged() -> None:
    assert _compute_needs_reconfiguration(_conn("web_crawler", "failed")) is True


def test_web_crawler_with_success_status_is_not_flagged() -> None:
    assert _compute_needs_reconfiguration(_conn("web_crawler", "success")) is False


def test_web_crawler_with_completed_status_is_not_flagged() -> None:
    assert _compute_needs_reconfiguration(_conn("web_crawler", "completed")) is False


def test_web_crawler_with_running_status_is_not_flagged() -> None:
    assert _compute_needs_reconfiguration(_conn("web_crawler", "running")) is False


def test_web_crawler_with_null_status_is_not_flagged() -> None:
    """Brand-new connector that hasn't synced yet must not be flagged."""
    assert _compute_needs_reconfiguration(_conn("web_crawler", None)) is False


def test_notion_connector_with_failed_status_is_not_flagged() -> None:
    """The badge predicate intentionally narrows to web_crawler — that's
    the connector type this SPEC's failure mode applies to. Other
    connectors with failed syncs surface via existing UI paths."""
    assert _compute_needs_reconfiguration(_conn("notion", "failed")) is False


def test_github_connector_with_failed_status_is_not_flagged() -> None:
    assert _compute_needs_reconfiguration(_conn("github", "failed")) is False


def test_web_crawler_with_failed_partial_status_is_not_flagged() -> None:
    """REQ-4 writes 'failed_partial' on knowledge.crawl_jobs in the
    knowledge-ingest DB. Per REQ-4 spec the wrapping connector.sync_runs
    rolls up to 'failed' (not 'failed_partial') when the dirty-content
    guard trips, and that 'failed' is what bubbles back to portal_connectors
    via the existing sync-status callback. Portal therefore never observes
    'failed_partial' on last_sync_status — but encode the assumption here
    so a future contract change is loud."""
    assert _compute_needs_reconfiguration(_conn("web_crawler", "failed_partial")) is False
