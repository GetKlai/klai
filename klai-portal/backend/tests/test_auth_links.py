"""Unit tests for app.services.auth_links — SPEC-PORTAL-AUTH-EMAIL-LINKS-001 REQ-5/REQ-7."""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.services.auth_links import (
    URL_TEMPLATE_MAX_LEN,
    AuthLinkRoute,
    assert_auth_link_templates_ready,
    build_url_template,
)


class TestBuildUrlTemplate:
    def test_password_set_template_shape(self):
        """REQ-5: produces <portal_url>/password/set?userID=...&code=...&orgID=..."""
        url = build_url_template(AuthLinkRoute.PASSWORD_SET)
        base = settings.portal_url.rstrip("/")
        assert url == base + "/password/set?userID={{.UserID}}&code={{.Code}}&orgID={{.OrgID}}"

    def test_verify_email_template_shape(self):
        """REQ-4 forward-compat: verify-email route uses the same placeholders."""
        url = build_url_template(AuthLinkRoute.VERIFY_EMAIL)
        base = settings.portal_url.rstrip("/")
        assert url == base + "/verify?userID={{.UserID}}&code={{.Code}}&orgID={{.OrgID}}"

    def test_all_three_placeholders_present_literally(self):
        """Zitadel substitutes server-side; placeholders MUST appear as literal text."""
        for route in AuthLinkRoute:
            url = build_url_template(route)
            assert "{{.UserID}}" in url, f"{route.name} missing UserID placeholder"
            assert "{{.Code}}" in url, f"{route.name} missing Code placeholder"
            assert "{{.OrgID}}" in url, f"{route.name} missing OrgID placeholder"

    def test_template_under_proto_max_len(self):
        """Zitadel proto caps url_template at 200 chars across all three Send* messages."""
        for route in AuthLinkRoute:
            url = build_url_template(route)
            assert len(url) <= URL_TEMPLATE_MAX_LEN, (
                f"{route.name} template too long: {len(url)}>{URL_TEMPLATE_MAX_LEN}"
            )

    def test_trailing_slash_in_frontend_url_does_not_double(self, monkeypatch):
        """Defensive: FRONTEND_URL with trailing slash should not produce //path."""
        monkeypatch.setattr(settings, "frontend_url", "https://my.getklai.com/")
        url = build_url_template(AuthLinkRoute.PASSWORD_SET)
        assert url == "https://my.getklai.com/password/set?userID={{.UserID}}&code={{.Code}}&orgID={{.OrgID}}"
        assert "//password" not in url

    def test_oversized_frontend_url_raises(self, monkeypatch):
        """If a future env-var pushes the URL past 200 chars, fail fast."""
        long_host = "https://" + "x" * 200 + ".example.com"
        monkeypatch.setattr(settings, "frontend_url", long_host)
        with pytest.raises(RuntimeError, match="exceeds Zitadel max_len=200"):
            build_url_template(AuthLinkRoute.PASSWORD_SET)


class TestAssertAuthLinkTemplatesReady:
    """REQ-7: boot-time assertion catches misconfigured FRONTEND_URL / DOMAIN."""

    def test_passes_with_default_settings(self):
        """Default settings (FRONTEND_URL or DOMAIN-fallback) pass silently."""
        assert_auth_link_templates_ready()  # raises on failure

    def test_scheme_only_portal_url_raises(self, monkeypatch):
        """A URL with no host (e.g. ``https://``) fails the boot check."""
        monkeypatch.setattr(settings, "frontend_url", "https://")
        with pytest.raises(RuntimeError, match="not a URL"):
            assert_auth_link_templates_ready()

    def test_non_url_frontend_url_raises(self, monkeypatch):
        monkeypatch.setattr(settings, "frontend_url", "my.getklai.com")  # missing scheme
        with pytest.raises(RuntimeError, match="not a URL"):
            assert_auth_link_templates_ready()

    def test_localhost_dev_url_passes(self, monkeypatch):
        """Per SPEC REQ-7: localhost is valid in dev."""
        monkeypatch.setattr(settings, "frontend_url", "http://localhost:5173")
        assert_auth_link_templates_ready()
