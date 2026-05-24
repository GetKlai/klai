"""Tests for REQ-2 (Finding B-2): empty allowed_origins must default-deny.

SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-2.

AC-tested:
- AC2.1: origin_allowed(origin, [], allow_any_origin=False) -> False
- AC2.2: origin_allowed(origin, [], allow_any_origin=True) -> True
- AC2.3: origin_allowed(origin, ["https://a.com"], allow_any_origin=False) -> True for matching
- AC2.4: origin_allowed(origin, ["https://a.com"], allow_any_origin=False) -> False for non-matching
- AC2.5: allow_any_origin defaults to False (backward-compat break must be explicit)
"""

from __future__ import annotations

from app.services.widget_auth import origin_allowed


class TestOriginAllowedDefaultDeny:
    """REQ-2 AC2.1 / AC2.2 — empty list behaviour."""

    def test_empty_allowed_origins_with_allow_any_false_denies(self):
        """AC2.1 — Empty allowed_origins + allow_any_origin=False must return False.

        Pre-REQ-2 behaviour: empty list returned True (open to the world).
        Post-REQ-2: empty list returns False unless allow_any_origin=True.
        """
        assert origin_allowed("https://example.com", [], allow_any_origin=False) is False

    def test_empty_allowed_origins_with_allow_any_true_allows(self):
        """AC2.2 — Empty allowed_origins + allow_any_origin=True must return True."""
        assert origin_allowed("https://example.com", [], allow_any_origin=True) is True

    def test_allow_any_origin_defaults_to_false(self):
        """AC2.5 — allow_any_origin defaults to False so callers must opt-in explicitly."""
        # Calling without the keyword must deny an empty list.
        assert origin_allowed("https://example.com", []) is False

    def test_non_empty_list_still_matches_correctly(self):
        """AC2.3 — Non-empty allowed_origins still perform exact matching."""
        assert origin_allowed("https://example.com", ["https://example.com"], allow_any_origin=False) is True

    def test_non_empty_list_still_denies_non_matching(self):
        """AC2.4 — Non-empty allowed_origins denies an origin not in the list."""
        assert origin_allowed("https://other.com", ["https://example.com"], allow_any_origin=False) is False

    def test_allow_any_origin_true_with_non_empty_list_still_allows(self):
        """allow_any_origin=True is an open-world flag — list content is irrelevant."""
        assert origin_allowed("https://any.com", ["https://example.com"], allow_any_origin=True) is True

    def test_allow_any_origin_false_preserves_wildcard_subdomain_matching(self):
        """Wildcard subdomain matching still works when allow_any_origin=False."""
        assert (
            origin_allowed(
                "https://app.example.com",
                ["https://*.example.com"],
                allow_any_origin=False,
            )
            is True
        )


class TestOriginAllowedWidgetRowIntegration:
    """REQ-2 — verify partner.py passes allow_any_origin from widget_row correctly.

    These tests exercise the production code path in partner.py where
    widget_row.allow_any_origin is forwarded to origin_allowed().

    We test the logic via direct calls to origin_allowed with the flag,
    matching what partner.py will do after the GREEN phase.
    """

    def test_widget_with_allow_any_origin_true_accepts_any_caller(self):
        """Widget with allow_any_origin=True works regardless of the origins list."""
        # Real widget rows after migration with public_share_enabled=True get allow_any_origin=True
        assert origin_allowed("https://phishing.example.com", [], allow_any_origin=True) is True

    def test_widget_with_allow_any_origin_false_and_empty_list_denies_all(self):
        """Widget with allow_any_origin=False + empty list denies all origins."""
        # Real widget rows migrated from public_share_enabled=False get explicit subdomain
        # in allowed_origins, but this tests the guard itself.
        assert origin_allowed("https://anything.com", [], allow_any_origin=False) is False
