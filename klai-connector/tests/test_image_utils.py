"""Tests for image extraction utilities and data models."""

from app.adapters.base import DocumentRef, ImageRef
from app.services.image_utils import (
    dedupe_image_urls,
    extract_markdown_image_urls,
    is_valid_image_src,
    resolve_relative_url,
)


class TestExtractMarkdownImageUrls:
    """Tests for markdown image URL extraction."""

    def test_extracts_simple_image(self):
        md = "Some text ![diagram](images/arch.png) more text"
        result = extract_markdown_image_urls(md)
        assert len(result) == 1
        assert result[0] == ("diagram", "images/arch.png")

    def test_extracts_multiple_images(self):
        md = "![a](one.png) text ![b](two.jpg)"
        result = extract_markdown_image_urls(md)
        assert len(result) == 2
        assert result[0] == ("a", "one.png")
        assert result[1] == ("b", "two.jpg")

    def test_extracts_image_with_empty_alt(self):
        md = "![](photo.webp)"
        result = extract_markdown_image_urls(md)
        assert len(result) == 1
        assert result[0] == ("", "photo.webp")

    def test_extracts_absolute_url(self):
        md = "![logo](https://example.com/logo.png)"
        result = extract_markdown_image_urls(md)
        assert result[0] == ("logo", "https://example.com/logo.png")

    def test_ignores_non_image_links(self):
        md = "[click here](page.html)"
        result = extract_markdown_image_urls(md)
        assert len(result) == 0

    def test_empty_string(self):
        assert extract_markdown_image_urls("") == []

    def test_skips_data_uris(self):
        md = "![inline](data:image/png;base64,iVBOR...)"
        result = extract_markdown_image_urls(md)
        assert len(result) == 0

    def test_extracts_image_with_spaces_in_alt(self):
        md = "![my cool diagram](path/to/img.png)"
        result = extract_markdown_image_urls(md)
        assert result[0] == ("my cool diagram", "path/to/img.png")


class TestResolveRelativeUrl:
    """Tests for URL resolution."""

    def test_absolute_url_unchanged(self):
        assert resolve_relative_url("https://example.com/img.png", "https://site.com/page") == "https://example.com/img.png"

    def test_relative_path_resolved(self):
        result = resolve_relative_url("images/photo.png", "https://site.com/docs/page.html")
        assert result == "https://site.com/docs/images/photo.png"

    def test_root_relative_path(self):
        result = resolve_relative_url("/assets/logo.png", "https://site.com/docs/page.html")
        assert result == "https://site.com/assets/logo.png"

    def test_parent_traversal(self):
        result = resolve_relative_url("../img.png", "https://site.com/docs/sub/page.html")
        assert result == "https://site.com/docs/img.png"

    def test_empty_base_returns_relative(self):
        assert resolve_relative_url("img.png", "") == "img.png"


class TestIsValidImageSrc:
    """Guard against srcset debris (Cloudflare image-resize comma-split)."""

    def test_accepts_absolute_http(self):
        assert is_valid_image_src("https://example.com/img.png")

    def test_accepts_absolute_https(self):
        assert is_valid_image_src("https://cdn.example.com/a/b.jpg")

    def test_accepts_protocol_relative(self):
        assert is_valid_image_src("//cdn.example.com/img.png")

    def test_accepts_root_relative(self):
        assert is_valid_image_src("/assets/img.png")

    def test_accepts_relative_path(self):
        assert is_valid_image_src("images/x.png")

    def test_accepts_dot_relative(self):
        assert is_valid_image_src("./img.png")

    def test_rejects_empty(self):
        assert not is_valid_image_src("")

    def test_rejects_whitespace(self):
        assert not is_valid_image_src("   ")

    def test_rejects_data_uri(self):
        assert not is_valid_image_src("data:image/png;base64,iVBOR")

    def test_rejects_srcset_quality_fragment(self):
        # Real-world Cloudflare comma-split debris.
        assert not is_valid_image_src("quality=90")

    def test_rejects_srcset_fit_fragment(self):
        assert not is_valid_image_src("fit=scale-down")

    def test_rejects_srcset_width_fragment(self):
        assert not is_valid_image_src("w=1920")

    def test_accepts_cloudflare_resize_url(self):
        # The *full* Cloudflare URL must still pass — it has / and the =s are inside a path.
        url = "https://images.spr.so/cdn-cgi/imagedelivery/abc/def/w=1920,quality=90,fit=scale-down"
        assert is_valid_image_src(url)


class TestDedupeImageUrls:
    def test_preserves_first_seen_order(self):
        assert dedupe_image_urls(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]

    def test_empty_input(self):
        assert dedupe_image_urls([]) == []

    def test_all_unique_unchanged(self):
        assert dedupe_image_urls(["a", "b", "c"]) == ["a", "b", "c"]


class TestImageRef:
    """Tests for ImageRef dataclass."""

    def test_create_image_ref(self):
        ref = ImageRef(url="https://example.com/img.png", alt="diagram", source_path="docs/img.png")
        assert ref.url == "https://example.com/img.png"
        assert ref.alt == "diagram"
        assert ref.source_path == "docs/img.png"

    def test_alt_defaults_to_empty(self):
        ref = ImageRef(url="https://example.com/img.png", source_path="img.png")
        assert ref.alt == ""


class TestDocumentRefWithImages:
    """Tests for DocumentRef with optional images field."""

    def test_document_ref_images_default_none(self):
        ref = DocumentRef(path="doc.md", ref="abc", size=100, content_type="text/markdown")
        assert ref.images is None

    def test_document_ref_with_images(self):
        images = [ImageRef(url="https://s3/img.png", source_path="img.png")]
        ref = DocumentRef(path="doc.md", ref="abc", size=100, content_type="text/markdown", images=images)
        assert len(ref.images) == 1
        assert ref.images[0].url == "https://s3/img.png"
