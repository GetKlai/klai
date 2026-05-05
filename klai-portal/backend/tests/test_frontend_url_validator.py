"""SPEC-CODEBASE-AUDIT-001 Adversarial Findings 3+4 — frontend_url host allowlist.

Validates that `Settings._validate_frontend_url_host` rejects FRONTEND_URL values
whose hostname is not in the trusted allowlist (localhost, configured `domain`,
or subdomain of configured `domain`). Closes the open-redirect / token-capture
exploit chain via SOPS drift on FRONTEND_URL.

Mirrors the test pattern of `test_validate_callback_url.py`.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings

# Required env-var dependencies satisfied by conftest.py (DATABASE_URL, ZITADEL_PAT,
# SSO_COOKIE_KEY, PORTAL_SECRETS_KEY, ENCRYPTION_KEY, VEXA_WEBHOOK_SECRET,
# MONEYBIRD_WEBHOOK_TOKEN, ZITADEL_IDP_*); these tests focus on frontend_url only.


class TestFrontendUrlAllowed:
    def test_empty_string_passes_with_default_fallback(self) -> None:
        s = Settings(frontend_url="")
        assert s.frontend_url == ""
        # portal_url falls back to https://portal.{domain}
        assert s.portal_url == f"https://portal.{s.domain}"

    def test_whitespace_only_does_not_raise_validator(self) -> None:
        # Whitespace-only is treated as empty by our validator (no exception).
        # Note: the `portal_url` property's `or`-fallback only triggers on
        # truly-empty strings, so whitespace propagates verbatim. Out of scope
        # for this validator — covered by SPEC-CODEBASE-AUDIT-001 cleanup.
        s = Settings(frontend_url="   ")
        # Just assert no ValidationError was raised
        assert s.frontend_url == "   "

    def test_canonical_login_subdomain_passes(self) -> None:
        s = Settings(frontend_url="https://my.getklai.com")
        assert s.portal_url == "https://my.getklai.com"

    def test_apex_domain_passes(self) -> None:
        s = Settings(frontend_url="https://getklai.com")
        assert s.portal_url == "https://getklai.com"

    def test_arbitrary_subdomain_of_domain_passes(self) -> None:
        s = Settings(frontend_url="https://chat.getklai.com")
        assert s.portal_url == "https://chat.getklai.com"

    def test_localhost_with_port_passes(self) -> None:
        s = Settings(frontend_url="http://localhost:5174", portal_env="development")
        assert s.portal_url == "http://localhost:5174"

    def test_127_0_0_1_passes(self) -> None:
        s = Settings(frontend_url="http://127.0.0.1:5174", portal_env="development")
        assert s.portal_url == "http://127.0.0.1:5174"


class TestFrontendUrlRejected:
    def test_attacker_domain_rejected(self) -> None:
        with pytest.raises(ValidationError, match="not in the trusted allowlist"):
            Settings(frontend_url="https://attacker.example")

    def test_lookalike_domain_rejected(self) -> None:
        # getklai.com.attacker.tld must NOT be matched as subdomain of getklai.com
        with pytest.raises(ValidationError, match="not in the trusted allowlist"):
            Settings(frontend_url="https://my.getklai.com.attacker.tld")

    def test_invalid_scheme_rejected(self) -> None:
        with pytest.raises(ValidationError, match="scheme http or https"):
            Settings(frontend_url="ftp://my.getklai.com")

    def test_javascript_scheme_rejected(self) -> None:
        with pytest.raises(ValidationError, match="scheme http or https"):
            Settings(frontend_url="javascript:alert(1)")

    def test_production_http_rejected_for_domain(self) -> None:
        # debug=False to skip the unrelated _no_debug_in_production validator
        with pytest.raises(ValidationError, match="must use https in production"):
            Settings(
                frontend_url="http://my.getklai.com",
                portal_env="production",
                debug=False,
            )

    def test_production_localhost_still_allowed(self) -> None:
        # localhost is allowed in any env (canary/dev exception)
        s = Settings(
            frontend_url="http://localhost:5174",
            portal_env="production",
            debug=False,
        )
        assert s.portal_url == "http://localhost:5174"
