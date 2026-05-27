"""Fail-closed default tests for the portal crawl-preview pass-through.

SPEC-CONNECTOR-INPUT-VALIDATION-001 follow-up — fail-OPEN regression guard.

Two failure modes are covered:

1. ``knowledge_ingest_client.preview_crawl`` receives a response dict that
   contains no ``classification`` key (upstream returned a degraded payload).
   The portal response builder must default to ``"unknown"``, not ``"success"``.

2. ``knowledge_ingest_client.preview_crawl`` catches an exception and returns
   its own degraded dict.  That dict must now carry ``classification="unknown"``
   explicitly so the portal response builder never needs to invent a default.

These tests work at the service-function and source-inspection level to avoid
spinning up a real database or auth middleware.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent.parent
_API_FILE = _REPO_ROOT / "klai-portal" / "backend" / "app" / "api" / "app_knowledge_bases.py"
_CLIENT_FILE = _REPO_ROOT / "klai-portal" / "backend" / "app" / "services" / "knowledge_ingest_client.py"


# ---------------------------------------------------------------------------
# Layer 2 — portal-api response builder default (source-level assertion)
# ---------------------------------------------------------------------------


def test_portal_response_builder_defaults_to_unknown() -> None:
    """The result.get("classification", ...) default in app_knowledge_bases.py
    must be "unknown", not "success".

    Source-level inspection catches the regression without needing a full
    app stack — the same approach used in test_knowledge_ingest_client_caller_header.py.
    """
    source = _API_FILE.read_text(encoding="utf-8")
    assert 'result.get("classification", "unknown")' in source, (
        'app_knowledge_bases.py must use result.get("classification", "unknown"). '
        'Using "success" as the default is fail-OPEN: an absent classification key '
        "silently enables Save in the wizard."
    )
    assert 'result.get("classification", "success")' not in source, (
        'Found result.get("classification", "success") in app_knowledge_bases.py. '
        "This is the fail-OPEN default that was replaced — restore the fail-closed fix."
    )


def test_portal_crawl_preview_response_model_defaults_to_unknown() -> None:
    """The CrawlPreviewResponse model in app_knowledge_bases.py must default
    classification to 'unknown', not 'success'."""
    source = _API_FILE.read_text(encoding="utf-8")
    # The model block is:
    #   classification: str = "unknown"
    # Ensure the fail-closed default is present and the old default is absent.
    assert 'classification: str = "unknown"' in source, (
        "CrawlPreviewResponse.classification default in app_knowledge_bases.py "
        "must be 'unknown'. A 'success' default is fail-OPEN."
    )


def test_portal_preview_and_auth_probe_can_use_saved_credentials() -> None:
    """Edit flows must be able to test encrypted saved cookies server-side.

    The browser receives only has_saved_credentials from the connector API, so
    preview/auth-probe need an explicit connector_id + use_saved_credentials
    contract instead of relying on plaintext cookies in the form.
    """
    source = _API_FILE.read_text(encoding="utf-8")
    assert "use_saved_credentials: bool = False" in source
    assert "connector_id: str | None = None" in source
    assert "_load_saved_web_crawler_cookies" in source
    assert "saved_credentials_conflict" in source
    assert "credential_store.decrypt_credentials" in source


# ---------------------------------------------------------------------------
# Layer 3 — knowledge_ingest_client.preview_crawl exception fallback
# ---------------------------------------------------------------------------


def test_crawl_preview_pass_through_defaults_classification_to_unknown() -> None:
    """When the upstream returns a dict without a 'classification' key
    (degraded response), preview_crawl passes the raw dict up.  The portal
    response builder then fills the 'unknown' default.

    This test validates the source-level guarantee that the fallback default
    is "unknown" at the portal layer.

    We also verify the client-level fallback dict includes the explicit key
    to provide belt-and-suspenders coverage.
    """
    source = _CLIENT_FILE.read_text(encoding="utf-8")
    assert '"classification": "unknown"' in source, (
        "knowledge_ingest_client.preview_crawl exception fallback must include "
        '"classification": "unknown" in the returned dict. '
        "Without it the portal response builder's default becomes the only safety net."
    )
    assert '"classification_reason"' in source, (
        "knowledge_ingest_client.preview_crawl exception fallback must include "
        '"classification_reason" so the UI can surface a user-readable message.'
    )


def test_crawl_preview_pass_through_propagates_explicit_classification() -> None:
    """When the upstream returns an explicit classification (e.g. 'selector_required'),
    the client must pass it through unchanged — no override, no re-default."""
    from app.services.knowledge_ingest_client import preview_crawl

    captured_payload: dict = {}

    async def _mock_send(self, request, *args, **kwargs):
        captured_payload["url"] = str(request.url)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(
            return_value={
                "fit_markdown": "# Docs\n\nReal content.",
                "word_count": 120,
                "url": "https://docs.example.com/page",
                "classification": "selector_required",
                "classification_reason": "Link density above threshold.",
                "warnings": [],
            }
        )
        return mock_resp

    with patch.object(httpx.AsyncClient, "send", _mock_send):
        result = asyncio.run(preview_crawl(url="https://docs.example.com/page", org_id="org-test"))

    assert result["classification"] == "selector_required", (
        f"preview_crawl must propagate explicit classification from upstream, got {result['classification']!r}"
    )
    assert result["classification_reason"] == "Link density above threshold."


def test_crawl_preview_client_exception_returns_unknown_classification() -> None:
    """When preview_crawl catches an Exception (timeout, connection error, etc.),
    the returned fallback dict must carry classification='unknown' — not an
    absent key that lets the portal response builder's default kick in."""
    from app.services.knowledge_ingest_client import preview_crawl

    async def _raise_timeout(self, request, *args, **kwargs):
        raise httpx.TimeoutException("timed out", request=request)

    with patch.object(httpx.AsyncClient, "send", _raise_timeout):
        result = asyncio.run(preview_crawl(url="https://example.com/page", org_id="org-test"))

    assert result.get("classification") == "unknown", (
        f"preview_crawl exception path must return classification='unknown', got {result.get('classification')!r}"
    )
    assert result.get("classification_reason"), (
        "preview_crawl exception path must return a non-empty classification_reason."
    )
    assert "Preview service did not respond" in result["classification_reason"], (
        f"classification_reason should mention 'Preview service did not respond', "
        f"got: {result['classification_reason']!r}"
    )
