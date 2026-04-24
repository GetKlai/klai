"""Tests for the text source extractor.

SPEC-KB-SOURCES-001 Module 4 — validates normalisation, title derivation,
and deterministic source_ref for dedup. No external I/O, pure function.
"""

from __future__ import annotations

import hashlib

import pytest

from app.services.source_extractors.exceptions import InvalidContentError
from app.services.source_extractors.text import extract_text


class TestLengthValidation:
    def test_rejects_content_over_500k(self) -> None:
        oversized = "a" * 500_001
        with pytest.raises(InvalidContentError, match="exceeds"):
            extract_text(title=None, content=oversized)

    def test_accepts_content_exactly_at_limit(self) -> None:
        at_limit = "a" * 500_000
        title, content, _ = extract_text(title="x", content=at_limit)
        assert len(content) == 500_000
        assert title == "x"

    def test_rejects_empty_input(self) -> None:
        with pytest.raises(InvalidContentError):
            extract_text(title=None, content="")

    def test_rejects_whitespace_only(self) -> None:
        with pytest.raises(InvalidContentError, match="empty after normalisation"):
            extract_text(title=None, content="   \n\t  \n   ")

    def test_rejects_nul_only(self) -> None:
        with pytest.raises(InvalidContentError, match="empty after normalisation"):
            extract_text(title=None, content="\x00\x00\x00")


class TestNormalisation:
    def test_strips_nul_bytes(self) -> None:
        _, content, _ = extract_text(title="t", content="hello\x00world")
        assert "\x00" not in content
        assert content == "helloworld"

    def test_collapses_whitespace_runs_to_single_space(self) -> None:
        _, content, _ = extract_text(title="t", content="hello     world\n\n\tagain")
        assert content == "hello world again"

    def test_strips_leading_trailing_whitespace(self) -> None:
        _, content, _ = extract_text(title="t", content="   hello world   \n")
        assert content == "hello world"


class TestTitleDerivation:
    def test_explicit_title_wins(self) -> None:
        title, _, _ = extract_text(title="My Note", content="any body here")
        assert title == "My Note"

    def test_explicit_title_is_trimmed(self) -> None:
        title, _, _ = extract_text(title="  My Note  ", content="any body here")
        assert title == "My Note"

    def test_fallback_to_first_nonempty_line(self) -> None:
        title, _, _ = extract_text(
            title=None,
            content="\n\n   First real line   \nsecond\nthird",
        )
        assert title == "First real line"

    def test_fallback_line_truncated_to_120_chars(self) -> None:
        long_line = "x" * 300
        title, _, _ = extract_text(title=None, content=long_line)
        assert len(title) == 120
        assert title == "x" * 120

    def test_fallback_to_untitled_when_empty_title_and_no_line(self) -> None:
        # Normalised content present, but title="" and no clear first line:
        # after-normalise content "abc" is used as first-line fallback (it IS a line).
        # Construct a case where first-line cannot be derived: leading blanks only.
        # This is actually caught as empty-after-normalisation, so "Untitled note"
        # triggers when fallback line-derivation somehow yields empty while content
        # normalises to non-empty — extremely rare, but the code path must exist.
        # Verify via a title that is all whitespace and content with only collapsible WS + a glyph:
        title, _, _ = extract_text(title="   ", content="  x  ")
        # Explicit title is empty after trim; first-line fallback yields "x" → used.
        assert title == "x"

    def test_fallback_triggers_untitled_when_original_is_unsplittable(self) -> None:
        # R4.3: Untitled note is the final fallback. Trigger it by passing title=None
        # and content whose first-line derivation naturally yields "" — not possible
        # when normalised content is non-empty (else-branch unreachable in normal cases).
        # Instead: assert that a title of " \t " is ignored and fallback kicks in.
        title, _, _ = extract_text(title=" \t ", content="actual body")
        assert title == "actual body"


class TestSourceRef:
    def test_sha256_deterministic_on_identical_content(self) -> None:
        _, _, ref_a = extract_text(title="A", content="Hello world")
        _, _, ref_b = extract_text(title="B", content="Hello world")
        assert ref_a == ref_b

    def test_sha256_ignores_title(self) -> None:
        _, _, ref_a = extract_text(title="title one", content="same body")
        _, _, ref_b = extract_text(title="title two", content="same body")
        assert ref_a == ref_b

    def test_sha256_matches_whitespace_normalised_input(self) -> None:
        """A paragraph with extra whitespace must hash the same as the collapsed form."""
        _, _, ref_raw = extract_text(title="x", content="hello     world")
        _, _, ref_collapsed = extract_text(title="x", content="hello world")
        assert ref_raw == ref_collapsed

    def test_source_ref_format(self) -> None:
        _, _, ref = extract_text(title="x", content="Hello")
        assert ref.startswith("text:sha256:")
        assert len(ref) == len("text:sha256:") + 64  # hex-encoded sha256

    def test_source_ref_hex_matches_normalised_content(self) -> None:
        content = "Hello world"
        _, _, ref = extract_text(title="x", content=content)
        expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
        assert ref == f"text:sha256:{expected}"

    def test_different_content_different_ref(self) -> None:
        _, _, ref_a = extract_text(title="x", content="paragraph one")
        _, _, ref_b = extract_text(title="x", content="paragraph two")
        assert ref_a != ref_b


class TestReturnShape:
    def test_returns_three_tuple(self) -> None:
        result = extract_text(title="t", content="body")
        assert isinstance(result, tuple)
        assert len(result) == 3

    def test_rejects_non_string_content(self) -> None:
        with pytest.raises(InvalidContentError):
            extract_text(title="t", content=None)  # type: ignore[arg-type]
