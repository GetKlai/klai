"""Self-service signup fires a Klai-branded email-verification mail.

Implementation: signup uses Zitadel v2 AddHumanUser
(``zitadel.create_human_user_v2_with_verify``) which posts to
``/v2/users/human`` with ``email.verification.sendCode.urlTemplate``.
Zitadel creates the user in USER_STATE_ACTIVE AND fires
``user.human.email.code.added`` atomically. klai-mailer renders that
event through the Klai wrapper (subject "Confirm your email address
with Klai"). The button URL substitutes Zitadel's
``{{.UserID}}/{{.Code}}/{{.OrgID}}`` placeholders and lands on
``my.getklai.com/verify`` which POSTs to ``/api/auth/verify-email``.

Why NOT the legacy ``_import`` path (admin-invite still uses it): every
standalone Zitadel email API call ``/v2/users/{id}/email/send|resend|...``
rejects USER_STATE_INITIAL users with COMMAND-uz0Uu — the state ``_import``
leaves the user in when ``isEmailVerified=false``. The v2 endpoint
sidesteps that lifecycle gotcha entirely.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_BASE = {
    "first_name": "Founder",
    "last_name": "Person",
    "email": "founder@bedrijf.nl",
    "password": "VeryStrongPassw0rd!",
    "company_name": "Bedrijf BV",
    "preferred_language": "nl",
}


def _mock_zitadel(mz: MagicMock) -> None:
    mz.create_org = AsyncMock(return_value={"id": "zit-org-001"})
    mz.create_human_user_v2_with_verify = AsyncMock(return_value={"userId": "zit-user-001"})
    mz.grant_user_role = AsyncMock()


def _mock_portal_org(morg: MagicMock) -> MagicMock:
    inst = MagicMock()
    inst.id = 1
    inst.slug = "bedrijf-bv"
    inst.plan = "chat"
    morg.return_value = inst
    return inst


class TestSignupFiresVerifyMail:
    @pytest.mark.asyncio
    async def test_signup_calls_v2_with_verify_url_template(self) -> None:
        """Signup MUST use the v2 verify-aware create. The legacy
        ``create_human_user`` (_import) parks users in INITIAL state
        and the mail flow falls apart later.
        """
        from app.api.signup import SignupRequest, signup
        from app.core.config import settings

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
            _mock_zitadel(mz)
            _mock_portal_org(morg)
            await signup(body=body, background_tasks=MagicMock(), db=AsyncMock())

        mz.create_human_user_v2_with_verify.assert_called_once()
        kw = mz.create_human_user_v2_with_verify.call_args.kwargs
        # url_template lands on /verify with Zitadel placeholders
        url_template = kw.get("url_template")
        assert url_template is not None, "url_template kwarg missing"
        assert url_template.startswith(settings.portal_url.rstrip("/") + "/verify"), (
            f"url_template MUST land on /verify, got: {url_template}"
        )
        for placeholder in ("{{.UserID}}", "{{.Code}}", "{{.OrgID}}"):
            assert placeholder in url_template, f"missing placeholder {placeholder!r}"

    @pytest.mark.asyncio
    async def test_signup_does_not_call_legacy_create_human_user(self) -> None:
        """Defensive: ensure no one quietly puts the _import path back."""
        from app.api.signup import SignupRequest, signup

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
            _mock_zitadel(mz)
            _mock_portal_org(morg)
            await signup(body=body, background_tasks=MagicMock(), db=AsyncMock())

        # Whatever else changes, the legacy method must not be the one used.
        assert not mz.create_human_user.called, (
            "Signup must use create_human_user_v2_with_verify, NOT create_human_user — "
            "the _import path leaves users in USER_STATE_INITIAL and breaks the mail flow."
        )
