"""Tests for image URL validation / dedup utilities (SPEC-KB-IMAGE-002)."""

from __future__ import annotations

from klai_image_storage import (
    dedupe_image_urls,
    extract_markdown_image_urls,
    is_valid_image_src,
    resolve_relative_url,
)


class TestExtractMarkdownImageUrls:
    def test_extracts_simple_image(self) -> None:
        md = "Some text ![diagram](images/arch.png) more text"
        assert extract_markdown_image_urls(md) == [("diagram", "images/arch.png")]

    def test_extracts_multiple_images(self) -> None:
        md = "![a](one.png) text ![b](two.jpg)"
        assert extract_markdown_image_urls(md) == [("a", "one.png"), ("b", "two.jpg")]

    def test_extracts_image_with_empty_alt(self) -> None:
        assert extract_markdown_image_urls("![](photo.webp)") == [("", "photo.webp")]

    def test_extracts_absolute_url(self) -> None:
        md = "![logo](https://example.com/logo.png)"
        assert extract_markdown_image_urls(md) == [("logo", "https://example.com/logo.png")]

    def test_skips_data_uris(self) -> None:
        md = "![inline](data:image/png;base64,iVBOR...)"
        assert extract_markdown_image_urls(md) == []

    def test_ignores_regular_links(self) -> None:
        assert extract_markdown_image_urls("[click](page.html)") == []

    def test_empty_string(self) -> None:
        assert extract_markdown_image_urls("") == []

    def test_extracts_image_with_spaces_in_alt(self) -> None:
        md = "![my cool diagram](path/to/img.png)"
        assert extract_markdown_image_urls(md) == [("my cool diagram", "path/to/img.png")]


class TestResolveRelativeUrl:
    def test_absolute_url_unchanged(self) -> None:
        assert (
            resolve_relative_url("https://example.com/img.png", "https://site.com/page")
            == "https://example.com/img.png"
        )

    def test_relative_path_resolved(self) -> None:
        assert (
            resolve_relative_url("images/photo.png", "https://site.com/docs/page.html")
            == "https://site.com/docs/images/photo.png"
        )

    def test_root_relative_path(self) -> None:
        assert (
            resolve_relative_url("/assets/logo.png", "https://site.com/docs/page.html")
            == "https://site.com/assets/logo.png"
        )

    def test_parent_traversal(self) -> None:
        assert (
            resolve_relative_url("../img.png", "https://site.com/docs/sub/page.html") == "https://site.com/docs/img.png"
        )

    def test_empty_base_returns_original(self) -> None:
        assert resolve_relative_url("img.png", "") == "img.png"

    def test_http_url_unchanged(self) -> None:
        assert resolve_relative_url("http://example.com/img.png", "") == "http://example.com/img.png"


class TestIsValidImageSrc:
    """Cloudflare srcset debris guard (SPEC-CRAWLER-004 REQ-02.1)."""

    def test_accepts_absolute_http(self) -> None:
        assert is_valid_image_src("https://example.com/img.png")

    def test_accepts_absolute_https(self) -> None:
        assert is_valid_image_src("https://cdn.example.com/a/b.jpg")

    def test_accepts_protocol_relative(self) -> None:
        assert is_valid_image_src("//cdn.example.com/img.png")

    def test_accepts_root_relative(self) -> None:
        assert is_valid_image_src("/assets/img.png")

    def test_accepts_relative_path(self) -> None:
        assert is_valid_image_src("images/x.png")

    def test_accepts_dot_relative(self) -> None:
        assert is_valid_image_src("./img.png")

    def test_rejects_empty(self) -> None:
        assert not is_valid_image_src("")

    def test_rejects_whitespace(self) -> None:
        assert not is_valid_image_src("   ")

    def test_rejects_data_uri(self) -> None:
        assert not is_valid_image_src("data:image/png;base64,iVBOR")

    def test_rejects_srcset_quality_fragment(self) -> None:
        # Real-world Cloudflare comma-split debris.
        assert not is_valid_image_src("quality=90")

    def test_rejects_srcset_fit_fragment(self) -> None:
        assert not is_valid_image_src("fit=scale-down")

    def test_rejects_srcset_width_fragment(self) -> None:
        assert not is_valid_image_src("w=1920")

    def test_accepts_cloudflare_full_url(self) -> None:
        # The full Cloudflare URL must still pass — it has / and the =s are inside a path.
        url = "https://images.spr.so/cdn-cgi/imagedelivery/abc/def/w=1920,quality=90,fit=scale-down"
        assert is_valid_image_src(url)


class TestDedupeImageUrls:
    def test_preserves_first_seen_order(self) -> None:
        assert dedupe_image_urls(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]

    def test_empty_input(self) -> None:
        assert dedupe_image_urls([]) == []

    def test_all_unique_unchanged(self) -> None:
        assert dedupe_image_urls(["a", "b", "c"]) == ["a", "b", "c"]
