"""SPEC-AUTH-009 R2 — Drop portal_org_allowed_domains artifacts.

RED tests: these tests verify that the SPEC-AUTH-006 allowlist
artifacts have been removed. They will FAIL until the removal is done.

AC-12 (derived from R2 constraints):
- PortalOrgAllowedDomain is NOT importable from app.models.portal
- app.api.admin.domains router does NOT exist
- Frontend file routes/admin/domains.tsx does NOT exist
- i18n keys admin_domains_* are gone from messages/en.json and messages/nl.json
- test_admin_domains.py and test_allowed_domains.py are deleted
- Free-email blocklist helpers (is_free_email_provider, normalize_domain,
  is_valid_domain) are still importable and functional (C2.6 keep clause)
"""

from __future__ import annotations

import importlib
import os
import sys

import pytest


class TestAllowedDomainModelGone:
    """C2.3: PortalOrgAllowedDomain class MUST be removed from portal.py."""

    def test_portal_org_allowed_domain_not_importable(self) -> None:
        """PortalOrgAllowedDomain should raise ImportError / AttributeError after removal."""
        with pytest.raises((ImportError, AttributeError)):
            from app.models.portal import PortalOrgAllowedDomain  # noqa: F401


class TestAdminDomainsRouterGone:
    """C2.2: app/api/admin/domains.py MUST be removed."""

    def test_admin_domains_module_not_importable(self) -> None:
        """Importing app.api.admin.domains should raise ModuleNotFoundError."""
        # Evict from cache to get a clean import
        sys.modules.pop("app.api.admin.domains", None)
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("app.api.admin.domains")


class TestFrontendDomainsRouteGone:
    """C2.4: frontend/src/routes/admin/domains.tsx MUST be deleted."""

    def test_domains_tsx_file_deleted(self) -> None:
        """The frontend domains route file must not exist."""
        # Path relative to the repo root (worktree root)
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        domains_tsx = os.path.join(
            repo_root,
            "klai-portal",
            "frontend",
            "src",
            "routes",
            "admin",
            "domains.tsx",
        )
        assert not os.path.exists(domains_tsx), f"domains.tsx still exists at {domains_tsx}; should be deleted per C2.4"


class TestI18nKeyGone:
    """C2.5: admin_domains_* i18n keys MUST be removed from message files."""

    def _load_messages(self, lang: str) -> str:
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        msg_path = os.path.join(repo_root, "klai-portal", "frontend", "messages", f"{lang}.json")
        if not os.path.exists(msg_path):
            pytest.skip(f"Message file {msg_path} not found")
        with open(msg_path, encoding="utf-8") as fh:
            return fh.read()

    def test_en_json_has_no_admin_domains_keys(self) -> None:
        content = self._load_messages("en")
        assert "admin_domains_" not in content, "en.json still contains admin_domains_* keys; remove per C2.5"

    def test_nl_json_has_no_admin_domains_keys(self) -> None:
        content = self._load_messages("nl")
        assert "admin_domains_" not in content, "nl.json still contains admin_domains_* keys; remove per C2.5"


class TestDomainValidationHelpersPreserved:
    """C2.6 keep clause: is_free_email_provider/normalize_domain/is_valid_domain remain."""

    def test_is_free_email_provider_importable(self) -> None:
        from app.services.domain_validation import is_free_email_provider

        assert is_free_email_provider("gmail.com") is True

    def test_normalize_domain_importable(self) -> None:
        from app.services.domain_validation import normalize_domain

        assert normalize_domain("  ACME.NL  ") == "acme.nl"

    def test_is_valid_domain_importable(self) -> None:
        from app.services.domain_validation import is_valid_domain

        assert is_valid_domain("acme.nl") is True
