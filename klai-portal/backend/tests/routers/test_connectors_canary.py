"""Tests for WebcrawlerConfig validation — SPEC-CRAWL-003 REQ-1, AC-12.

All tests named after the Test Plan in the SPEC.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.connectors import WebcrawlerConfig


class TestWebcrawlerConfigCanaryXOR:
    """AC-12: XOR validation — canary_url iff canary_fingerprint (REQ-1)."""

    def test_canary_url_without_fingerprint_accepted(self) -> None:
        """canary_url without fingerprint is accepted on input (SPEC-CRAWL-004).

        The backend auto-computes the fingerprint on save via klai-connector.
        The Pydantic model allows this so the preview auth_guard flow works.
        """
        cfg = WebcrawlerConfig(
            base_url="https://wiki.example.com",
            canary_url="https://wiki.example.com/known-page",
        )
        assert cfg.canary_url == "https://wiki.example.com/known-page"
        assert cfg.canary_fingerprint is None

    def test_canary_xor_fingerprint_only(self) -> None:
        """canary_fingerprint set without canary_url → 422."""
        with pytest.raises(ValidationError) as exc_info:
            WebcrawlerConfig(
                base_url="https://wiki.example.com",
                canary_fingerprint="abc1234567890abc",
            )
        errors = exc_info.value.errors()
        assert any("canary" in str(e).lower() for e in errors), f"Expected canary error, got: {errors}"

    def test_canary_both_set_valid(self) -> None:
        """canary_url and canary_fingerprint both set → valid."""
        cfg = WebcrawlerConfig(
            base_url="https://wiki.example.com",
            canary_url="https://wiki.example.com/known-page",
            canary_fingerprint="abc1234567890abc",
        )
        assert cfg.canary_url == "https://wiki.example.com/known-page"
        assert cfg.canary_fingerprint == "abc1234567890abc"

    def test_canary_both_absent_valid(self) -> None:
        """Neither canary field set → valid (Layer A disabled)."""
        cfg = WebcrawlerConfig(base_url="https://wiki.example.com")
        assert cfg.canary_url is None
        assert cfg.canary_fingerprint is None


class TestWebcrawlerConfigFingerprintRegex:
    """canary_fingerprint must match ^[0-9a-f]{16}$ (SPEC Data Model Diff)."""

    def test_canary_fingerprint_invalid_not_hex(self) -> None:
        """canary_fingerprint with non-hex chars → 422."""
        with pytest.raises(ValidationError):
            WebcrawlerConfig(
                base_url="https://wiki.example.com",
                canary_url="https://wiki.example.com/page",
                canary_fingerprint="ZZZZZZZZZZZZZZZZ",  # uppercase not allowed
            )

    def test_canary_fingerprint_too_short(self) -> None:
        """canary_fingerprint shorter than 16 chars → 422."""
        with pytest.raises(ValidationError):
            WebcrawlerConfig(
                base_url="https://wiki.example.com",
                canary_url="https://wiki.example.com/page",
                canary_fingerprint="abc123",
            )

    def test_canary_fingerprint_too_long(self) -> None:
        """canary_fingerprint longer than 16 chars → 422."""
        with pytest.raises(ValidationError):
            WebcrawlerConfig(
                base_url="https://wiki.example.com",
                canary_url="https://wiki.example.com/page",
                canary_fingerprint="abc1234567890abcXXX",
            )

    def test_canary_fingerprint_exactly_16_hex_chars_valid(self) -> None:
        """Exactly 16 lowercase hex chars → valid."""
        cfg = WebcrawlerConfig(
            base_url="https://wiki.example.com",
            canary_url="https://wiki.example.com/page",
            canary_fingerprint="0123456789abcdef",
        )
        assert cfg.canary_fingerprint == "0123456789abcdef"


class TestWebcrawlerConfigCanaryUrlPrefix:
    """canary_url must start with base_url + path_prefix (SPEC Data Model Diff)."""

    def test_canary_url_outside_base_url_invalid(self) -> None:
        """canary_url from different domain → 422."""
        with pytest.raises(ValidationError):
            WebcrawlerConfig(
                base_url="https://wiki.example.com",
                canary_url="https://other.example.com/page",
                canary_fingerprint="0123456789abcdef",
            )

    def test_canary_url_within_base_url_valid(self) -> None:
        """canary_url starting with base_url → valid."""
        cfg = WebcrawlerConfig(
            base_url="https://wiki.example.com",
            canary_url="https://wiki.example.com/en/article",
            canary_fingerprint="0123456789abcdef",
        )
        assert cfg.canary_url == "https://wiki.example.com/en/article"

    def test_canary_url_outside_path_prefix_invalid(self) -> None:
        """canary_url not starting with base_url + path_prefix → 422."""
        with pytest.raises(ValidationError):
            WebcrawlerConfig(
                base_url="https://wiki.example.com",
                path_prefix="/en",
                canary_url="https://wiki.example.com/de/article",
                canary_fingerprint="0123456789abcdef",
            )

    def test_canary_url_within_path_prefix_valid(self) -> None:
        """canary_url within base_url + path_prefix → valid."""
        cfg = WebcrawlerConfig(
            base_url="https://wiki.example.com",
            path_prefix="/en",
            canary_url="https://wiki.example.com/en/article",
            canary_fingerprint="0123456789abcdef",
        )
        assert cfg.canary_url == "https://wiki.example.com/en/article"


class TestWebcrawlerConfigLoginIndicatorSelector:
    """login_indicator_selector validation (SPEC Data Model Diff)."""

    def test_selector_valid_class_selector(self) -> None:
        """Valid CSS class selector → accepted."""
        cfg = WebcrawlerConfig(
            base_url="https://wiki.example.com",
            login_indicator_selector=".logged-in-user-menu",
        )
        assert cfg.login_indicator_selector == ".logged-in-user-menu"

    def test_selector_valid_attribute_selector(self) -> None:
        """Valid CSS attribute selector → accepted."""
        cfg = WebcrawlerConfig(
            base_url="https://wiki.example.com",
            login_indicator_selector="a[href*=logout]",
        )
        assert cfg.login_indicator_selector == "a[href*=logout]"

    def test_selector_empty_string_invalid(self) -> None:
        """Empty login_indicator_selector → 422."""
        with pytest.raises(ValidationError):
            WebcrawlerConfig(
                base_url="https://wiki.example.com",
                login_indicator_selector="",
            )

    def test_selector_with_javascript_uri_invalid(self) -> None:
        """login_indicator_selector containing 'javascript:' → 422 (XSS vector)."""
        with pytest.raises(ValidationError):
            WebcrawlerConfig(
                base_url="https://wiki.example.com",
                login_indicator_selector="a[href^=javascript:void(0)]",
            )

    def test_selector_with_legitimate_script_class_valid(self) -> None:
        """Legitimate CSS selectors with 'script' substring are accepted.

        `.transcript`, `[data-script-version]`, and `script[type]` (element
        selector) are all valid CSS — only HTML/JS injection shapes (`<script`,
        `javascript:`) are rejected. See WebcrawlerConfig validator rationale.
        """
        cfg = WebcrawlerConfig(
            base_url="https://wiki.example.com",
            login_indicator_selector="[data-script-version]",
        )
        assert cfg.login_indicator_selector == "[data-script-version]"

    def test_selector_with_angle_bracket_invalid(self) -> None:
        """login_indicator_selector containing '<' → 422."""
        with pytest.raises(ValidationError):
            WebcrawlerConfig(
                base_url="https://wiki.example.com",
                login_indicator_selector="<script>",
            )

    def test_selector_with_gt_bracket_invalid(self) -> None:
        """login_indicator_selector containing '>' → 422."""
        with pytest.raises(ValidationError):
            WebcrawlerConfig(
                base_url="https://wiki.example.com",
                login_indicator_selector="div > span",
            )

    def test_selector_case_insensitive_javascript_check(self) -> None:
        """login_indicator_selector with 'JAVASCRIPT:' (uppercase) → 422."""
        with pytest.raises(ValidationError):
            WebcrawlerConfig(
                base_url="https://wiki.example.com",
                login_indicator_selector="a[href=JAVASCRIPT:alert(1)]",
            )

    def test_no_selector_valid(self) -> None:
        """No login_indicator_selector → valid (Layer B disabled)."""
        cfg = WebcrawlerConfig(base_url="https://wiki.example.com")
        assert cfg.login_indicator_selector is None


class TestWebcrawlerConfigExistingFields:
    """Existing fields remain unchanged and all new fields default to None."""

    def test_existing_fields_unchanged(self) -> None:
        """Existing fields still work as before SPEC-CRAWL-003."""
        cfg = WebcrawlerConfig(
            base_url="https://wiki.example.com",
            path_prefix="/en",
            max_pages=100,
            max_depth=5,
            content_selector=".content",
            cookies=[{"name": "session", "value": "abc"}],
        )
        assert cfg.base_url == "https://wiki.example.com"
        assert cfg.path_prefix == "/en"
        assert cfg.max_pages == 100
        assert cfg.max_depth == 5
        assert cfg.content_selector == ".content"

    def test_new_fields_default_to_none(self) -> None:
        """All three new fields default to None when not specified."""
        cfg = WebcrawlerConfig(base_url="https://wiki.example.com")
        assert cfg.canary_url is None
        assert cfg.canary_fingerprint is None
        assert cfg.login_indicator_selector is None
