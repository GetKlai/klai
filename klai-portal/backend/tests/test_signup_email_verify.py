"""Self-service signup must fire a Klai-branded email-verification mail.

Background: Zitadel's stock InitCode event for a freshly-created user is
dropped by klai-mailer (SPEC-MAILER-DROP-INITCODE-001) in favour of Klai's
own send_invite_code / send_email_verification_code flows. Without an
explicit follow-up call after create_human_user, signup users get NO mail
and the "Confirm your email" page is a lie. These tests pin the contract:

  1. ``zitadel.create_human_user`` is called with ``send_codes=False`` so
     the Zitadel stock InitCode does not even fire.
  2. After role grant, ``zitadel.send_email_verification_code`` is called
     exactly once with the url_template built from AuthLinkRoute.VERIFY_EMAIL.
  3. If the verification mail call fails, signup returns 502 with a
     ``signup_partial_failure`` payload (mirrors invite_user partial-failure
     pattern in admin/users.py).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

_BASE = {
    "first_name": "Founder",
    "last_name": "Person",
    "email": "founder@bedrijf.nl",
    "password": "VeryStrongPassw0rd!",
    "company_name": "Bedrijf BV",
    "preferred_language": "nl",
}


def _mock_zitadel(mz: MagicMock, *, send_verify_fails: bool = False) -> None:
    mz.create_org = AsyncMock(return_value={"id": "zit-org-001"})
    mz.create_human_user = AsyncMock(return_value={"userId": "zit-user-001"})
    mz.grant_user_role = AsyncMock()
    if send_verify_fails:
        mz.send_email_verification_code = AsyncMock(side_effect=RuntimeError("zitadel boom"))
    else:
        mz.send_email_verification_code = AsyncMock()


def _mock_portal_org(morg: MagicMock) -> MagicMock:
    inst = MagicMock()
    inst.id = 1
    inst.slug = "bedrijf-bv"
    inst.plan = "chat"
    morg.return_value = inst
    return inst


class TestSignupTriggersEmailVerify:
    @pytest.mark.asyncio
    async def test_create_human_user_suppresses_initcode(self) -> None:
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
        assert kw.get("send_codes") is False, (
            "signup MUST pass send_codes=False — otherwise Zitadel fires "
            "InitCode which klai-mailer drops, and the user gets no mail."
        )

    @pytest.mark.asyncio
    async def test_send_email_verification_code_called_once(self) -> None:
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

        mz.send_email_verification_code.assert_called_once()
        call = mz.send_email_verification_code.call_args
        # Positional user_id
        assert call.args == ("zit-user-001",), f"unexpected positional args: {call.args!r}"
        # Keyword url_template lands on /verify with the three Zitadel placeholders
        url_template = call.kwargs["url_template"]
        assert url_template.startswith(settings.portal_url.rstrip("/") + "/verify")
        for placeholder in ("{{.UserID}}", "{{.Code}}", "{{.OrgID}}"):
            assert placeholder in url_template, f"missing placeholder {placeholder!r}"

    @pytest.mark.asyncio
    async def test_verification_send_failure_yields_502_partial(self) -> None:
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
            _mock_zitadel(mz, send_verify_fails=True)
            _mock_portal_org(morg)
            with pytest.raises(HTTPException) as exc_info:
                await signup(body=body, background_tasks=MagicMock(), db=AsyncMock())

        assert exc_info.value.status_code == 502
        detail = exc_info.value.detail
        assert isinstance(detail, dict)
        assert detail.get("code") == "signup_partial_failure"
        assert detail.get("user_id") == "zit-user-001"
