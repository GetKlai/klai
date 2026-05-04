"""Unit tests for ``_parse_image_refs`` and orphan-key SQL semantics.

SPEC-CONNECTOR-DELETE-LIFECYCLE-001 REQ-06.2.

The integration tests for the orphan-keys SQL live alongside other
real-postgres tests in tests/integration/. These unit tests cover only
the URL-parsing helper which is pure-python and can be exhaustively
verified without a database.
"""

from __future__ import annotations

from knowledge_ingest.routes.ingest import _parse_image_refs


class TestParseImageRefs:
    def test_handles_canonical_kb_image_url(self) -> None:
        urls = [
            "/kb-images/368884765035593759/images/support/abc123def.png",
        ]
        refs = _parse_image_refs(urls)
        assert refs == [
            (
                "368884765035593759/images/support/abc123def.png",
                "abc123def",
            )
        ]

    def test_handles_multiple_urls(self) -> None:
        urls = [
            "/kb-images/o1/images/kb1/aaa111.png",
            "/kb-images/o1/images/kb1/bbb222.webp",
        ]
        refs = _parse_image_refs(urls)
        assert len(refs) == 2
        assert refs[0] == ("o1/images/kb1/aaa111.png", "aaa111")
        assert refs[1] == ("o1/images/kb1/bbb222.webp", "bbb222")

    def test_skips_non_kb_image_urls(self) -> None:
        """Manual uploads pointing at external CDNs are not tracked."""
        urls = [
            "https://cdn.example.com/foo.png",
            "/some-other-prefix/bar.jpg",
            "/kb-images/o1/images/kb1/valid.png",
        ]
        refs = _parse_image_refs(urls)
        assert refs == [("o1/images/kb1/valid.png", "valid")]

    def test_skips_non_string_entries(self) -> None:
        urls: list = ["/kb-images/o1/images/kb1/abc.png", None, 42, {"url": "x"}]
        refs = _parse_image_refs(urls)
        assert refs == [("o1/images/kb1/abc.png", "abc")]

    def test_skips_url_with_no_extension(self) -> None:
        """Defensive: malformed URLs without an extension still produce a
        usable content_hash but we accept it (the basename is the hash).
        """
        urls = ["/kb-images/o1/images/kb1/abc"]
        refs = _parse_image_refs(urls)
        # No "." in basename -> rsplit returns the whole string -> hash=basename
        assert refs == [("o1/images/kb1/abc", "abc")]

    def test_empty_input_returns_empty_list(self) -> None:
        assert _parse_image_refs([]) == []

    def test_skips_url_when_basename_collapses_to_empty(self) -> None:
        urls = ["/kb-images/.png"]
        refs = _parse_image_refs(urls)
        assert refs == []
