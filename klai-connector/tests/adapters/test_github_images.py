"""Tests for GitHubAdapter image extraction — adapter-owned URL resolution."""

from __future__ import annotations

from app.adapters.base import ImageRef
from app.adapters.github import GitHubAdapter


class TestExtractMarkdownImages:
    """GitHubAdapter resolves markdown image URLs to absolute raw URLs."""

    def test_relative_path_without_leading_dot(self):
        """``images/foo.png`` resolves against the repo raw root."""
        content = "# Guide\n\n![logo](images/logo.png)"
        images = GitHubAdapter._extract_markdown_images(content, "acme", "docs", "main")
        assert images == [
            ImageRef(
                url="https://raw.githubusercontent.com/acme/docs/main/images/logo.png",
                alt="logo",
                source_path="",
            ),
        ]

    def test_dot_slash_relative_path(self):
        """``./images/foo.png`` resolves identically to ``images/foo.png``."""
        content = "![banner](./assets/banner.jpg)"
        images = GitHubAdapter._extract_markdown_images(content, "acme", "docs", "main")
        assert len(images) == 1
        assert images[0].url == "https://raw.githubusercontent.com/acme/docs/main/assets/banner.jpg"

    def test_leading_slash_resolves_against_host_root(self):
        """A leading ``/`` anchors at the host root per urljoin semantics.

        Note: this matches the pre-refactor behaviour. GitHub markdown rarely
        uses leading-slash image paths; if we ever need to support them as
        repo-root-relative we would need a different resolver.
        """
        content = "![icon](/icons/star.svg)"
        images = GitHubAdapter._extract_markdown_images(content, "acme", "docs", "main")
        assert images[0].url == "https://raw.githubusercontent.com/icons/star.svg"

    def test_absolute_url_passes_through_unchanged(self):
        """URLs already absolute are not rewritten."""
        content = "![cdn](https://cdn.example.com/x.png)"
        images = GitHubAdapter._extract_markdown_images(content, "acme", "docs", "main")
        assert images[0].url == "https://cdn.example.com/x.png"

    def test_alt_text_is_preserved(self):
        """Alt text from markdown syntax flows into ImageRef.alt."""
        content = "![Architecture diagram](arch.png)"
        images = GitHubAdapter._extract_markdown_images(content, "acme", "docs", "main")
        assert images[0].alt == "Architecture diagram"

    def test_branch_is_honoured(self):
        """Non-main branches appear in the resolved raw URL."""
        content = "![x](x.png)"
        images = GitHubAdapter._extract_markdown_images(content, "acme", "docs", "release/v2")
        assert images[0].url == "https://raw.githubusercontent.com/acme/docs/release/v2/x.png"

    def test_multiple_images_in_one_document(self):
        """Every markdown image in the document is captured."""
        content = "![a](a.png)\nSome text\n![b](./b.png)\n![c](/c.png)"
        images = GitHubAdapter._extract_markdown_images(content, "acme", "docs", "main")
        assert len(images) == 3
        assert [img.alt for img in images] == ["a", "b", "c"]

    def test_no_images_returns_empty_list(self):
        """Content without markdown images produces an empty list (not None)."""
        content = "# Just text\n\nNo pictures here."
        images = GitHubAdapter._extract_markdown_images(content, "acme", "docs", "main")
        assert images == []

    def test_data_uri_images_are_skipped(self):
        """Inline ``data:`` URIs are not included (handled by extract_markdown_image_urls)."""
        content = "![logo](data:image/png;base64,iVBORw0KGgo=)"
        images = GitHubAdapter._extract_markdown_images(content, "acme", "docs", "main")
        assert images == []
