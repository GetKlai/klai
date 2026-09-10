"""The crawl4ai 400 diagnosis must survive into log + CrawlResult.

Production incident (measured 2026-09-10 on crawl4ai 0.9.2): POST /crawl
answered HTTP 400 with a body naming the rejected config field, e.g.
{"detail":"Rejected config: field 'js_code' is not permitted on
CrawlerRunConfig from an untrusted request"} — but ``str(httpx.
HTTPStatusError)`` drops that body, so ``crawl4ai_request_failed`` in
VictoriaLogs only ever showed "Client error '400 Bad Request'". The outage
read as "this page needs JavaScript" for weeks instead of a rejected field.

The body crosses a trust boundary. It is logged through the client's own
``_truncate_error_message``, the same helper every persisted error text
already goes through: auth/token query params are masked BEFORE truncation,
then the text is bounded at 300 chars.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from structlog.testing import capture_logs

from knowledge_ingest import crawl4ai_client

_REJECTED_CONFIG_BODY = {
    "detail": (
        "Rejected config: field 'js_code' is not permitted on "
        "CrawlerRunConfig from an untrusted request"
    )
}

_FORBIDDEN_TOKEN = "0f8e2a5b91c3d7e4f6a8b2c5"


def _status_error(status: int, body: dict[str, Any]) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://crawl4ai:11235/crawl")
    response = httpx.Response(status, json=body, request=request)
    return httpx.HTTPStatusError(
        f"Client error '{status} Bad Request' for url 'http://crawl4ai:11235/crawl'",
        request=request,
        response=response,
    )


def _stub_crawl_sync(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    async def _boom(_client: httpx.AsyncClient, _payload: dict[str, Any]) -> dict[str, Any]:
        raise error

    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", _boom)


async def _crawl_page(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> tuple[dict[str, Any], crawl4ai_client.CrawlResult]:
    _stub_crawl_sync(monkeypatch, error)
    with capture_logs() as logs:
        result = await crawl4ai_client._crawl_page_with_config(
            "https://example.com/", {}, cookies=None, selector=None
        )
    events = [e for e in logs if e["event"] == "crawl4ai_request_failed"]
    assert len(events) == 1
    return events[0], result


@pytest.mark.asyncio
async def test_rejected_config_body_reaches_log_and_error_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-1: the server's rejected-field diagnosis is neither swallowed nor raw."""
    event, result = await _crawl_page(monkeypatch, _status_error(400, _REJECTED_CONFIG_BODY))

    assert "field 'js_code' is not permitted" in event["response_body"]
    # error_message stays untouched on purpose: fetch-outcome classification
    # reads it, and body markers there would reclassify a transport 5xx.
    # The body already reaches CrawlResult.raw_error_text via _raw_error_text.
    assert "field 'js_code' is not permitted" in result.raw_error_text
    assert result.error_message == str(_status_error(400, _REJECTED_CONFIG_BODY))
    # Existing keys keep their old shape (brief: only an extra key may be added).
    assert event["error"] == (
        "Client error '400 Bad Request' for url 'http://crawl4ai:11235/crawl'"
    )
    assert result.success is False


@pytest.mark.asyncio
async def test_logged_body_masks_auth_tokens_echoed_from_the_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-2: crawl4ai echoes the failing URL, which may carry a session token.

    Measured on crawl4ai 0.9.2 (2026-09-10): the server does NOT echo
    ``hooks_config`` cookie values back in a 400 body, so the reachable leak
    is the URL. That is exactly what ``_mask_sensitive_query_params`` covers,
    and it runs before truncation so a token near the cut point cannot
    survive half-exposed.
    """
    body = {"detail": f"failed to fetch https://wiki.example.com/?token={_FORBIDDEN_TOKEN}"}

    event, _result = await _crawl_page(monkeypatch, _status_error(400, body))

    assert _FORBIDDEN_TOKEN not in event["response_body"]
    assert _FORBIDDEN_TOKEN not in str(event)
    assert "token=***" in event["response_body"]


@pytest.mark.asyncio
async def test_connect_error_keeps_exact_old_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-3: a non-HTTPStatusError must not gain a response_body key anywhere."""
    error = httpx.ConnectError("all connection attempts failed")

    event, result = await _crawl_page(monkeypatch, error)

    assert "response_body" not in event
    assert result.error_message == "all connection attempts failed"
    assert result.status_code is None
    assert result.error_type == "httpx.ConnectError"
    assert result.raw_error_text == "all connection attempts failed"


@pytest.mark.asyncio
async def test_seed_crawl_failure_also_carries_the_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The seed is the FIRST request of every web_crawler sync.

    A 400 there kills the whole sync, so it is the call site where losing the
    diagnosis costs the most. _crawl_page_with_config, _fetch_seed_page, its
    relaxed retry and the DOM-summary path all funnel through _crawl_sync and
    all used to drop the body; they now share _crawl4ai_error_body.
    """
    _stub_crawl_sync(monkeypatch, _status_error(400, _REJECTED_CONFIG_BODY))

    with capture_logs() as logs:
        result = await crawl4ai_client._fetch_seed_page(
            start_url="https://example.com/", crawler_config={}, cookies=None
        )

    events = [e for e in logs if e["event"] == "crawl_site_seed_request_failed"]
    assert len(events) == 1
    assert "field 'js_code' is not permitted" in events[0]["response_body"]
    assert "field 'js_code' is not permitted" in result.raw_error_text
    assert result.success is False


@pytest.mark.asyncio
async def test_seed_connect_error_keeps_exact_old_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same guarantee as the per-page path: no response_body key on transport errors."""
    _stub_crawl_sync(monkeypatch, httpx.ConnectError("all connection attempts failed"))

    with capture_logs() as logs:
        result = await crawl4ai_client._fetch_seed_page(
            start_url="https://example.com/", crawler_config={}, cookies=None
        )

    events = [e for e in logs if e["event"] == "crawl_site_seed_request_failed"]
    assert len(events) == 1
    assert "response_body" not in events[0]
    assert result.error_message == "all connection attempts failed"


@pytest.mark.asyncio
async def test_empty_response_body_adds_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 500 with no body must not log the httpx sentence a second time."""
    request = httpx.Request("POST", "http://crawl4ai:11235/crawl")
    response = httpx.Response(500, content=b"", request=request)
    error = httpx.HTTPStatusError("crawl4ai failed", request=request, response=response)

    event, _result = await _crawl_page(monkeypatch, error)

    assert "response_body" not in event
