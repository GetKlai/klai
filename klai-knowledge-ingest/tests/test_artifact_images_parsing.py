"""Unit tests for ``_parse_image_refs`` and orphan-key SQL semantics.

SPEC-CONNECTOR-DELETE-LIFECYCLE-001 REQ-06.2 + SPEC-KB-IMAGES-V2-001 REQ-1.

These tests now go through ``KbImage.from_path`` (the single source of
truth for kb-image URL parsing), so fixtures must use canonical-shape
URLs: a 64-char hex sha + a supported extension (jpg|png|gif|webp).
"""

from __future__ import annotations

from knowledge_ingest.routes.ingest import _parse_image_refs

# Realistic sha256s — content hashes of small fixture byte strings.
_SHA_A = "a" * 64
_SHA_B = "b" * 64
_SHA_VALID = "1c" * 32


class TestParseImageRefs:
    def test_handles_canonical_kb_image_url(self) -> None:
        urls = [
            f"/kb-images/100000000000000002/images/support/{_SHA_A}.png",
        ]
        refs = _parse_image_refs(urls)
        assert refs == [
            (
                f"100000000000000002/images/support/{_SHA_A}.png",
                _SHA_A,
            )
        ]

    def test_handles_multiple_urls(self) -> None:
        urls = [
            f"/kb-images/100000000000000001/images/kb1/{_SHA_A}.png",
            f"/kb-images/100000000000000001/images/kb1/{_SHA_B}.webp",
        ]
        refs = _parse_image_refs(urls)
        assert len(refs) == 2
        assert refs[0] == (f"100000000000000001/images/kb1/{_SHA_A}.png", _SHA_A)
        assert refs[1] == (f"100000000000000001/images/kb1/{_SHA_B}.webp", _SHA_B)

    def test_skips_non_kb_image_urls(self) -> None:
        """Manual uploads pointing at external CDNs are not tracked."""
        urls = [
            "https://cdn.example.com/foo.png",
            "/some-other-prefix/bar.jpg",
            f"/kb-images/100000000000000001/images/kb1/{_SHA_VALID}.png",
        ]
        refs = _parse_image_refs(urls)
        assert refs == [(f"100000000000000001/images/kb1/{_SHA_VALID}.png", _SHA_VALID)]

    def test_skips_non_string_entries(self) -> None:
        urls: list = [
            f"/kb-images/100000000000000001/images/kb1/{_SHA_A}.png",
            None,
            42,
            {"url": "x"},
        ]
        refs = _parse_image_refs(urls)
        assert refs == [(f"100000000000000001/images/kb1/{_SHA_A}.png", _SHA_A)]

    def test_skips_url_without_canonical_shape(self) -> None:
        """SPEC-KB-IMAGES-V2-001 REQ-1: only canonical shape (5 segments + 64-hex
        sha + supported ext) is accepted. Anything else is silently skipped —
        same fail-safe as before, stricter shape."""
        urls = [
            "/kb-images/100000000000000001/images/kb1/abc",  # no ext, short basename
            "/kb-images/100000000000000001/images/kb1/short.png",  # wrong sha length
            f"/kb-images/100000000000000001/images/kb1/{_SHA_A}.svg",  # disallowed ext
            "/kb-images/.png",  # missing org/kb/file segments
        ]
        refs = _parse_image_refs(urls)
        assert refs == []

    def test_empty_input_returns_empty_list(self) -> None:
        assert _parse_image_refs([]) == []
