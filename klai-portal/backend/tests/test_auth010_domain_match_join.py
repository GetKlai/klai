"""SPEC-AUTH-010 — domain-match join in the signup flows.

Covers:
- R1/R2: social signup domain-match branch + /signup/social/join
- R3: idp_callback provisions brand-new IdP users (no more silent bounce)
- R4: two-phase password signup (domain_match choice / join_pending)
- R5: auto_accept_same_domain founder checkbox (server-side guarded)
- R6.3: email-link join-request approval sets tenant context (RLS)
- R7: password login routes zero-membership users to the picker
- R8: pending-session response exposes kind + auto_accept
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from helpers import make_request

_FERNET_KEY = "R1c1-s96uO9Yz7k1E0kN6qz52gzd9PwNbAeZaks_PIc="  # nosec — test placeholder
_DOMAIN = "getklai.com"


def _encrypt_pending(email: str = "piet@bedrijf.nl") -> str:
    payload = json.dumps(
        {
            "session_id": "sid-1",
            "session_token": "stk-1",
            "zitadel_user_id": "zuser-2",
            "email": email,
            "has_valid_invite": False,
            "ua_hash": "",
            "ip_subnet": "127.0.0.0",
        }
    ).encode()
    return Fernet(_FERNET_KEY.encode()).encrypt(payload).decode()


def _org(org_id: int = 1, name: str = "Bedrijf BV", auto_accept: bool = False) -> MagicMock:
    org = MagicMock()
    org.id = org_id
    org.name = name
    org.slug = "bedrijf-bv"
    org.primary_domain = "bedrijf.nl"
    org.auto_accept_same_domain = auto_accept
    return org


def _signup_body(**overrides):
    from app.api.signup import SignupRequest

    base = {
        "first_name": "Piet",
        "last_name": "Peters",
        "email": "piet@bedrijf.nl",
        "password": "Correct horse battery staple 2026!",
        "company_name": "Bedrijf BV",
        "preferred_language": "nl",
    }
    base.update(overrides)
    return SignupRequest(**base)


# ---------------------------------------------------------------------------
# R4: two-phase password signup
# ---------------------------------------------------------------------------


class TestPasswordSignupDomainMatch:
    @pytest.mark.asyncio
    async def test_phase1_returns_domain_match_without_side_effects(self) -> None:
        """C4.1: boolean-only disclosure — no names, no Zitadel/DB writes."""
        from app.api.signup import signup

        db = AsyncMock()
        with (
            patch("app.api.signup.zitadel") as mz,
            patch("app.api.signup.find_domain_match_orgs", AsyncMock(return_value=[_org()])),
            patch("app.api.signup.assert_zitadel_password_policy_compatible", AsyncMock()),
            patch("app.api.signup.check_signup_email_rate_limit", AsyncMock(return_value=True)),
        ):
            mz.create_org = AsyncMock()
            mz.create_human_user_v2_with_verify = AsyncMock()
            result = await signup(body=_signup_body(), background_tasks=MagicMock(), db=db)

        assert result.kind == "domain_match"
        assert result.domain == "bedrijf.nl"
        assert "Bedrijf BV" not in (result.message or "")
        mz.create_org.assert_not_awaited()
        mz.create_human_user_v2_with_verify.assert_not_awaited()
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_join_choice_creates_user_but_no_org(self) -> None:
        from app.api.signup import signup

        db = AsyncMock()
        with (
            patch("app.api.signup.zitadel") as mz,
            patch("app.api.signup.find_domain_match_orgs", AsyncMock(return_value=[_org()])),
            patch("app.api.signup.assert_zitadel_password_policy_compatible", AsyncMock()),
            patch("app.api.signup.check_signup_email_rate_limit", AsyncMock(return_value=True)),
            patch("app.api.signup.emit_event"),
        ):
            mz.create_org = AsyncMock()
            mz.create_human_user_v2_with_verify = AsyncMock(return_value={"userId": "zu-join-1"})
            result = await signup(body=_signup_body(domain_choice="join"), background_tasks=MagicMock(), db=db)

        assert result.kind == "join_pending"
        assert result.user_id == "zu-join-1"
        mz.create_human_user_v2_with_verify.assert_awaited_once()
        mz.create_org.assert_not_awaited()
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_choice_skips_domain_match(self) -> None:
        """C1.3-equivalent escape hatch: explicit create ignores matches."""
        from app.api.signup import signup

        db = AsyncMock()
        db.add = MagicMock()
        org_row = MagicMock()
        org_row.id = 7
        org_row.slug = "bedrijf-bv-zit"
        org_row.plan = "knowledge"
        find_mock = AsyncMock(return_value=[_org()])
        with (
            patch("app.api.signup.zitadel") as mz,
            patch("app.api.signup.find_domain_match_orgs", find_mock),
            patch("app.api.signup.assert_zitadel_password_policy_compatible", AsyncMock()),
            patch("app.api.signup.check_signup_email_rate_limit", AsyncMock(return_value=True)),
            patch("app.api.signup.PortalOrg", return_value=org_row),
            patch("app.api.signup.PortalUser"),
            patch("app.api.signup.set_tenant", AsyncMock()),
            patch("app.api.signup.validate_slug_for_provisioning"),
            patch("app.api.signup.invalidate_tenant_slug_cache"),
            patch("app.api.signup.provision_tenant"),
            patch("app.api.signup.emit_event"),
        ):
            mz.create_org = AsyncMock(return_value={"id": "zit-org-7"})
            mz.create_human_user_v2_with_verify = AsyncMock(return_value={"userId": "zu-7"})
            mz.grant_user_role = AsyncMock()
            result = await signup(
                body=_signup_body(domain_choice="create"),
                background_tasks=MagicMock(),
                db=db,
            )

        assert result.kind == "created"
        find_mock.assert_not_awaited()
        mz.create_org.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_auto_accept_checkbox_reaches_org_row(self) -> None:
        """R5: founder checkbox lands on the PortalOrg row (business domain)."""
        from app.api.signup import signup

        db = AsyncMock()
        db.add = MagicMock()
        org_row = MagicMock()
        org_row.id = 8
        org_row.slug = "bedrijf-bv-zit"
        org_row.plan = "knowledge"
        with (
            patch("app.api.signup.zitadel") as mz,
            patch("app.api.signup.find_domain_match_orgs", AsyncMock(return_value=[])),
            patch("app.api.signup.assert_zitadel_password_policy_compatible", AsyncMock()),
            patch("app.api.signup.check_signup_email_rate_limit", AsyncMock(return_value=True)),
            patch("app.api.signup.PortalOrg", return_value=org_row) as mock_org_cls,
            patch("app.api.signup.PortalUser"),
            patch("app.api.signup.set_tenant", AsyncMock()),
            patch("app.api.signup.validate_slug_for_provisioning"),
            patch("app.api.signup.invalidate_tenant_slug_cache"),
            patch("app.api.signup.provision_tenant"),
            patch("app.api.signup.emit_event"),
        ):
            mz.create_org = AsyncMock(return_value={"id": "zit-org-8"})
            mz.create_human_user_v2_with_verify = AsyncMock(return_value={"userId": "zu-8"})
            mz.grant_user_role = AsyncMock()
            result = await signup(
                body=_signup_body(auto_accept_same_domain=True),
                background_tasks=MagicMock(),
                db=db,
            )

        assert result.kind == "created"
        assert mock_org_cls.call_args.kwargs["auto_accept_same_domain"] is True
        assert mock_org_cls.call_args.kwargs["primary_domain"] == "bedrijf.nl"


# ---------------------------------------------------------------------------
# R1/R2: social signup domain-match + join
# ---------------------------------------------------------------------------


class TestSocialSignupDomainMatch:
    @pytest.mark.asyncio
    async def test_domain_match_lists_orgs_and_keeps_cookie(self) -> None:
        from app.api.signup import SocialSignupRequest, signup_social

        db = AsyncMock()
        response = MagicMock()
        with (
            patch("app.api.signup.settings") as ms,
            patch("app.api.signup.zitadel") as mz,
            patch("app.api.signup._get_fernet", return_value=Fernet(_FERNET_KEY.encode())),
            patch(
                "app.api.signup.find_domain_match_orgs",
                AsyncMock(return_value=[_org(auto_accept=True), _org(org_id=2, name="Pinger", auto_accept=False)]),
            ),
        ):
            ms.domain = _DOMAIN
            ms.sso_cookie_key = _FERNET_KEY
            mz.create_org = AsyncMock()
            result = await signup_social(
                body=SocialSignupRequest(company_name="Bedrijf BV"),
                response=response,
                background_tasks=MagicMock(),
                request=make_request(),
                db=db,
                klai_idp_pending=_encrypt_pending(),
            )

        assert result.kind == "domain_match"
        assert result.domain == "bedrijf.nl"
        assert [o.name for o in result.orgs] == ["Bedrijf BV", "Pinger"]
        assert [o.auto_accept for o in result.orgs] == [True, False]
        mz.create_org.assert_not_awaited()
        # C1.5: pending cookie survives so the user can still join or retry.
        response.delete_cookie.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_new_workspace_escape_hatch(self) -> None:
        from app.api.signup import SocialSignupRequest, signup_social

        db = AsyncMock()
        db.add = MagicMock()
        org_row = MagicMock()
        org_row.id = 9
        org_row.slug = "bedrijf-bv-zit"
        org_row.plan = "knowledge"
        find_mock = AsyncMock(return_value=[_org()])
        with (
            patch("app.api.signup.settings") as ms,
            patch("app.api.signup.zitadel") as mz,
            patch("app.api.signup._get_fernet", return_value=Fernet(_FERNET_KEY.encode())),
            patch("app.api.signup.find_domain_match_orgs", find_mock),
            patch("app.api.signup.PortalOrg", return_value=org_row),
            patch("app.api.signup.PortalUser"),
            patch("app.api.signup.set_tenant", AsyncMock()),
            patch("app.api.signup.validate_slug_for_provisioning"),
            patch("app.api.signup.invalidate_tenant_slug_cache"),
            patch("app.api.signup.provision_tenant"),
            patch("app.api.signup.emit_event"),
        ):
            ms.domain = _DOMAIN
            ms.sso_cookie_key = _FERNET_KEY
            ms.sso_cookie_max_age = 3600
            ms.zitadel_portal_org_id = "portal-org"
            mz.create_org = AsyncMock(return_value={"id": "zit-org-9"})
            mz.grant_user_role = AsyncMock()
            result = await signup_social(
                body=SocialSignupRequest(company_name="Bedrijf BV", create_new_workspace=True),
                response=MagicMock(),
                background_tasks=MagicMock(),
                request=make_request(),
                db=db,
                klai_idp_pending=_encrypt_pending(),
            )

        assert result.kind == "created"
        find_mock.assert_not_awaited()
        mz.create_org.assert_awaited_once()


class TestSocialJoin:
    @pytest.mark.asyncio
    async def test_auto_accept_inserts_personal_user_and_logs_in(self) -> None:
        from app.api.signup import SocialJoinRequestBody, signup_social_join
        from app.models.portal import PortalUser

        db = AsyncMock()
        added = []
        db.add = lambda obj: added.append(obj)
        response = MagicMock()
        with (
            patch("app.api.signup.settings") as ms,
            patch("app.api.signup._get_fernet", return_value=Fernet(_FERNET_KEY.encode())),
            patch("app.api.signup.find_domain_match_orgs", AsyncMock(return_value=[_org(auto_accept=True)])),
            patch("app.api.signup.set_tenant", AsyncMock()) as mock_set_tenant,
            patch("app.api.auth_select.notify_auto_join_admins", AsyncMock()),
            patch("app.services.listmonk.sync_portal_user_best_effort", AsyncMock()),
            patch("app.api.signup.emit_event"),
        ):
            ms.domain = _DOMAIN
            ms.sso_cookie_key = _FERNET_KEY
            ms.sso_cookie_max_age = 3600
            result = await signup_social_join(
                body=SocialJoinRequestBody(org_id=1),
                response=response,
                request=make_request(),
                db=db,
                klai_idp_pending=_encrypt_pending(),
            )

        assert result.kind == "auto_join"
        rows = [r for r in added if isinstance(r, PortalUser)]
        assert len(rows) == 1
        assert rows[0].role == "personal"
        assert rows[0].seat_type == "chat"
        assert rows[0].status == "active"
        mock_set_tenant.assert_awaited()
        cookie_keys = [c.kwargs.get("key") for c in response.set_cookie.call_args_list]
        assert "klai_sso" in cookie_keys
        response.delete_cookie.assert_called_once()
        db.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_no_auto_accept_creates_join_request(self) -> None:
        from app.api.signup import SocialJoinRequestBody, signup_social_join
        from app.models.portal import PortalJoinRequest, PortalUser

        db = AsyncMock()
        added = []
        db.add = lambda obj: added.append(obj)
        response = MagicMock()
        # db.execute call order: (1) duplicate-pending check → none,
        # (2) admin recipients query → one admin with an email.
        dup_result = MagicMock()
        dup_result.scalar_one_or_none = MagicMock(return_value=None)
        admin = MagicMock()
        admin.email = "admin@bedrijf.nl"
        admins_result = MagicMock()
        admins_result.scalars.return_value.all.return_value = [admin]
        db.execute = AsyncMock(side_effect=[dup_result, admins_result])
        notify_mock = AsyncMock()
        with (
            patch("app.api.signup.settings") as ms,
            patch("app.api.signup._get_fernet", return_value=Fernet(_FERNET_KEY.encode())),
            patch("app.api.signup.find_domain_match_orgs", AsyncMock(return_value=[_org(auto_accept=False)])),
            patch("app.api.signup.set_tenant", AsyncMock()),
            patch("app.api.signup.generate_approval_token", return_value="approval-tok"),
            patch("app.services.notifications.notify_admin_join_request", notify_mock),
        ):
            ms.domain = _DOMAIN
            ms.sso_cookie_key = _FERNET_KEY
            result = await signup_social_join(
                body=SocialJoinRequestBody(org_id=1),
                response=response,
                request=make_request(),
                db=db,
                klai_idp_pending=_encrypt_pending(),
            )

        assert result.kind == "join_request_pending"
        assert result.redirect_to == "/join-request/sent"
        jr_rows = [r for r in added if isinstance(r, PortalJoinRequest)]
        assert len(jr_rows) == 1
        assert jr_rows[0].org_id == 1
        assert [r for r in added if isinstance(r, PortalUser)] == []
        cookie_keys = [c.kwargs.get("key") for c in response.set_cookie.call_args_list]
        assert "klai_sso" not in cookie_keys
        # L5: the R2 notification requirement is actually exercised.
        notify_mock.assert_awaited_once()
        assert notify_mock.call_args.kwargs["admin_email"] == "admin@bedrijf.nl"

    @pytest.mark.asyncio
    async def test_duplicate_pending_join_request_is_idempotent(self) -> None:
        """M2: replaying the pending cookie must not stack rows or re-notify."""
        from app.api.signup import SocialJoinRequestBody, signup_social_join
        from app.models.portal import PortalJoinRequest

        db = AsyncMock()
        added = []
        db.add = lambda obj: added.append(obj)
        dup_result = MagicMock()
        dup_result.scalar_one_or_none = MagicMock(return_value=MagicMock())  # existing pending row
        db.execute = AsyncMock(return_value=dup_result)
        notify_mock = AsyncMock()
        with (
            patch("app.api.signup.settings") as ms,
            patch("app.api.signup._get_fernet", return_value=Fernet(_FERNET_KEY.encode())),
            patch("app.api.signup.find_domain_match_orgs", AsyncMock(return_value=[_org(auto_accept=False)])),
            patch("app.api.signup.set_tenant", AsyncMock()),
            patch("app.services.notifications.notify_admin_join_request", notify_mock),
        ):
            ms.domain = _DOMAIN
            ms.sso_cookie_key = _FERNET_KEY
            result = await signup_social_join(
                body=SocialJoinRequestBody(org_id=1),
                response=MagicMock(),
                request=make_request(),
                db=db,
                klai_idp_pending=_encrypt_pending(),
            )

        assert result.kind == "join_request_pending"
        assert [r for r in added if isinstance(r, PortalJoinRequest)] == []
        notify_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_org_not_in_domain_matches_returns_403(self) -> None:
        from fastapi import HTTPException

        from app.api.signup import SocialJoinRequestBody, signup_social_join

        db = AsyncMock()
        with (
            patch("app.api.signup.settings") as ms,
            patch("app.api.signup._get_fernet", return_value=Fernet(_FERNET_KEY.encode())),
            patch("app.api.signup.find_domain_match_orgs", AsyncMock(return_value=[_org(org_id=1)])),
        ):
            ms.sso_cookie_key = _FERNET_KEY
            with pytest.raises(HTTPException) as exc:
                await signup_social_join(
                    body=SocialJoinRequestBody(org_id=999),
                    response=MagicMock(),
                    request=make_request(),
                    db=db,
                    klai_idp_pending=_encrypt_pending(),
                )
        assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# R7: password login routes zero-membership users to the picker
# ---------------------------------------------------------------------------


class TestLoginDomainMatchRouting:
    def _member_result(self, rows: list) -> MagicMock:
        mr = MagicMock()
        mr.scalars.return_value.all.return_value = rows
        return mr

    @pytest.mark.asyncio
    async def test_zero_members_verified_match_returns_ref(self) -> None:
        from app.api.auth import _maybe_select_workspace_ref

        db = AsyncMock()
        db.execute = AsyncMock(return_value=self._member_result([]))
        store_mock = AsyncMock(return_value="ref-abc")
        svc_cls = MagicMock()
        svc_cls.return_value.store = store_mock
        with (
            patch("app.services.domain_match.find_domain_match_orgs", AsyncMock(return_value=[_org(auto_accept=True)])),
            patch("app.api.auth._is_email_already_verified", AsyncMock(return_value=True)),
            patch("app.services.pending_session.PendingSessionService", svc_cls),
        ):
            ref = await _maybe_select_workspace_ref(
                zitadel_user_id="zu-1",
                email="piet@bedrijf.nl",
                auth_request_id="ar-1",
                session_id="sid",
                session_token="stk",
                db=db,
            )

        assert ref == "ref-abc"
        entries = store_mock.call_args.kwargs["entries"]
        assert entries[0]["kind"] == "domain_match"
        assert entries[0]["auto_accept"] is True

    @pytest.mark.asyncio
    async def test_existing_member_returns_none(self) -> None:
        from app.api.auth import _maybe_select_workspace_ref

        db = AsyncMock()
        db.execute = AsyncMock(return_value=self._member_result([MagicMock()]))
        with patch("app.services.domain_match.find_domain_match_orgs", AsyncMock(return_value=[_org()])):
            ref = await _maybe_select_workspace_ref(
                zitadel_user_id="zu-1",
                email="piet@bedrijf.nl",
                auth_request_id="ar-1",
                session_id="sid",
                session_token="stk",
                db=db,
            )
        assert ref is None

    @pytest.mark.asyncio
    async def test_unverified_email_returns_none(self) -> None:
        """C7.3: no join offer before the mail link is clicked."""
        from app.api.auth import _maybe_select_workspace_ref

        db = AsyncMock()
        db.execute = AsyncMock(return_value=self._member_result([]))
        with (
            patch("app.services.domain_match.find_domain_match_orgs", AsyncMock(return_value=[_org()])),
            patch("app.api.auth._is_email_already_verified", AsyncMock(return_value=False)),
        ):
            ref = await _maybe_select_workspace_ref(
                zitadel_user_id="zu-1",
                email="piet@bedrijf.nl",
                auth_request_id="ar-1",
                session_id="sid",
                session_token="stk",
                db=db,
            )
        assert ref is None

    @pytest.mark.asyncio
    async def test_store_failure_returns_none(self) -> None:
        """C7.4: Redis trouble degrades to the pre-SPEC login flow."""
        from app.api.auth import _maybe_select_workspace_ref

        db = AsyncMock()
        db.execute = AsyncMock(return_value=self._member_result([]))
        svc_cls = MagicMock()
        svc_cls.return_value.store = AsyncMock(side_effect=RuntimeError("redis down"))
        with (
            patch("app.services.domain_match.find_domain_match_orgs", AsyncMock(return_value=[_org()])),
            patch("app.api.auth._is_email_already_verified", AsyncMock(return_value=True)),
            patch("app.services.pending_session.PendingSessionService", svc_cls),
        ):
            ref = await _maybe_select_workspace_ref(
                zitadel_user_id="zu-1",
                email="piet@bedrijf.nl",
                auth_request_id="ar-1",
                session_id="sid",
                session_token="stk",
                db=db,
            )
        assert ref is None


# ---------------------------------------------------------------------------
# R3: idp_callback provisions brand-new IdP users
# ---------------------------------------------------------------------------


class TestIdpCallbackNewUser:
    def _db_with(self, members: list, domain_orgs: list) -> AsyncMock:
        mr = MagicMock()
        mr.scalars.return_value.all.return_value = members
        dr = MagicMock()
        dr.scalars.return_value.all.return_value = domain_orgs
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[mr, dr])
        return db

    @pytest.mark.asyncio
    async def test_new_idp_user_is_provisioned_and_reaches_picker(self) -> None:
        from app.api.auth import idp_callback

        zit = MagicMock()
        zit.retrieve_idp_intent = AsyncMock(return_value={"idpInformation": {}})  # no userId
        zit.create_zitadel_user_from_idp = AsyncMock(return_value="znew-1")
        zit.create_session_for_user_idp = AsyncMock(return_value={"sessionId": "sid", "sessionToken": "stk"})
        zit.get_session_details = AsyncMock(return_value={"zitadel_user_id": "znew-1", "email": "piet@bedrijf.nl"})
        db = self._db_with(members=[], domain_orgs=[_org()])
        svc = MagicMock()
        svc.return_value.store = AsyncMock(return_value="ref-new")
        with (
            patch("app.api.auth.zitadel", zit),
            patch("app.api.auth.emit_event"),
            patch("app.api.auth.audit.log_event", AsyncMock()),
            patch("app.api.auth.PendingSessionService", svc),
        ):
            response = await idp_callback(
                id="intent-9",
                token="tok-9",
                auth_request_id="ar-9",
                request=make_request(),
                db=db,
            )

        zit.create_zitadel_user_from_idp.assert_awaited_once()
        zit.create_session_for_user_idp.assert_awaited_once_with("znew-1", "intent-9", "tok-9")
        assert response.status_code == 302
        assert response.headers.get("location") == "/select-workspace?ref=ref-new"

    @pytest.mark.asyncio
    async def test_new_idp_user_create_failure_redirects_to_login(self) -> None:
        from app.api.auth import idp_callback

        zit = MagicMock()
        zit.retrieve_idp_intent = AsyncMock(return_value={})
        zit.create_zitadel_user_from_idp = AsyncMock(side_effect=RuntimeError("boom"))
        with (
            patch("app.api.auth.zitadel", zit),
            patch("app.api.auth.emit_event"),
        ):
            response = await idp_callback(
                id="intent-9",
                token="tok-9",
                auth_request_id="ar-9",
                request=make_request(),
                db=AsyncMock(),
            )

        assert response.status_code == 302
        assert response.headers.get("location") == "/login?authRequest=ar-9"


class TestIdpSessionRetryHelper:
    @pytest.mark.asyncio
    async def test_network_error_returns_none_instead_of_raising(self) -> None:
        """H1: httpx.RequestError must surface as (None, …) so callers 302 to
        their failure_url instead of leaking a raw 500 mid-OIDC redirect."""
        import httpx

        from app.api.auth import _create_idp_session_with_cqrs_retry

        with patch(
            "app.api.auth.zitadel.create_session_for_user_idp",
            AsyncMock(side_effect=httpx.ConnectTimeout("boom")),
        ):
            session, attempts, last_exc = await _create_idp_session_with_cqrs_retry("u1", "i1", "t1")

        assert session is None
        assert attempts == 1
        assert isinstance(last_exc, httpx.ConnectTimeout)


class TestJoinChoiceWithoutRemainingMatch:
    @pytest.mark.asyncio
    async def test_join_choice_with_no_match_raises_409(self) -> None:
        """M1: an explicit 'join' must never silently create a workspace when
        the matched org disappeared between phase 1 and phase 2."""
        from fastapi import HTTPException

        from app.api.signup import signup

        db = AsyncMock()
        with (
            patch("app.api.signup.zitadel") as mz,
            patch("app.api.signup.find_domain_match_orgs", AsyncMock(return_value=[])),
            patch("app.api.signup.assert_zitadel_password_policy_compatible", AsyncMock()),
            patch("app.api.signup.check_signup_email_rate_limit", AsyncMock(return_value=True)),
        ):
            mz.create_org = AsyncMock()
            with pytest.raises(HTTPException) as exc:
                await signup(body=_signup_body(domain_choice="join"), background_tasks=MagicMock(), db=db)

        assert exc.value.status_code == 409
        mz.create_org.assert_not_awaited()


# ---------------------------------------------------------------------------
# R6.3: email-link approval sets tenant context before RLS-guarded writes
# ---------------------------------------------------------------------------


class TestApproveTokenPathSetsTenant:
    @pytest.mark.asyncio
    async def test_token_approval_calls_set_tenant(self) -> None:
        from app.api.admin.join_requests import approve_join_request

        jr = MagicMock()
        jr.id = 5
        jr.org_id = 42
        jr.zitadel_user_id = "zu-5"
        jr.email = "piet@bedrijf.nl"
        jr.display_name = "Piet"
        jr.expires_at = None
        db = AsyncMock()
        db.add = MagicMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=jr)))
        with (
            patch("app.api.admin.join_requests.verify_approval_token", return_value=True),
            patch("app.api.admin.join_requests.set_tenant", AsyncMock()) as mock_set_tenant,
            patch("app.api.admin.join_requests.notify_user_join_approved", AsyncMock()),
            patch("app.services.listmonk.sync_portal_user_best_effort", AsyncMock()),
        ):
            result = await approve_join_request(request_id=5, credentials=None, db=db, token="tok")

        mock_set_tenant.assert_awaited_once_with(db, 42)
        assert result.message == "Request approved"


# ---------------------------------------------------------------------------
# R8: pending-session response exposes join semantics
# ---------------------------------------------------------------------------


class TestPendingSessionExposesKind:
    @pytest.mark.asyncio
    async def test_get_pending_session_returns_kind_and_auto_accept(self) -> None:
        from app.api.auth_select import get_pending_session

        entries = [
            {"org_id": 1, "name": "Bedrijf BV", "slug": "bedrijf-bv", "kind": "domain_match", "auto_accept": True},
            {"org_id": 2, "name": "Pinger", "slug": "pinger", "kind": "member"},
        ]
        session = {"entries": entries}
        org1 = _org(1, "Bedrijf BV")
        org2 = _org(2, "Pinger")
        org2.slug = "pinger"
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [org1, org2]
        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)
        with patch("app.api.auth_select.pending_session_svc") as svc:
            svc.retrieve = AsyncMock(return_value=session)
            resp = await get_pending_session(ref="r1", db=db)

        assert [(o.kind, o.auto_accept) for o in resp.orgs] == [("domain_match", True), ("member", False)]
