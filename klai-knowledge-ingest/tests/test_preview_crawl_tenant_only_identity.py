"""SPEC-CONNECTOR-INPUT-VALIDATION-001 hotfix — pin tenant-only identity
on the preview/crawl routes.

Both ``preview_crawl`` and ``crawl_url`` are service-to-service pass-throughs
(called by portal-api with X-Internal-Secret + X-Caller-Service: portal-api).
There is no end-user in the request context, so they MUST use the
``assert_caller_identity_tenant_only`` flavour.

Same regression-test pattern as ``test_ingest_endpoints_identity_assertion.py``
(introduced after PR #448 fixed the same TypeError pattern in purge routes).

The test uses a strict-signature mock so any future revert to the user-bound
``assert_caller_identity`` (which requires ``claimed_user_id``) fails fast
with TypeError instead of bleeding into a 500 in production.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


def _strict_tenant_only_identity_signature():
    """Mock that ONLY accepts the tenant-only signature.

    If the route ever reverts to the user-bound ``assert_caller_identity``
    (which would also pass ``claimed_user_id``), this raises TypeError and
    the test fails — exactly the bug we're guarding against.
    """

    async def _ok(request, *, claimed_org_id):
        return claimed_org_id

    return AsyncMock(side_effect=_ok)


def test_preview_crawl_uses_tenant_only_identity(client: TestClient) -> None:
    """REGRESSION: preview_crawl MUST use assert_caller_identity_tenant_only.

    The OLD wizard sent body.org_id="" (empty) so the broken
    ``assert_caller_identity`` call never fired. The NEW wizard always sends
    a non-empty org_id, so the bug surfaced as a 500 in production
    (TypeError: missing 1 required positional argument 'claimed_user_id').
    """
    strict_mock = _strict_tenant_only_identity_signature()

    with (
        patch(
            "knowledge_ingest.routes.crawl.assert_caller_identity_tenant_only",
            new=strict_mock,
        ),
        patch(
            "knowledge_ingest.routes.crawl.crawl_page",
            new=AsyncMock(
                return_value=type(
                    "R",
                    (),
                    {
                        "url": "https://example.com",
                        "fit_markdown": "Real article content. " * 60,
                        "raw_markdown": "Real article content. " * 60,
                        "html": "<html></html>",
                        "word_count": 600,
                        "success": True,
                        "metadata": {"status_code": 200},
                        "response_headers": {},
                        "links": {},
                        "media": {},
                        "error_message": None,
                    },
                )(),
            ),
        ),
    ):
        resp = client.post(
            "/ingest/v1/crawl/preview",
            json={"url": "https://example.com", "org_id": "368884765035593759"},
        )

    assert resp.status_code == 200, resp.text
    assert strict_mock.await_count == 1, "tenant-only identity assert MUST run"
    # Pin the call shape — no claimed_user_id positional or kwarg.
    call_kwargs = strict_mock.await_args.kwargs
    assert call_kwargs == {"claimed_org_id": "368884765035593759"}


def test_crawl_url_uses_tenant_only_identity(client: TestClient) -> None:
    """REGRESSION: same fix applied to /ingest/v1/crawl (crawl_url)."""
    strict_mock = _strict_tenant_only_identity_signature()

    with (
        patch(
            "knowledge_ingest.routes.crawl.assert_caller_identity_tenant_only",
            new=strict_mock,
        ),
        # Stub everything downstream so the route returns 200 cleanly.
        patch("knowledge_ingest.routes.crawl.validate_url", new=AsyncMock(return_value=None)),
        patch(
            "knowledge_ingest.routes.crawl.get_domain_selector",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "knowledge_ingest.routes.crawl.crawl_page",
            new=AsyncMock(
                return_value=type(
                    "R",
                    (),
                    {
                        "url": "https://example.com",
                        "fit_markdown": "x" * 100,
                        "raw_markdown": "x" * 100,
                        "html": "<html></html>",
                        "word_count": 100,
                        "success": True,
                        "metadata": {},
                        "response_headers": {},
                        "links": {},
                        "media": {},
                        "error_message": None,
                    },
                )(),
            ),
        ),
        patch(
            "knowledge_ingest.routes.crawl.pg_store.get_crawled_page_stored",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "knowledge_ingest.routes.crawl.pg_store.upsert_crawled_page",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "knowledge_ingest.routes.crawl.ingest_document",
            new=AsyncMock(return_value={"chunks": 0}),
        ),
    ):
        resp = client.post(
            "/ingest/v1/crawl",
            json={
                "org_id": "368884765035593759",
                "kb_slug": "test-kb",
                "url": "https://example.com",
            },
        )

    assert resp.status_code == 200, resp.text
    assert strict_mock.await_count == 1
    call_kwargs = strict_mock.await_args.kwargs
    assert call_kwargs == {"claimed_org_id": "368884765035593759"}


def test_preview_crawl_skips_identity_when_org_id_empty(client: TestClient) -> None:
    """When org_id is empty (anonymous preview), identity assert MUST NOT
    run — that's the legitimate fallthrough for the older anonymous flow."""
    spy = _strict_tenant_only_identity_signature()

    with (
        patch(
            "knowledge_ingest.routes.crawl.assert_caller_identity_tenant_only",
            new=spy,
        ),
        patch(
            "knowledge_ingest.routes.crawl.crawl_page",
            new=AsyncMock(
                return_value=type(
                    "R",
                    (),
                    {
                        "url": "https://example.com",
                        "fit_markdown": "x" * 100,
                        "raw_markdown": "x" * 100,
                        "html": "<html></html>",
                        "word_count": 100,
                        "success": True,
                        "metadata": {},
                        "response_headers": {},
                        "links": {},
                        "media": {},
                        "error_message": None,
                    },
                )(),
            ),
        ),
    ):
        resp = client.post(
            "/ingest/v1/crawl/preview",
            json={"url": "https://example.com"},  # no org_id
        )

    assert resp.status_code == 200
    assert spy.await_count == 0, "identity assert must NOT run for anonymous preview"
