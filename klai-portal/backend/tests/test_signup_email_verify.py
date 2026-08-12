"""Self-service signup must send users to Klai's verify page."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException


@pytest.fixture(autouse=True)
def _no_domain_match_by_default():
    """SPEC-AUTH-010: signup endpoints query domain-match orgs; default to none."""
    from unittest.mock import AsyncMock as _AM
    from unittest.mock import patch as _patch

    with _patch("app.api.signup.find_domain_match_orgs", _AM(return_value=[])):
        yield


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
            patch("app.api.signup.assert_zitadel_password_policy_compatible", AsyncMock()),
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

    @pytest.mark.asyncio
    async def test_signup_maps_zitadel_password_policy_400_to_user_facing_error(self) -> None:
        from app.api.signup import SignupRequest, signup

        body = SignupRequest(**_BASE)
        mock_db = AsyncMock()
        request = httpx.Request("POST", "https://zitadel.test/v2/users/human")
        response = httpx.Response(
            400,
            request=request,
            json={"message": "password does not match password complexity policy"},
        )

        with (
            patch("app.api.signup.assert_zitadel_password_policy_compatible", AsyncMock()),
            patch("app.api.signup.check_signup_email_rate_limit", AsyncMock(return_value=True)),
            patch("app.api.signup.zitadel") as mz,
        ):
            mz.create_org = AsyncMock(return_value={"id": "zit-org-001"})
            mz.create_human_user_v2_with_verify = AsyncMock(
                side_effect=httpx.HTTPStatusError("policy", request=request, response=response)
            )
            mz.delete_org = AsyncMock()

            with pytest.raises(HTTPException) as exc:
                await signup(body=body, background_tasks=MagicMock(), db=mock_db)

        assert exc.value.status_code == 400
        assert "Wachtwoord voldoet niet" in str(exc.value.detail)
        mz.delete_org.assert_awaited_once_with("zit-org-001")
