"""Fail-closed default tests for CrawlPreviewResponse.classification.

SPEC-CONNECTOR-INPUT-VALIDATION-001 follow-up — fail-OPEN regression guard.

The "unknown" default ensures that:

1. Omitting ``classification`` from ``CrawlPreviewResponse(...)`` yields
   ``"unknown"``, not ``"success"``.  This is the field-level gate.

2. When the upstream crawl service raises an exception (timeout / crash),
   the exception handler in ``preview_crawl`` returns ``"unknown"`` —
   never a concrete outcome like ``"requires_javascript"``.  That would be
   a mis-diagnosis of an upstream outage.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from knowledge_ingest.routes.crawl import CrawlPreviewResponse

# ---------------------------------------------------------------------------
# Layer 1 — model default
# ---------------------------------------------------------------------------


def test_preview_response_default_classification_is_unknown() -> None:
    """Instantiating CrawlPreviewResponse without classification must yield
    'unknown', not 'success'.

    Regression: before this fix the default was "success", which meant that
    any serialisation gap (missing key, partial upstream dict) silently told
    the wizard the content was ready to save.
    """
    resp = CrawlPreviewResponse(url="https://example.com", fit_markdown="", word_count=0)
    assert resp.classification == "unknown", (
        f"CrawlPreviewResponse.classification default must be 'unknown', "
        f"got {resp.classification!r}. "
        "A 'success' default is fail-OPEN: the wizard would enable Save "
        "on a non-classified preview."
    )


# ---------------------------------------------------------------------------
# Layer 1 — exception handler in preview_crawl endpoint
# ---------------------------------------------------------------------------


def test_preview_crawl_exception_path_returns_unknown(client: TestClient) -> None:
    """When crawl_page raises any exception, the endpoint must return
    classification='unknown' with a user-readable reason.

    Before the fix, the exception handler returned
    classification='requires_javascript', which is a misdiagnosis: an upstream
    crash looks nothing like a JS-rendered SPA.  The user would be told to
    wait for JS rendering when the real problem was a service outage.
    """
    with patch(
        "knowledge_ingest.routes.crawl.crawl_page",
        new=AsyncMock(side_effect=Exception("upstream broken")),
    ):
        resp = client.post(
            "/ingest/v1/crawl/preview",
            json={"url": "https://example.com/page"},
        )

    assert resp.status_code == 200, f"Expected 200 from degraded path, got {resp.status_code}"
    body = resp.json()

    assert body["classification"] == "unknown", (
        f"Exception path must return classification='unknown', "
        f"got {body['classification']!r}. "
        "Returning a concrete outcome on error is a misdiagnosis."
    )
    assert body.get("classification_reason"), (
        "classification_reason must be set on the exception path "
        "so the UI can show a user-readable message."
    )
    assert "Could not reach" in body["classification_reason"], (
        "classification_reason should mention 'Could not reach', "
        f"got: {body['classification_reason']!r}"
    )
