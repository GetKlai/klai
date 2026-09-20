"""Tests for the URL source extractor (SPEC-KB-SOURCES-001 Module 2).

URL extraction runs on the authenticated knowledge-ingest preview boundary
(``knowledge_ingest_client.preview_crawl``) — never on a direct crawl4ai call,
which portal-api is not allowed to make. Covers the happy path (preview
fit_markdown → title + content + canonical source_ref), the exact tenant
identity handed to that boundary, portal-side SSRF guarding ahead of the
outbound call, failure modes (empty / unknown preview) and title derivation.
"""

from __future__ import annotations

from collections.abc import Iterable
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.source_extractors.exceptions import (
    SourceFetchError,
    SSRFBlockedError,
)


def _fake_resolver(ips: Iterable[str]) -> object:
    resolved = list(ips)

    async def _resolve(_host: str, _timeout: float = 2.0) -> list[str]:
        return resolved

    return _resolve


def _preview_response(fit_markdown: str, *, classification: str = "success") -> dict[str, object]:
    """Build a knowledge-ingest preview-shaped response."""
    return {
        "fit_markdown": fit_markdown,
        "word_count": len(fit_markdown.split()),
        "url": "https://example.com/page",
        "classification": classification,
        "classification_reason": "",
    }


def _preview_failure() -> dict[str, object]:
    """The exact shape ``preview_crawl`` returns when the upstream failed —
    it swallows the error and reports an unknown, empty preview."""
    return {
        "fit_markdown": "",
        "word_count": 0,
        "url": "https://example.com/page",
        "classification": "unknown",
        "classification_reason": "Preview service did not respond. Try again.",
    }


@pytest.mark.parametrize(
    ("mode", "expected_markdown", "expected_classification"),
    [
        (None, "", "unknown"),
        ("connector_preview", "", "unknown"),
        ("single_page_source", "# Correct flow", "success"),
    ],
)
async def test_single_page_client_requires_correct_mode_marker(
    monkeypatch: pytest.MonkeyPatch,
    mode: str | None,
    expected_markdown: str,
    expected_classification: str,
) -> None:
    from app.services import knowledge_ingest_client

    data = {"fit_markdown": "# Correct flow", "classification": "success"}
    if mode is not None:
        data["mode"] = mode

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return data

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, *_args: object, **_kwargs: object) -> _Response:
            return _Response()

    monkeypatch.setattr(knowledge_ingest_client.httpx, "AsyncClient", lambda **_kwargs: _Client())

    result = await knowledge_ingest_client.preview_crawl(
        "https://example.com/page",
        org_id="zitadel-org-123",
        single_page_source=True,
    )

    assert result["classification"] == expected_classification
    assert result["fit_markdown"] == expected_markdown


@pytest.fixture
def mock_preview(monkeypatch: pytest.MonkeyPatch):
    """Patch the authenticated knowledge-ingest preview boundary."""

    def _install(response: dict[str, object]) -> dict[str, object]:
        calls: dict[str, object] = {}

        async def _preview_crawl(
            url: str,
            content_selector: str | None = None,
            org_id: str = "",
            **kwargs: object,
        ) -> dict[str, object]:
            calls["url"] = url
            calls["org_id"] = org_id
            calls.update(kwargs)
            return response

        monkeypatch.setattr(
            "app.services.knowledge_ingest_client.preview_crawl",
            _preview_crawl,
        )
        monkeypatch.setattr(
            "app.services.source_extractors._url_validator._resolve_host",
            _fake_resolver(["93.184.216.34"]),
        )
        return calls

    return _install


class TestTenantIdentity:
    async def test_url_extraction_is_scoped_to_the_requesting_tenant(self, mock_preview) -> None:
        """Extraction is a tenant-scoped call on the knowledge-ingest boundary.

        Production symptom (HTTP 401): portal-api POSTed straight to crawl4ai,
        which it is not allowed to speak to; knowledge-ingest owns the crawler
        and the internal-service auth. The portal must hand that service the
        canonical URL and the *exact* tenant identity it was given — a local
        numeric org id is not the ingest identity.
        """
        from app.services.source_extractors.url import extract_url

        calls = mock_preview(_preview_response("# My Page\n\nBody text here."))
        await extract_url("https://Example.com:443/page#frag", "org_zitadel_123")

        assert calls["url"] == "https://example.com/page"
        assert calls["org_id"] == "org_zitadel_123"
        assert calls["single_page_source"] is True

    async def test_blocked_url_never_reaches_the_preview_service(
        self, mock_preview, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Portal SSRF guarding stays in front of the outbound service call."""
        from app.services.source_extractors.url import extract_url

        calls = mock_preview(_preview_response("# My Page"))
        # Installed after the fixture: the fixture installs its own public-IP
        # resolver, and this host must resolve to loopback instead.
        monkeypatch.setattr(
            "app.services.source_extractors._url_validator._resolve_host",
            _fake_resolver(["127.0.0.1"]),
        )

        with pytest.raises(SSRFBlockedError):
            await extract_url("http://private.internal/page", "org_zitadel_123")

        assert calls == {}

    async def test_add_url_source_passes_the_zitadel_org_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.api.app_knowledge_sources import UrlSourceRequest, add_url_source

        kb = SimpleNamespace(slug="kb", name="KB", owner_type="org")
        extract = AsyncMock(return_value=("Title", "content", "https://example.com/page"))
        monkeypatch.setattr(
            "app.api.app_knowledge_sources._get_writable_kb_or_raise",
            AsyncMock(return_value=kb),
        )
        monkeypatch.setattr(
            "app.api.app_knowledge_sources._load_org_or_500",
            AsyncMock(return_value=SimpleNamespace(zitadel_org_id="zitadel-org-123")),
        )
        monkeypatch.setattr("app.api.app_knowledge_sources.extract_url", extract)
        monkeypatch.setattr(
            "app.api.app_knowledge_sources._forward_ingest",
            AsyncMock(return_value="artifact-1"),
        )

        await add_url_source(
            "kb",
            UrlSourceRequest(url="https://example.com/page"),
            perms=SimpleNamespace(org_id=17, user_id="user-1"),
            db=AsyncMock(),
        )

        extract.assert_awaited_once_with("https://example.com/page", "zitadel-org-123")


class TestHappyPath:
    async def test_returns_title_and_markdown(self, mock_preview) -> None:
        from app.services.source_extractors.url import extract_url

        mock_preview(_preview_response("# My Page\n\nBody text here."))
        title, content, source_ref = await extract_url("https://example.com/page", "org_1")
        assert title == "My Page"
        assert "Body text" in content
        assert source_ref == "https://example.com/page"

    async def test_template_residue_is_stripped_from_preview_content(self, mock_preview) -> None:
        from app.services.source_extractors.url import extract_url

        mock_preview(_preview_response("# Welcome\n\nReal prose stays here.\n* {{item.Name}}\n"))
        _, content, _ = await extract_url("https://example.com/page", "org_1")
        assert "{{item.Name}}" not in content
        assert "Real prose stays here." in content


class TestTitleDerivation:
    async def test_h1_wins(self, mock_preview) -> None:
        from app.services.source_extractors.url import extract_url

        mock_preview(_preview_response("Intro line\n\n# Real Title\n\nBody"))
        title, _, _ = await extract_url("https://example.com/page", "org_1")
        assert title == "Real Title"

    async def test_first_nonempty_line_when_no_h1(self, mock_preview) -> None:
        from app.services.source_extractors.url import extract_url

        mock_preview(_preview_response("\n\nFirst paragraph text here.\n\nMore text."))
        title, _, _ = await extract_url("https://example.com/page", "org_1")
        assert title == "First paragraph text here."

    async def test_hostname_fallback_when_no_text(self) -> None:
        """This should never happen in practice — empty content is rejected.

        But IF it happened, we'd fall back to hostname. We simulate it by
        handing the helper markdown with no usable text.
        """
        from app.services.source_extractors.url import _derive_title

        result = _derive_title("", hostname="example.com")
        assert result == "example.com"

    async def test_h1_with_leading_whitespace(self, mock_preview) -> None:
        from app.services.source_extractors.url import extract_url

        mock_preview(_preview_response("   # My Page   \n\nBody"))
        title, _, _ = await extract_url("https://example.com/page", "org_1")
        assert title == "My Page"

    async def test_first_line_truncated_to_120(self, mock_preview) -> None:
        from app.services.source_extractors.url import extract_url

        long_line = "x" * 200
        mock_preview(_preview_response(f"{long_line}\n\nmore"))
        title, _, _ = await extract_url("https://example.com/page", "org_1")
        assert len(title) <= 120


class TestFailureModes:
    async def test_raises_on_preview_service_failure(self, mock_preview) -> None:
        """Upstream failure arrives as an empty/unknown preview → fail loudly.

        ``preview_crawl`` never raises: a 401/5xx/timeout from knowledge-ingest
        comes back as an empty preview classified ``unknown``. Ingesting that
        would store an empty source, so it must surface as SourceFetchError
        (the endpoint maps it to a 502 the user can retry).
        """
        from app.services.source_extractors.url import extract_url

        mock_preview(_preview_failure())
        with pytest.raises(SourceFetchError):
            await extract_url("https://example.com/page", "org_1")

    async def test_raises_on_unknown_classification_with_content(self, mock_preview) -> None:
        """An unclassified preview is not trusted content, even when non-empty."""
        from app.services.source_extractors.url import extract_url

        mock_preview(_preview_response("# Partial render", classification="unknown"))
        with pytest.raises(SourceFetchError):
            await extract_url("https://example.com/page", "org_1")

    async def test_raises_on_empty_markdown(self, mock_preview) -> None:
        from app.services.source_extractors.url import extract_url

        mock_preview(_preview_response(""))
        with pytest.raises(SourceFetchError):
            await extract_url("https://example.com/page", "org_1")

    async def test_raises_on_whitespace_only_markdown(self, mock_preview) -> None:
        from app.services.source_extractors.url import extract_url

        mock_preview(_preview_response("   \n\n\t\n  "))
        with pytest.raises(SourceFetchError):
            await extract_url("https://example.com/page", "org_1")

    async def test_raises_when_only_template_residue_remains(self, mock_preview) -> None:
        """An unrendered AngularJS shell is not content either."""
        from app.services.source_extractors.url import extract_url

        mock_preview(_preview_response("{{item.Name}}\n{{item.Value}}"))
        with pytest.raises(SourceFetchError):
            await extract_url("https://example.com/page", "org_1")


class TestSourceRef:
    async def test_source_ref_is_canonical_url(self, mock_preview) -> None:
        from app.services.source_extractors.url import extract_url

        mock_preview(_preview_response("# Page"))
        _, _, source_ref = await extract_url("https://Example.com:443/path#frag", "org_1")
        assert source_ref == "https://example.com/path"

    async def test_query_string_preserved_in_source_ref(self, mock_preview) -> None:
        from app.services.source_extractors.url import extract_url

        mock_preview(_preview_response("# Page"))
        _, _, source_ref = await extract_url("https://example.com/archive?page=2", "org_1")
        assert source_ref == "https://example.com/archive?page=2"


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
