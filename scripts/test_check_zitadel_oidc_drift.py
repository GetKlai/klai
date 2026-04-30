"""Unit tests for the OIDC drift-check script.

The script in `scripts/check_zitadel_oidc_drift.py` runs against live
Zitadel from the workflow; these tests cover the pure-logic helpers
(_classify, _extract_first_labels) so we catch regressions in the
classifier without needing Zitadel access.

Run from repo root:
    python -m pytest scripts/test_check_zitadel_oidc_drift.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

# The script lives in scripts/ alongside this test; add to sys.path so
# the regular import works without a packaging change.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from check_zitadel_oidc_drift import (  # noqa: E402
    DEFAULT_EXPECTED_STATIC,
    _classify,
    _extract_first_labels,
)


class TestClassify:
    """Cover every branch of `_classify`."""

    def test_static_label_classified_static(self) -> None:
        for label in DEFAULT_EXPECTED_STATIC:
            assert _classify(label, DEFAULT_EXPECTED_STATIC) == "static", label

    def test_single_word_label_classified_tenant(self) -> None:
        # Tenant-shaped: no dashes, not in static set.
        for label in ("voys", "getklai", "acme", "neworg"):
            assert _classify(label, DEFAULT_EXPECTED_STATIC) == "tenant"

    def test_chat_prefix_label_classified_chat_prefix(self) -> None:
        for label in ("chat-voys", "chat-getklai", "chat-acme", "chat-x"):
            assert _classify(label, DEFAULT_EXPECTED_STATIC) == "chat-prefix"

    def test_unknown_dashed_label_classified_unknown(self) -> None:
        # A new system service that didn't exist when this script was
        # written must classify as `unknown` so the drift check fails.
        for label in ("api-dev", "dashboard-staging", "weird-thing"):
            assert _classify(label, DEFAULT_EXPECTED_STATIC) == "unknown"

    def test_chat_alone_is_static_not_chat_prefix(self) -> None:
        """Edge: bare ``chat`` (no dash) is classified as static when in
        the expected set, NOT as chat-prefix (which requires a non-empty
        tail after ``chat-``)."""
        assert _classify("chat", DEFAULT_EXPECTED_STATIC) == "static"

    def test_chat_dash_alone_classified_unknown(self) -> None:
        """Edge: ``chat-`` with empty tail (no slug after the dash) is
        not a valid per-tenant pattern."""
        # `chat-` doesn't match `len > "chat-"`; falls through to unknown.
        assert _classify("chat-", DEFAULT_EXPECTED_STATIC) == "unknown"


class TestExtractFirstLabels:
    """Cover the redirect_uri parser used to drive the classifier."""

    def test_extracts_first_label_under_domain(self) -> None:
        apps = [
            {
                "oidcConfig": {
                    "redirectUris": [
                        "https://chat-getklai.getklai.com/oauth/openid/callback",
                        "https://my.getklai.com/api/auth/oidc/callback",
                    ],
                },
            },
        ]
        labels = _extract_first_labels(apps, "getklai.com")
        assert labels == {"chat-getklai", "my"}

    def test_ignores_non_klai_redirect_uris(self) -> None:
        apps = [
            {
                "oidcConfig": {
                    "redirectUris": [
                        "http://localhost:3010/docs/api/auth/callback/zitadel",
                        "https://example.com/callback",
                        "https://chat.getklai.com/oauth/openid/callback",
                    ],
                },
            },
        ]
        labels = _extract_first_labels(apps, "getklai.com")
        assert labels == {"chat"}

    def test_ignores_bare_apex(self) -> None:
        """Bare apex (no subdomain) is not a "first label" — skip it."""
        apps = [
            {
                "oidcConfig": {
                    "redirectUris": [
                        "https://getklai.com/docs/api/auth/callback/zitadel",
                        "https://chat.getklai.com/cb",
                    ],
                },
            },
        ]
        labels = _extract_first_labels(apps, "getklai.com")
        assert labels == {"chat"}

    def test_takes_only_first_label_for_multi_subdomain(self) -> None:
        """For ``foo.bar.getklai.com`` we keep ``foo`` (the first label
        of the subdomain part)."""
        apps = [
            {
                "oidcConfig": {
                    "redirectUris": [
                        "https://foo.bar.getklai.com/cb",
                    ],
                },
            },
        ]
        labels = _extract_first_labels(apps, "getklai.com")
        assert labels == {"foo"}

    def test_handles_app_without_oidc_config(self) -> None:
        """Apps that have no oidcConfig (e.g. API-only apps) are skipped
        without raising."""
        apps = [
            {"name": "non-oidc-app"},
            {"oidcConfig": {}},
            {"oidcConfig": {"redirectUris": []}},
            {
                "oidcConfig": {
                    "redirectUris": ["https://chat.getklai.com/cb"],
                },
            },
        ]
        labels = _extract_first_labels(apps, "getklai.com")
        assert labels == {"chat"}

    def test_handles_empty_redirect_list(self) -> None:
        labels = _extract_first_labels([], "getklai.com")
        assert labels == set()

    def test_lowercases_host(self) -> None:
        """Operator copy-paste with mixed case still classifies correctly."""
        apps = [
            {
                "oidcConfig": {
                    "redirectUris": ["https://CHAT.GetKlai.com/cb"],
                },
            },
        ]
        labels = _extract_first_labels(apps, "getklai.com")
        assert labels == {"chat"}


class TestEndToEndScenarios:
    """Combinations of `_extract_first_labels` + `_classify` that mirror
    the actual drift-detection logic the script runs."""

    def test_only_known_hosts_no_drift(self) -> None:
        apps = [
            {
                "oidcConfig": {
                    "redirectUris": [
                        "https://chat.getklai.com/cb",
                        "https://chat-dev.getklai.com/cb",
                        "https://my.getklai.com/cb",  # static (FRONTEND_URL)
                        "https://chat-getklai.getklai.com/cb",  # tenant chat
                        "https://voys.getklai.com/cb",  # tenant
                    ],
                },
            },
        ]
        labels = _extract_first_labels(apps, "getklai.com")
        unclassified = [
            label
            for label in labels
            if _classify(label, DEFAULT_EXPECTED_STATIC) == "unknown"
        ]
        # `my` is not in DEFAULT_EXPECTED_STATIC because FRONTEND_URL is
        # added at runtime from settings; the drift script intentionally
        # treats `my` as tenant-shaped (single label, no dash) which is
        # acceptable — operators can extend EXPECTED if false-positive.
        assert unclassified == []

    def test_new_unknown_system_label_is_drift(self) -> None:
        apps = [
            {
                "oidcConfig": {
                    "redirectUris": [
                        "https://staging-api.getklai.com/cb",  # NEW dashed
                    ],
                },
            },
        ]
        labels = _extract_first_labels(apps, "getklai.com")
        unclassified = [
            label
            for label in labels
            if _classify(label, DEFAULT_EXPECTED_STATIC) == "unknown"
        ]
        assert unclassified == ["staging-api"]
