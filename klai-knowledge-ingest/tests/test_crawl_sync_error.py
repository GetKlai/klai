"""The stable error code a polling caller gets for a finished crawl job."""

from knowledge_ingest.routes.crawl_sync import _crawl_sync_error


def test_a_failed_job_reports_its_reason_not_a_bare_failed():
    """A crawl refused by the crawler ends ``failed`` with a reason (2026-10-28);
    the reason has to reach the connector and the portal, as it does for
    failed_partial."""
    row = {
        "status": "failed",
        "error": None,
        "error_summary": '{"reason": "crawler_config_rejected"}',
    }

    assert _crawl_sync_error(row) == "crawler_config_rejected"
    assert _crawl_sync_error({"status": "failed", "error": None, "error_summary": None}) == "failed"
    assert _crawl_sync_error({"status": "completed", "error": None}) is None
