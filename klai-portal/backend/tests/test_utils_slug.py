"""Tests for app.utils.slug.slugify.

Locks in the slug-generation contract documented in SPEC-CHAT-TEMPLATES-001
REQ-TEMPLATES-CRUD-U2. Callers rely on:
- lowercase output
- hyphen-collapsed runs
- 64-char truncation
- empty string for empty-ish input (caller MUST reject with 400)
"""

from __future__ import annotations

import pytest

from app.utils.slug import MAX_SLUG_LENGTH, slugify


class TestSlugify:
    def test_simple_name(self) -> None:
        assert slugify("Klantenservice") == "klantenservice"

    def test_name_with_spaces(self) -> None:
        assert slugify("Customer Service") == "customer-service"

    def test_preserves_existing_hyphens(self) -> None:
        assert slugify("Formal-Tone") == "formal-tone"

    def test_collapses_multiple_spaces_and_hyphens(self) -> None:
        assert slugify("Creatief  --  Stijl") == "creatief-stijl"

    def test_strips_punctuation(self) -> None:
        assert slugify("E-mailadressen redacten!") == "e-mailadressen-redacten"

    def test_strips_leading_trailing_whitespace(self) -> None:
        assert slugify("  Klantenservice  ") == "klantenservice"

    def test_strips_leading_trailing_hyphens(self) -> None:
        assert slugify("---hoi---") == "hoi"

    def test_preserves_unicode_word_chars(self) -> None:
        # Python 3 re \w is Unicode-aware by default.
        assert slugify("café") == "café"

    def test_truncates_to_max_length(self) -> None:
        long_name = "a" * 200
        assert len(slugify(long_name)) == MAX_SLUG_LENGTH
        assert slugify(long_name) == "a" * MAX_SLUG_LENGTH

    def test_empty_string_returns_empty(self) -> None:
        assert slugify("") == ""

    def test_whitespace_only_returns_empty(self) -> None:
        assert slugify("   ") == ""

    def test_punctuation_only_returns_empty(self) -> None:
        assert slugify("!@#$%^&*()") == ""

    def test_deterministic(self) -> None:
        # Same input → same output, every time.
        assert slugify("Customer Support") == slugify("Customer Support")

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Klantenservice", "klantenservice"),
            ("Formeel", "formeel"),
            ("Creatief", "creatief"),
            ("Samenvatter", "samenvatter"),
        ],
    )
    def test_default_template_names(self, raw: str, expected: str) -> None:
        # Verifies the slugs used by default_templates.DEFAULT_TEMPLATES.
        assert slugify(raw) == expected
