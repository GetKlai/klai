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

    async def test_single_page_uses_lean_crawl_config(
        self,
        mock_httpx_factory,
    ) -> None:
        """Single-page URL-add uses a LEAN config: domcontentloaded, no wait_for
        JS predicate, no js_code injection, no excluded_tags, no aggressive
        content filter. Each heavy connector-pipeline knob broke real sites
        (hang / 500 / over-prune) — see _crawl_config docstring (2026-05-22)."""
        from app.services.source_extractors.url import _CRAWL4AI_TIMEOUT, extract_url

        sent = mock_httpx_factory(_crawl_response("# Hello"))
        await extract_url("https://example.com/page")

        import json as _json

        body = _json.loads(str(sent["json"]))
        params = body["crawler_config"]["params"]

        assert params["wait_until"] == "domcontentloaded"
        assert "wait_for" not in params
        assert "js_code" not in params
        assert "js_code_before_wait" not in params
        assert "excluded_tags" not in params
        assert "content_filter" not in params["markdown_generator"]["params"]
        # page_timeout MUST stay below the httpx client timeout so crawl4ai
        # answers before portal-api gives up (else 502 "unreachable").
        assert params["page_timeout"] < _CRAWL4AI_TIMEOUT * 1000


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

    async def test_extracts_content_even_when_success_false(self, mock_httpx_factory) -> None:
        """success=False but markdown present → use it (no raise).

        crawl4ai reports success=False when a wait_for predicate or page_timeout
        elapses, yet it often still captured usable markdown from the last DOM
        state. Bailing on success=False threw that away and produced a spurious
        502 "Pagina onbereikbaar" for small/slow pages (jantinedoornbos.nl
        regression). The empty-content check below is the real gate.
        """
        from app.services.source_extractors.url import extract_url

        mock_httpx_factory(_crawl_response("# Heading\n\nsome content", success=False))
        _title, content, _ref = await extract_url("https://example.com/page")
        assert "some content" in content

    async def test_raises_on_success_false_and_empty_markdown(self, mock_httpx_factory) -> None:
        """success=False AND no markdown → genuine failure, still raises."""
        from app.services.source_extractors.url import extract_url

        mock_httpx_factory(_crawl_response("", success=False))
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


# ---------------------------------------------------------------------------
# Client timeout budget
# ---------------------------------------------------------------------------


def test_crawl4ai_timeout_covers_slow_javascript_sites() -> None:
    """The client ceiling must sit above real-world slow-render timings.

    Regression (2026-08-06, support.ascendcloud.com): the budget was 30s while
    crawl4ai logged [COMPLETE] ✓ at 34-35s on six consecutive attempts. The
    crawl succeeded every time; portal-api quit 4-6s early and the user got
    "Pagina onbereikbaar - probeer opnieuw" — a fetch-failure message for a
    fetch that worked.

    Pinned so a future "tighten the timeout" cleanup has to confront the
    measurement instead of the intuition that 30s is generous.
    """
    from app.services.source_extractors.url import _CRAWL4AI_TIMEOUT

    measured_worst_case_seconds = 35.0
    assert _CRAWL4AI_TIMEOUT > measured_worst_case_seconds, (
        f"_CRAWL4AI_TIMEOUT={_CRAWL4AI_TIMEOUT}s is at or below the measured "
        f"{measured_worst_case_seconds}s worst case; slow JS sites will 502 again"
    )


# ---------------------------------------------------------------------------
# Unrendered template residue (see also knowledge-ingest's twin helper)
# ---------------------------------------------------------------------------


def test_strip_unrendered_template_lines_drops_token_junk() -> None:
    from app.services.source_extractors.url import strip_unrendered_template_lines

    md = (
        "# Welcome\n"
        "Real prose stays here.\n"
        "* {{item.Name}}\n"
        "{{selectedCountryPhone.countryCode}} {{selectedCountryPhone.text}}\n"
        "Use {{name}} to insert the customer name into the template.\n"
    )
    cleaned = strip_unrendered_template_lines(md)
    assert "{{item.Name}}" not in cleaned
    assert "{{selectedCountryPhone" not in cleaned
    assert "Real prose stays here." in cleaned
    # Prose that merely mentions a token is kept unchanged.
    assert "Use {{name}} to insert the customer name into the template." in cleaned
