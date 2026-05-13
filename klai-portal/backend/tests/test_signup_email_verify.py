"""Self-service signup must send users to Klai's verify page."""

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


def _mock_portal_org(morg: MagicMock) -> MagicMock:
    inst = MagicMock()
    inst.id = 1
    inst.slug = "bedrijf-bv"
    inst.plan = "chat"
    morg.return_value = inst
    return inst


class TestSignupFiresKlaiVerifyMail:
    @pytest.mark.asyncio
    async def test_signup_uses_v2_verify_url_template(self) -> None:
        from app.api.signup import SignupRequest, signup
        from app.core.config import settings

        body = SignupRequest(**_BASE)
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
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
            mz.create_org = AsyncMock(return_value={"id": "zit-org-001"})
            mz.create_human_user_v2_with_verify = AsyncMock(return_value={"userId": "zit-user-001"})
            mz.create_human_user = AsyncMock()
            mz.grant_user_role = AsyncMock()
            _mock_portal_org(morg)

            await signup(body=body, background_tasks=MagicMock(), db=mock_db)

        mz.create_human_user_v2_with_verify.assert_called_once()
        mz.create_human_user.assert_not_called()
        kwargs = mz.create_human_user_v2_with_verify.call_args.kwargs
        assert kwargs["org_id"] == settings.zitadel_portal_org_id
        assert kwargs["url_template"].startswith(settings.portal_url.rstrip("/") + "/verify?")
        for placeholder in ("{{.UserID}}", "{{.Code}}", "{{.OrgID}}"):
            assert placeholder in kwargs["url_template"]
