"""
Tests for the org_found field in MeResponse (SPEC-AUTH-006 R1).

Verifies that /api/me returns org_found=True when a portal_users row exists
for the authenticated user, and org_found=False when no row exists.

SPEC-PORTAL-RBAC-REFACTOR-001 Phase 1: /api/me now resolves the caller via
``app.core.permissions.resolve_user_permissions`` first, then re-fetches
portal_user / org for the display-name cache. Tests patch the resolver +
the row lookup to drive the two branches.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.permissions import ProfileRole, UserPermissions
from app.core.profiles import Capability


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


def _mock_perms(*, org_id: int = 42, role: ProfileRole = ProfileRole.COMPANY) -> UserPermissions:
    return UserPermissions(
        user_id="user-test",
        org_id=org_id,
        org_slug="acme",
        role=role,
        plan="knowledge",
        enabled_addons=frozenset(),
        platform_unlocked_features=frozenset(),
        effective_role=role,
        effective_capabilities=frozenset({Capability.KB_CONNECTORS}),
        effective_products=frozenset({"chat", "knowledge"}),
        effective_kb_limits=None,  # type: ignore[arg-type]
        is_platform_admin=False,
        provisioning_status="ready",
    )


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
