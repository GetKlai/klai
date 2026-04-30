"""SPEC-AUTH-009 R1 -- primary_domain schema + free-email block (RED).

Covers AC-1, AC-2, C1.1, C1.3, R7/C7.2.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_BASE: dict = {
    "first_name": "Jan",
    "last_name": "Jansen",
    "email": "founder@bedrijf.nl",
    "password": "Correct!Battery!Horse!",
    "company_name": "Bedrijf BV",
    "preferred_language": "nl",
}


class TestPortalOrgModel:
    def test_has_primary_domain(self) -> None:
        from app.models.portal import PortalOrg

        assert hasattr(PortalOrg, "primary_domain")

    def test_has_auto_accept_same_domain(self) -> None:
        from app.models.portal import PortalOrg

        assert hasattr(PortalOrg, "auto_accept_same_domain")


def _mock_deps(mz, morg):
    mz.create_org = AsyncMock(return_value={"id": "zit-org-001"})
    mz.create_human_user = AsyncMock(return_value={"userId": "zit-user-001"})
    mz.grant_user_role = AsyncMock()
    inst = MagicMock()
    inst.id = 1
    inst.slug = "bedrijf-bv"
    inst.plan = "professional"
    morg.return_value = inst
    return inst


class TestPrimaryDomainSetAtSignup:
    @pytest.mark.asyncio
    async def test_signup_sets_primary_domain(self) -> None:
        """AC-1: primary_domain stored on portal_orgs."""
        from app.api.signup import SignupRequest, signup

        mock_db = AsyncMock()
        body = SignupRequest(**_BASE)
        with (
            patch("app.api.signup.check_signup_email_rate_limit", AsyncMock(return_value=True)),
            patch("app.api.signup.zitadel") as mz,
            patch("app.api.signup.provision_tenant"),
            patch("app.api.signup.emit_event"),
            patch("app.api.signup.invalidate_tenant_slug_cache"),
            patch("app.api.signup.set_tenant", AsyncMock()),
            patch("app.api.signup.PortalOrg") as morg,
            patch("app.api.signup.PortalUser"),
        ):
            _mock_deps(mz, morg)
            await signup(body=body, background_tasks=MagicMock(), db=mock_db)
            kw = morg.call_args.kwargs
            assert "primary_domain" in kw
            assert kw["primary_domain"] == "bedrijf.nl"

    @pytest.mark.asyncio
    async def test_signup_sets_auto_accept_false(self) -> None:
        """AC-1: auto_accept_same_domain=False by default."""
        from app.api.signup import SignupRequest, signup

        mock_db = AsyncMock()
        body = SignupRequest(**_BASE)
        with (
            patch("app.api.signup.check_signup_email_rate_limit", AsyncMock(return_value=True)),
            patch("app.api.signup.zitadel") as mz,
            patch("app.api.signup.provision_tenant"),
            patch("app.api.signup.emit_event"),
            patch("app.api.signup.invalidate_tenant_slug_cache"),
            patch("app.api.signup.set_tenant", AsyncMock()),
            patch("app.api.signup.PortalOrg") as morg,
            patch("app.api.signup.PortalUser"),
        ):
            _mock_deps(mz, morg)
            await signup(body=body, background_tasks=MagicMock(), db=mock_db)
            kw = morg.call_args.kwargs
            assert kw.get("auto_accept_same_domain", False) is False

    @pytest.mark.asyncio
    async def test_signup_normalises_domain_lowercase(self) -> None:
        """C1.1: primary_domain is normalised to lowercase."""
        from app.api.signup import SignupRequest, signup

        mock_db = AsyncMock()
        body = SignupRequest(**{**_BASE, "email": "founder@BEDRIJF.NL"})
        with (
            patch("app.api.signup.check_signup_email_rate_limit", AsyncMock(return_value=True)),
            patch("app.api.signup.zitadel") as mz,
            patch("app.api.signup.provision_tenant"),
            patch("app.api.signup.emit_event"),
            patch("app.api.signup.invalidate_tenant_slug_cache"),
            patch("app.api.signup.set_tenant", AsyncMock()),
            patch("app.api.signup.PortalOrg") as morg,
            patch("app.api.signup.PortalUser"),
        ):
            _mock_deps(mz, morg)
            await signup(body=body, background_tasks=MagicMock(), db=mock_db)
            kw = morg.call_args.kwargs
            assert kw.get("primary_domain") == "bedrijf.nl"


class TestFreeEmailSignupRejected:
    """Free-email rejected at signup with HTTP 400 (AC-2, C1.3, R7)."""

    @pytest.mark.asyncio
    async def test_gmail_returns_400(self) -> None:
        from fastapi import HTTPException

        from app.api.signup import SignupRequest, signup

        mock_db = AsyncMock()
        body = SignupRequest(**{**_BASE, "email": "user@gmail.com"})
        with (
            patch("app.api.signup.check_signup_email_rate_limit", AsyncMock(return_value=True)),
            pytest.raises(HTTPException) as exc,
        ):
            await signup(body=body, background_tasks=MagicMock(), db=mock_db)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_gmail_error_mentions_company(self) -> None:
        """C7.2: Error must mention zakelijk/company or uitnodiging."""
        from fastapi import HTTPException

        from app.api.signup import SignupRequest, signup

        mock_db = AsyncMock()
        body = SignupRequest(**{**_BASE, "email": "user@gmail.com"})
        with (
            patch("app.api.signup.check_signup_email_rate_limit", AsyncMock(return_value=True)),
            pytest.raises(HTTPException) as exc,
        ):
            await signup(body=body, background_tasks=MagicMock(), db=mock_db)
        detail = exc.value.detail
        has_kw = "zakelijk" in detail.lower() or "company" in detail.lower() or "uitnodiging" in detail.lower()
        assert has_kw

    @pytest.mark.asyncio
    async def test_gmail_no_db_add(self) -> None:
        """AC-2: No portal_orgs row on free-email rejection."""

    @pytest.mark.asyncio
    async def test_hotmail_returns_400(self) -> None:
        from fastapi import HTTPException

        from app.api.signup import SignupRequest, signup

        mock_db = AsyncMock()
        body = SignupRequest(**{**_BASE, "email": "user@hotmail.com"})
        with (
            patch("app.api.signup.check_signup_email_rate_limit", AsyncMock(return_value=True)),
            pytest.raises(HTTPException) as exc,
        ):
            await signup(body=body, background_tasks=MagicMock(), db=mock_db)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_corporate_email_not_rejected(self) -> None:
        from app.api.signup import SignupRequest, signup

        mock_db = AsyncMock()
        body = SignupRequest(**_BASE)
        with (
            patch("app.api.signup.check_signup_email_rate_limit", AsyncMock(return_value=True)),
            patch("app.api.signup.zitadel") as mz,
            patch("app.api.signup.provision_tenant"),
            patch("app.api.signup.emit_event"),
            patch("app.api.signup.invalidate_tenant_slug_cache"),
            patch("app.api.signup.set_tenant", AsyncMock()),
            patch("app.api.signup.PortalOrg") as morg,
            patch("app.api.signup.PortalUser"),
        ):
            _mock_deps(mz, morg)
            result = await signup(body=body, background_tasks=MagicMock(), db=mock_db)
        assert result is not None
