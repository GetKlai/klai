"""Tests for the URL source extractor (SPEC-KB-SOURCES-001 Module 2).

Validates the happy path (crawl4ai → markdown + title), failure modes
(non-200, empty content), title derivation (h1 > first-line > hostname),
and source_ref canonicalisation.
"""

from __future__ import annotations

from collections.abc import Iterable

import httpx
import pytest

from app.services.source_extractors.exceptions import (
    SourceFetchError,
)


def _fake_resolver(ips: Iterable[str]) -> object:
    resolved = list(ips)

    async def _resolve(_host: str, _timeout: float = 2.0) -> list[str]:
        return resolved

    return _resolve


def _crawl_response(
    markdown: str,
    success: bool = True,
    status_code: int = 200,
) -> httpx.Response:
    """Build a crawl4ai-shaped JSON response."""
    return httpx.Response(
        status_code=status_code,
        json={
            "results": [
                {
                    "url": "https://example.com/page",
                    "success": success,
                    "markdown": {
                        "fit_markdown": markdown,
                        "raw_markdown": markdown,
                    },
                }
            ]
        },
    )


@pytest.fixture
def mock_httpx_factory(monkeypatch: pytest.MonkeyPatch):
    """Patch httpx.AsyncClient inside url extractor to use MockTransport."""

    def _install(response: httpx.Response) -> dict[str, object]:
        sent: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            sent["url"] = str(request.url)
            sent["json"] = request.read().decode("utf-8")
            sent["headers"] = dict(request.headers)
            return response

        transport = httpx.MockTransport(handler)

        class _Client(httpx.AsyncClient):
            def __init__(self, *args: object, **kwargs: object) -> None:  # type: ignore[no-untyped-def]
                kwargs["transport"] = transport
                super().__init__(*args, **kwargs)

        monkeypatch.setattr("app.services.source_extractors.url.httpx.AsyncClient", _Client)
        monkeypatch.setattr(
            "app.services.source_extractors._url_validator._resolve_host",
            _fake_resolver(["93.184.216.34"]),
        )
        return sent

    return _install


class TestHappyPath:
    async def test_returns_title_and_markdown(self, mock_httpx_factory) -> None:
        from app.services.source_extractors.url import extract_url

        mock_httpx_factory(_crawl_response("# My Page\n\nBody text here."))
        title, content, source_ref = await extract_url("https://example.com/page")
        assert title == "My Page"
        assert "Body text" in content
        assert source_ref == "https://example.com/page"

    async def test_calls_crawl4ai_endpoint(self, mock_httpx_factory) -> None:
        from app.services.source_extractors.url import extract_url

        sent = mock_httpx_factory(_crawl_response("# Hello"))
        await extract_url("https://example.com/page")
        assert "crawl" in str(sent["url"])

    async def test_sends_url_in_body(self, mock_httpx_factory) -> None:
        from app.services.source_extractors.url import extract_url

        sent = mock_httpx_factory(_crawl_response("# Hello"))
        await extract_url("https://example.com/page")
        import json as _json

        body = _json.loads(str(sent["json"]))
        assert body["urls"] == ["https://example.com/page"]


class TestTitleDerivation:
    async def test_h1_wins(self, mock_httpx_factory) -> None:
        from app.services.source_extractors.url import extract_url

        mock_httpx_factory(_crawl_response("Intro line\n\n# Real Title\n\nBody"))
        title, _, _ = await extract_url("https://example.com/page")
        assert title == "Real Title"

    async def test_first_nonempty_line_when_no_h1(self, mock_httpx_factory) -> None:
        from app.services.source_extractors.url import extract_url

        mock_httpx_factory(_crawl_response("\n\nFirst paragraph text here.\n\nMore text."))
        title, _, _ = await extract_url("https://example.com/page")
        assert title == "First paragraph text here."

    async def test_hostname_fallback_when_no_text(self, mock_httpx_factory) -> None:
        """This should never happen in practice — empty content is rejected.

        But IF it happened, we'd fall back to hostname. We simulate it by
        returning content that's only whitespace after markdown parsing.
        """
        from app.services.source_extractors.url import _derive_title

        result = _derive_title("", hostname="example.com")
        assert result == "example.com"

    async def test_h1_with_leading_whitespace(self, mock_httpx_factory) -> None:
        from app.services.source_extractors.url import extract_url

        mock_httpx_factory(_crawl_response("   # My Page   \n\nBody"))
        title, _, _ = await extract_url("https://example.com/page")
        assert title == "My Page"

    async def test_first_line_truncated_to_120(self, mock_httpx_factory) -> None:
        from app.services.source_extractors.url import extract_url

        long_line = "x" * 200
        mock_httpx_factory(_crawl_response(f"{long_line}\n\nmore"))
        title, _, _ = await extract_url("https://example.com/page")
        assert len(title) <= 120


class TestFailureModes:
    async def test_raises_on_non_200(self, mock_httpx_factory) -> None:
        from app.services.source_extractors.url import extract_url

        mock_httpx_factory(_crawl_response("", status_code=503))
        with pytest.raises(SourceFetchError):
            await extract_url("https://example.com/page")

    async def test_raises_on_empty_markdown(self, mock_httpx_factory) -> None:
        from app.services.source_extractors.url import extract_url

        mock_httpx_factory(_crawl_response(""))
        with pytest.raises(SourceFetchError):
            await extract_url("https://example.com/page")

    async def test_raises_on_whitespace_only_markdown(self, mock_httpx_factory) -> None:
        from app.services.source_extractors.url import extract_url

        mock_httpx_factory(_crawl_response("   \n\n\t\n  "))
        with pytest.raises(SourceFetchError):
            await extract_url("https://example.com/page")

    async def test_raises_on_crawl4ai_success_false(self, mock_httpx_factory) -> None:
        from app.services.source_extractors.url import extract_url

        mock_httpx_factory(_crawl_response("some content", success=False))
        with pytest.raises(SourceFetchError):
            await extract_url("https://example.com/page")

    async def test_raises_on_no_results(self, mock_httpx_factory, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.services.source_extractors.url import extract_url

        mock_httpx_factory(
            httpx.Response(status_code=200, json={"results": []}),
        )
        with pytest.raises(SourceFetchError):
            await extract_url("https://example.com/page")


class TestSourceRef:
    async def test_source_ref_is_canonical_url(self, mock_httpx_factory) -> None:
        from app.services.source_extractors.url import extract_url

        mock_httpx_factory(_crawl_response("# Page"))
        _, _, source_ref = await extract_url("https://Example.com:443/path#frag")
        assert source_ref == "https://example.com/path"

    async def test_query_string_preserved_in_source_ref(self, mock_httpx_factory) -> None:
        from app.services.source_extractors.url import extract_url

        mock_httpx_factory(_crawl_response("# Page"))
        _, _, source_ref = await extract_url("https://example.com/archive?page=2")
        assert source_ref == "https://example.com/archive?page=2"
