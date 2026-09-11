"""Tests for WebcrawlerConfig validation — SPEC-CRAWL-003 REQ-1, AC-12.

All tests named after the Test Plan in the SPEC.

SPEC-SEC-SSRF-001 note: these tests use placeholder hostnames like
``wiki.example.com`` that do not resolve in CI's sandboxed network.
The autouse fixture below stubs the SSRF validator's blocking
resolver to a public IP so these pre-existing tests keep exercising
their own canary / fingerprint / selector validation contracts —
the SSRF reject-list itself is covered by ``test_connectors_ssrf.py``.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException
from klai_image_storage.url_guard import _reset_dns_cache
from pydantic import ValidationError

from app.api.connectors import (
    WebcrawlerConfig,
    _assert_no_valueless_cookies,
    _resolve_kept_cookies,
    _validate_connector_config,
)


@pytest.fixture(autouse=True)
def _stub_dns_resolver():
    """Make ``wiki.example.com`` etc. look like a public hostname."""

    _reset_dns_cache()
    with patch(
        "klai_image_storage.url_guard._resolve_blocking",
        return_value=("93.184.216.34",),
    ):
        yield


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


class TestWebcrawlerConfigDiscoverySeed:
    """discovery_seed_url: optional fallback crawl seed, scoped within base_url."""

    def test_seed_within_base_url_accepted(self) -> None:
        cfg = WebcrawlerConfig(
            base_url="https://wiki.example.com",
            discovery_seed_url="https://wiki.example.com/articles/detail/a_id/1",
        )
        assert cfg.discovery_seed_url == "https://wiki.example.com/articles/detail/a_id/1"

    def test_seed_within_path_prefix_accepted(self) -> None:
        cfg = WebcrawlerConfig(
            base_url="https://wiki.example.com",
            path_prefix="/kb",
            discovery_seed_url="https://wiki.example.com/kb/article-1",
        )
        assert cfg.discovery_seed_url == "https://wiki.example.com/kb/article-1"

    def test_seed_outside_base_url_rejected(self) -> None:
        with pytest.raises(ValidationError, match="discovery_seed_url must start with"):
            WebcrawlerConfig(
                base_url="https://wiki.example.com",
                discovery_seed_url="https://other.example.com/article",
            )

    def test_seed_outside_path_prefix_rejected(self) -> None:
        with pytest.raises(ValidationError, match="discovery_seed_url must start with"):
            WebcrawlerConfig(
                base_url="https://wiki.example.com",
                path_prefix="/kb",
                discovery_seed_url="https://wiki.example.com/blog/post-1",
            )

    def test_no_seed_is_fine(self) -> None:
        cfg = WebcrawlerConfig(base_url="https://wiki.example.com")
        assert cfg.discovery_seed_url is None


class TestWebcrawlerConfigTestUrl:
    """test_url: the persisted auth-probe "URL to test".

    Checked through ``_validate_connector_config`` — the exact save-path
    contract: it returns the dict stored in ``portal_connectors.config`` and
    raises HTTPException(422) on rejection. The test URL is the destination
    decrypted saved cookies travel to, so it must stay on base_url's ORIGIN
    — deliberately NOT bound to the tighter base_url + path_prefix scope of
    canary_url: the login wall may sit outside the crawled subtree.
    """

    def test_offprefix_same_origin_test_url_is_persisted(self) -> None:
        stored = _validate_connector_config(
            "web_crawler",
            {
                "base_url": "https://wiki.example.com",
                "path_prefix": "/docs",
                "test_url": "https://wiki.example.com/nl/inloggen",
            },
        )
        assert stored["test_url"] == "https://wiki.example.com/nl/inloggen"

    def test_cross_origin_test_url_rejected(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            _validate_connector_config(
                "web_crawler",
                {
                    "base_url": "https://wiki.example.com",
                    "test_url": "https://other.example.com/nl/inloggen",
                },
            )
        assert exc_info.value.status_code == 422
        assert "test_url" in str(exc_info.value.detail)

    def test_empty_test_url_not_stored_as_choice(self) -> None:
        """Empty means "no stored choice" — the wizard keeps falling back to
        the derived base URL; a literal "" must not become a persisted value."""
        stored = _validate_connector_config(
            "web_crawler",
            {"base_url": "https://wiki.example.com", "test_url": ""},
        )
        assert stored["test_url"] is None

    def test_absent_test_url_defaults_to_none(self) -> None:
        stored = _validate_connector_config("web_crawler", {"base_url": "https://wiki.example.com"})
        assert stored["test_url"] is None

    def test_explicit_default_port_is_the_same_origin(self) -> None:
        """The wizard uses the browser's URL.origin, which drops a scheme's
        default port. Comparing raw netloc here rejected a value the wizard
        had just accepted, so the save 422'd on something the operator was
        told was fine."""
        stored = _validate_connector_config(
            "web_crawler",
            {
                "base_url": "https://wiki.example.com",
                "test_url": "https://wiki.example.com:443/nl/inloggen",
            },
        )
        assert stored["test_url"] == "https://wiki.example.com:443/nl/inloggen"

    def test_different_port_is_a_different_origin(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            _validate_connector_config(
                "web_crawler",
                {
                    "base_url": "https://wiki.example.com",
                    "test_url": "https://wiki.example.com:8443/nl/inloggen",
                },
            )
        assert exc_info.value.status_code == 422


class TestReplaceOneCookie:
    """Refreshing one expired cookie must not delete the others.

    The wizard prefills the saved cookie NAMES with empty values, so replacing
    one means typing one value and leaving the rest alone. Those blank rows
    used to be dropped before the request, and the backend then replaced the
    whole stored set with the single cookie that had been typed.
    """

    @staticmethod
    def _saved(*pairs: tuple[str, str]) -> dict:
        return {"cookies": [{"name": n, "value": v, "domain": "w.example.com", "path": "/"} for n, v in pairs]}

    def test_a_blank_row_keeps_the_stored_value(self) -> None:
        resolved = _resolve_kept_cookies(
            {
                "cookies": [
                    {"name": "sess", "value": "new1", "domain": "w.example.com", "path": "/"},
                    {"name": "xsrf", "domain": "w.example.com", "path": "/"},
                ]
            },
            self._saved(("sess", "old1"), ("xsrf", "old2")),
        )

        assert [(c["name"], c["value"]) for c in resolved] == [
            ("sess", "new1"),
            ("xsrf", "old2"),
        ]

    def test_a_removed_row_removes_the_cookie(self) -> None:
        """The x in the wizard drops the row, so the name never arrives."""
        resolved = _resolve_kept_cookies(
            {"cookies": [{"name": "sess", "value": "new1", "domain": "w.example.com", "path": "/"}]},
            self._saved(("sess", "old1"), ("xsrf", "old2")),
        )

        assert [c["name"] for c in resolved] == ["sess"]

    def test_a_blank_row_for_an_unknown_name_is_refused(self) -> None:
        """Storing a cookie with no value would crawl logged out and still
        answer HTTP 200, which is the failure that hid here for four weeks."""
        with pytest.raises(HTTPException) as exc_info:
            _resolve_kept_cookies(
                {"cookies": [{"name": "typo", "domain": "w.example.com", "path": "/"}]},
                self._saved(("sess", "old1")),
            )

        assert exc_info.value.status_code == 422
        assert "typo" in str(exc_info.value.detail)

    def test_a_blank_row_never_becomes_an_empty_cookie(self) -> None:
        resolved = _resolve_kept_cookies(
            {"cookies": [{"name": "sess", "domain": "w.example.com", "path": "/"}]},
            self._saved(("sess", "old1")),
        )

        assert all(c["value"] for c in resolved)

    def test_a_valueless_cookie_never_reaches_the_vault(self) -> None:
        """Create has nothing stored to fill a blank row from, and a direct API
        call reaches the same model. Asserted once, right before encryption,
        rather than trusted to hold at every call site -- storing one raises
        nowhere later: the crawl carries a blank cookie, the site serves the
        logged-out page, and crawl4ai answers HTTP 200."""
        with pytest.raises(HTTPException) as exc_info:
            _assert_no_valueless_cookies(
                {"cookies": [{"name": "sid", "domain": "w.example.com", "path": "/"}]}
            )

        assert exc_info.value.status_code == 422
        assert "sid" in str(exc_info.value.detail)

    def test_cookies_with_values_pass_the_vault_check(self) -> None:
        _assert_no_valueless_cookies(
            {"cookies": [{"name": "sid", "value": "x", "domain": "w.example.com", "path": "/"}]}
        )
