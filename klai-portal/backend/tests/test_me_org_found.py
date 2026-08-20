"""
Tests for the MeResponse contract served by GET /api/me.

``org_found`` (SPEC-AUTH-006 R1) is True when a portal_users row exists for
the authenticated user and False when no row exists. ``can_create_org_kbs``
is the org-KB creation gate the knowledge wizard reads.

SPEC-PORTAL-RBAC-REFACTOR-001 Phase 1: /api/me now resolves the caller via
``app.core.permissions.resolve_user_permissions`` first, then re-fetches
portal_user / org for the display-name cache. Tests patch the resolver +
the row lookup to drive the two branches.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.me import MeResponse
from app.core.permissions import ProfileRole, UserPermissions
from app.core.profiles import Capability, effective_kb_limits


def _mock_org() -> MagicMock:
    org = MagicMock()
    org.id = 42
    org.slug = "acme"
    # SPEC-SEC-IDENTITY-ASSERT-002 REQ-5: /api/me now sources org_id
    # from portal_orgs.zitadel_org_id (DB) instead of the JWT claim.
    org.zitadel_org_id = "zitadel-org-acme"
    org.provisioning_status = "ready"
    org.mfa_policy = "optional"
    org.moneybird_contact_id = "mb-1"
    return org


def _mock_portal_user() -> MagicMock:
    user = MagicMock()
    user.role = "company"
    user.preferred_language = "nl"
    user.display_name = "Test User"
    user.email = "test@acme.nl"
    return user


def _mock_perms(
    *,
    org_id: int = 42,
    role: ProfileRole = ProfileRole.COMPANY,
    plan: str = "knowledge",
) -> UserPermissions:
    return UserPermissions(
        user_id="user-test",
        org_id=org_id,
        org_slug="acme",
        role=role,
        plan=plan,
        platform_unlocked_features=frozenset(),
        effective_role=role,
        effective_capabilities=frozenset({Capability.KB_CONNECTORS}),
        effective_products=frozenset({"chat", "knowledge"}),
        # The resolver always derives this from (role, plan); /api/me now
        # surfaces its ``can_create_org_kbs`` flag, so a None here would be an
        # unrealistic fixture that hides the field under test.
        effective_kb_limits=effective_kb_limits(role.value, plan),
        is_platform_admin=False,
        provisioning_status="ready",
    )


async def _call_me(perms: UserPermissions | None) -> MeResponse:
    """Invoke ``/api/me`` against a fully mocked org + portal_user row."""
    from app.api.me import me

    org = _mock_org()
    mock_row = MagicMock()
    mock_row.__iter__ = lambda self: iter((org, _mock_portal_user()))

    mock_result = MagicMock()
    mock_result.one_or_none.return_value = mock_row

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()

    mock_credentials = MagicMock()
    mock_credentials.credentials = "test-token"

    with (
        patch("app.api.me.zitadel") as mock_zitadel,
        patch("app.api.me.resolve_user_permissions", new=AsyncMock(return_value=perms)),
        patch("app.api.me.set_tenant", new_callable=AsyncMock),
    ):
        mock_zitadel.get_userinfo = AsyncMock(
            return_value={"sub": "user-123", "email": "test@acme.nl", "name": "Test User"}
        )
        mock_zitadel.has_any_mfa = AsyncMock(return_value=False)
        return await me(credentials=mock_credentials, db=mock_db)


class TestMeOrgFound:
    """org_found field must be True when a portal_users row exists, False otherwise."""

    @pytest.mark.asyncio
    async def test_org_found_true_when_portal_user_exists(self) -> None:
        """When portal_users row exists for zitadel_user_id, org_found should be True."""
        from app.api.me import me

        org = _mock_org()
        portal_user = _mock_portal_user()
        perms = _mock_perms(org_id=org.id)

        mock_row = MagicMock()
        mock_row.__iter__ = lambda self: iter((org, portal_user))

        mock_result = MagicMock()
        mock_result.one_or_none.return_value = mock_row

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()

        mock_credentials = MagicMock()
        mock_credentials.credentials = "test-token"

        userinfo = {
            "sub": "user-123",
            "email": "test@acme.nl",
            "name": "Test User",
        }

        with (
            patch("app.api.me.zitadel") as mock_zitadel,
            patch("app.api.me.resolve_user_permissions", new=AsyncMock(return_value=perms)),
            patch("app.api.me.set_tenant", new_callable=AsyncMock),
        ):
            mock_zitadel.get_userinfo = AsyncMock(return_value=userinfo)
            mock_zitadel.has_any_mfa = AsyncMock(return_value=False)

            response = await me(credentials=mock_credentials, db=mock_db)

        assert response.org_found is True
        assert response.requires_2fa_setup is False

    @pytest.mark.asyncio
    async def test_required_mfa_without_enrolment_sets_setup_flag(self) -> None:
        """Required org MFA should tell the SPA to keep the user in the setup flow."""
        from app.api.me import me

        org = _mock_org()
        org.mfa_policy = "required"
        portal_user = _mock_portal_user()
        perms = _mock_perms(org_id=org.id)

        mock_row = MagicMock()
        mock_row.__iter__ = lambda self: iter((org, portal_user))

        mock_result = MagicMock()
        mock_result.one_or_none.return_value = mock_row

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()

        mock_credentials = MagicMock()
        mock_credentials.credentials = "test-token"

        userinfo = {
            "sub": "user-123",
            "email": "test@acme.nl",
            "name": "Test User",
        }

        with (
            patch("app.api.me.zitadel") as mock_zitadel,
            patch("app.api.me.resolve_user_permissions", new=AsyncMock(return_value=perms)),
            patch("app.api.me.set_tenant", new_callable=AsyncMock),
        ):
            mock_zitadel.get_userinfo = AsyncMock(return_value=userinfo)
            mock_zitadel.has_any_mfa = AsyncMock(return_value=False)

            response = await me(credentials=mock_credentials, db=mock_db)

        assert response.mfa_policy == "required"
        assert response.mfa_enrolled is False
        assert response.requires_2fa_setup is True

    @pytest.mark.asyncio
    async def test_org_found_false_when_no_portal_user(self) -> None:
        """When no portal_users row exists for zitadel_user_id, org_found should be False."""
        from app.api.me import me

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock())

        mock_credentials = MagicMock()
        mock_credentials.credentials = "test-token"

        userinfo = {
            "sub": "user-456",
            "email": "nobody@example.com",
            "name": "Nobody",
        }

        with (
            patch("app.api.me.zitadel") as mock_zitadel,
            patch("app.api.me.resolve_user_permissions", new=AsyncMock(return_value=None)),
        ):
            mock_zitadel.get_userinfo = AsyncMock(return_value=userinfo)
            mock_zitadel.has_any_mfa = AsyncMock(return_value=False)

            response = await me(credentials=mock_credentials, db=mock_db)

        assert response.org_found is False

    @pytest.mark.asyncio
    async def test_set_tenant_called_after_org_resolved(self) -> None:
        """`set_tenant(db, org.id)` MUST be called once a portal_users row resolves.

        Regression for the 2026-05-06 portal_users 500-incident:
        portal_users WITH CHECK is strict — the display_name/email cache
        update at the end of the org-found block (`db.commit()`) requires
        the GUC to be set. Without `set_tenant` the commit raises 42501.

        See `reports/audit-tenant-isolation-2026-05-05/spec-ti-003-incident/`.
        """
        from app.api.me import me

        org = _mock_org()
        portal_user = _mock_portal_user()
        perms = _mock_perms(org_id=org.id)

        mock_row = MagicMock()
        mock_row.__iter__ = lambda self: iter((org, portal_user))

        mock_result = MagicMock()
        mock_result.one_or_none.return_value = mock_row

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()

        mock_credentials = MagicMock()
        mock_credentials.credentials = "test-token"

        userinfo = {"sub": "user-789", "email": "test@acme.nl", "name": "Test User"}

        with (
            patch("app.api.me.zitadel") as mock_zitadel,
            patch("app.api.me.resolve_user_permissions", new=AsyncMock(return_value=perms)),
            patch("app.api.me.set_tenant", new_callable=AsyncMock) as mock_set_tenant,
        ):
            mock_zitadel.get_userinfo = AsyncMock(return_value=userinfo)
            mock_zitadel.has_any_mfa = AsyncMock(return_value=False)

            await me(credentials=mock_credentials, db=mock_db)

        mock_set_tenant.assert_called_once_with(mock_db, 42)

    @pytest.mark.asyncio
    async def test_set_tenant_NOT_called_when_org_not_found(self) -> None:
        """`set_tenant` MUST NOT be called when no portal_users row exists.

        Without an org we have no tenant context to bind. Calling set_tenant
        with a None / 0 id would either fail or silently bind the wrong
        tenant — either way wrong.
        """
        from app.api.me import me

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock())

        mock_credentials = MagicMock()
        mock_credentials.credentials = "test-token"

        userinfo = {"sub": "user-456", "email": "nobody@example.com", "name": "Nobody"}

        with (
            patch("app.api.me.zitadel") as mock_zitadel,
            patch("app.api.me.resolve_user_permissions", new=AsyncMock(return_value=None)),
            patch("app.api.me.set_tenant", new_callable=AsyncMock) as mock_set_tenant,
        ):
            mock_zitadel.get_userinfo = AsyncMock(return_value=userinfo)
            mock_zitadel.has_any_mfa = AsyncMock(return_value=False)

            await me(credentials=mock_credentials, db=mock_db)

        mock_set_tenant.assert_not_called()

    @pytest.mark.asyncio
    async def test_org_found_in_response_model(self) -> None:
        """MeResponse model should include org_found field with default False."""
        from app.api.me import MeResponse

        # Default should be False
        resp = MeResponse(user_id="u1", email="a@b.com", name="A")
        assert resp.org_found is False

        # Can be set to True
        resp2 = MeResponse(user_id="u1", email="a@b.com", name="A", org_found=True)
        assert resp2.org_found is True


class TestMeOrgKbGate:
    """``can_create_org_kbs`` must mirror what ``POST /api/app/knowledge-bases`` enforces.

    Regression for the 2026-08-20 report "Admin kan geen nieuwe knowledgebase
    aanmaken" (org Nerds, ``POST /api/app/knowledge-bases`` -> 403 in 4.9ms).
    ``assert_can_create_org_kb`` denies on ``profile AND plan``, but /api/me
    exposed no such field, so the wizard guessed with ``kb.connectors`` — a
    seat-derived capability that ``hasCapability`` reports as true for every
    admin. The wizard therefore offered (and defaulted to) an org-scoped KB
    that the backend would always refuse.
    """

    @pytest.mark.asyncio
    async def test_true_for_admin_on_the_knowledge_plan(self) -> None:
        response = await _call_me(_mock_perms(role=ProfileRole.ADMIN, plan="knowledge"))

        assert response.can_create_org_kbs is True

    @pytest.mark.asyncio
    async def test_false_for_admin_on_a_plan_that_forbids_org_kbs(self) -> None:
        """The reported shape: admin role, but the org plan lowers the gate."""
        response = await _call_me(_mock_perms(role=ProfileRole.ADMIN, plan="chat"))

        assert response.can_create_org_kbs is False

    @pytest.mark.asyncio
    async def test_false_for_a_role_below_kb_manager(self) -> None:
        response = await _call_me(_mock_perms(role=ProfileRole.COMPANY, plan="knowledge"))

        assert response.can_create_org_kbs is False

    @pytest.mark.asyncio
    async def test_matches_the_create_endpoint_gate(self) -> None:
        """Same source of truth as ``assert_can_create_org_kb`` — no second opinion."""
        from fastapi import HTTPException

        from app.services.kb_quota import assert_can_create_org_kb

        for role, plan in (
            (ProfileRole.ADMIN, "knowledge"),
            (ProfileRole.ADMIN, "chat"),
            (ProfileRole.KB_MANAGER, "knowledge"),
            (ProfileRole.COMPANY, "knowledge"),
            (ProfileRole.PERSONAL, "chat"),
        ):
            response = await _call_me(_mock_perms(role=role, plan=plan))

            org = MagicMock()
            org.plan = plan
            try:
                await assert_can_create_org_kb(org=org, role=role.value)
                endpoint_allows = True
            except HTTPException as exc:
                assert exc.status_code == 403
                assert exc.detail["error_code"] == "kb_quota_org_kb_not_allowed"
                endpoint_allows = False

            assert response.can_create_org_kbs is endpoint_allows, f"{role.value} on {plan}"

    @pytest.mark.asyncio
    async def test_defaults_to_false_without_a_portal_row(self) -> None:
        """No org resolved means no org-KB rights — fail closed."""
        response = await _call_me(None)

        assert response.can_create_org_kbs is False
        assert response.max_personal_kbs_per_user == 0

    @pytest.mark.asyncio
    async def test_exposes_the_personal_kb_cap(self) -> None:
        """``null`` means unlimited; a number is the cap kb_quota enforces.

        The wizard's personal-scope fallback reads this instead of guessing
        from ``kb.connectors``, which is true for every admin regardless of
        the cap their plan actually imposes. The two cases below are exactly
        that trap: same admin role, opposite caps.
        """
        capped = await _call_me(_mock_perms(role=ProfileRole.ADMIN, plan="chat"))
        assert capped.max_personal_kbs_per_user == 5

        unlimited = await _call_me(_mock_perms(role=ProfileRole.ADMIN, plan="knowledge"))
        assert unlimited.max_personal_kbs_per_user is None
