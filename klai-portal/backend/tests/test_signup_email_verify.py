"""Self-service signup must create users in ACTIVE state, not INITIAL.

Background: original plan was to send a Klai-branded verification mail
after signup via Zitadel /v2/users/.../email/send + a custom urlTemplate.
That endpoint rejects USER_STATE_INITIAL users (which is exactly the
state ``_import + isEmailVerified=False`` leaves them in) with error
"User is not yet initialized (COMMAND-uz0Uu)" — verified live against
prod Zitadel during the 2026-05-13 signup-mail incident.

Workaround: treat the signup form itself as the verification step. The
user proved control of the email AND set their own password, so the
account is created with ``isEmailVerified=True`` and transitions
straight to ACTIVE. No mail flow needed for signup; admin-invite still
uses send_invite_code (different lifecycle).
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
    mz.create_human_user = AsyncMock(return_value={"userId": "zit-user-001"})
    mz.grant_user_role = AsyncMock()


def _mock_portal_org(morg: MagicMock) -> MagicMock:
    inst = MagicMock()
    inst.id = 1
    inst.slug = "bedrijf-bv"
    inst.plan = "chat"
    morg.return_value = inst
    return inst


class TestSignupCreatesActiveUser:
    @pytest.mark.asyncio
    async def test_create_human_user_is_email_verified_true(self) -> None:
        """is_email_verified=True is the contract — without it Zitadel parks
        the user in USER_STATE_INITIAL and ANY subsequent login/email API
        call fails with COMMAND-uz0Uu.
        """
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

        kw = mz.create_human_user.call_args.kwargs
        assert kw.get("is_email_verified") is True, (
            "signup MUST pass is_email_verified=True — Zitadel /v2/users/.../email/send "
            "rejects INITIAL users, so without immediate-verify the account is unusable."
        )

    @pytest.mark.asyncio
    async def test_create_human_user_send_codes_false(self) -> None:
        """send_codes=False keeps Zitadel from firing the stock InitCode
        event (which klai-mailer drops per SPEC-MAILER-DROP-INITCODE-001).
        Pure log-noise reduction — but pinned to prevent future regressions.
        """
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

        kw = mz.create_human_user.call_args.kwargs
        assert kw.get("send_codes") is False
