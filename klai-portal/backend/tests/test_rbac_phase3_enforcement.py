"""SPEC-PORTAL-RBAC-REFACTOR-001 Phase 3 — enforcement gap tests.

Pins three classes of enforcement that close the Phase 2 architecture gates:

- REQ-6: ``personal`` effective_role MUST NOT see "org" / default_org_role
  KBs in the access list returned by ``get_accessible_kb_slugs``. The
  service-layer behaviour is already covered by ``test_profiles.py``;
  this file pins that the two API endpoints (``list_kbs_with_access``,
  ``get_knowledge_stats``) actually pass the caller's role through.
- REQ-7: ``personal`` effective_role MUST NOT write to org-owned KBs.
  Both ``_get_writable_kb_or_raise`` (URL/Text source POST) and
  ``connectors.create_connector`` MUST raise HTTP 403 with
  ``error_code=org_kb_write_requires_company``.
- REQ-12 / REQ-13: admin-side role-assignment endpoints MUST validate
  the requested role against ``ALLOWED_PROFILES_PER_PLAN[org.plan]``
  and reject with HTTP 403 ``error_code=role_not_allowed_for_plan`` when
  out-of-range. Pins all three endpoints (``invite_user``,
  ``update_user_role``, ``promote_admin``) and the ``kb_manager``
  vs ``free``/``core`` plan invariant.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.core.profiles import (
    ALLOWED_PROFILES_PER_PLAN,
    ProfileRole,
    assert_role_allowed_for_plan,
)
from tests.conftest import make_perms

# ---------------------------------------------------------------------------
# Constant + helper invariants
# ---------------------------------------------------------------------------


class TestAllowedProfilesPerPlan:
    """REQ-12 / REQ-13: locks the canonical plan→role allow-set so an
    accidental edit cannot widen or narrow the policy without a test
    failure surfacing it."""

    def test_free_admin_only_plus_personal(self):
        assert ALLOWED_PROFILES_PER_PLAN["free"] == frozenset({"personal", "admin"})

    def test_core_excludes_kb_manager(self):
        # REQ-13: kb_manager is unlocked only on ``complete``.
        assert "kb_manager" not in ALLOWED_PROFILES_PER_PLAN["core"]
        assert "group_manager" in ALLOWED_PROFILES_PER_PLAN["core"]

    def test_complete_includes_all_five_roles(self):
        assert ALLOWED_PROFILES_PER_PLAN["complete"] == frozenset(
            {"personal", "company", "kb_manager", "group_manager", "admin"}
        )


class TestAssertRoleAllowedForPlan:
    def test_admin_passes_on_every_plan(self):
        # ``admin`` is a billing-decision role; it must always be assignable.
        for plan in ("free", "core", "complete"):
            assert_role_allowed_for_plan("admin", plan)

    def test_kb_manager_rejected_on_free_with_correct_error_shape(self):
        with pytest.raises(HTTPException) as exc:
            assert_role_allowed_for_plan("kb_manager", "free")
        assert exc.value.status_code == 403
        detail = exc.value.detail
        assert isinstance(detail, dict)
        assert detail["error_code"] == "role_not_allowed_for_plan"
        assert detail["role"] == "kb_manager"
        assert detail["plan"] == "free"
        assert "admin" in detail["allowed"]

    def test_kb_manager_rejected_on_core(self):
        with pytest.raises(HTTPException) as exc:
            assert_role_allowed_for_plan("kb_manager", "core")
        assert exc.value.status_code == 403
        assert exc.value.detail["error_code"] == "role_not_allowed_for_plan"

    def test_kb_manager_passes_on_complete(self):
        assert_role_allowed_for_plan("kb_manager", "complete")

    def test_unknown_plan_falls_back_to_free_set(self):
        # Defensive: a typo in a plan field MUST NOT widen the role ladder.
        with pytest.raises(HTTPException) as exc:
            assert_role_allowed_for_plan("kb_manager", "totally-not-a-plan")
        assert exc.value.detail["error_code"] == "role_not_allowed_for_plan"


# ---------------------------------------------------------------------------
# REQ-7 — personal cannot write to org-owned KBs
# ---------------------------------------------------------------------------


def _kb_mock(*, slug: str = "shared", owner_type: str = "org") -> MagicMock:
    kb = MagicMock()
    kb.id = 7
    kb.slug = slug
    kb.org_id = 1
    kb.owner_type = owner_type
    kb.default_org_role = None
    kb.created_by = "owner-1"
    return kb


def _kb_query_db_mock(kb: MagicMock) -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = kb
    db.execute.return_value = result
    return db


class TestPersonalCannotWriteToOrgKB:
    """REQ-7: ``_get_writable_kb_or_raise`` and ``create_connector`` raise
    HTTP 403 ``org_kb_write_requires_company`` when a personal-role caller
    targets an org-owned KB."""

    @pytest.mark.asyncio
    async def test_get_writable_kb_or_raise_403_for_personal_on_org_kb(self):
        from app.api.app_knowledge_sources import _get_writable_kb_or_raise

        kb = _kb_mock(owner_type="org")
        db = _kb_query_db_mock(kb)
        perms = make_perms(role="personal", user_id="alice", org_id=1)

        with pytest.raises(HTTPException) as exc:
            await _get_writable_kb_or_raise("shared", perms, db)

        assert exc.value.status_code == 403
        assert exc.value.detail == {"error_code": "org_kb_write_requires_company"}

    @pytest.mark.asyncio
    async def test_get_writable_kb_or_raise_403_fires_before_role_lookup(self):
        """The 403 fires synchronously after the KB query — no expensive
        ``get_user_role_for_kb`` call is made for personal callers (avoids
        leaking signal that an org-KB exists or computing role in vain).
        """
        from app.api.app_knowledge_sources import _get_writable_kb_or_raise

        kb = _kb_mock(owner_type="org")
        db = _kb_query_db_mock(kb)
        perms = make_perms(role="personal", user_id="alice", org_id=1)

        with patch(
            "app.api.app_knowledge_sources.get_user_role_for_kb",
            new=AsyncMock(),
        ) as mock_role:
            with pytest.raises(HTTPException):
                await _get_writable_kb_or_raise("shared", perms, db)
        mock_role.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_writable_kb_or_raise_passes_for_personal_on_user_kb(self):
        """Personal-owned KBs (``owner_type="user"``) MUST still be writable
        by the owner — the gate is org-KB-specific."""
        from app.api.app_knowledge_sources import _get_writable_kb_or_raise

        kb = _kb_mock(owner_type="user")
        db = _kb_query_db_mock(kb)
        perms = make_perms(role="personal", user_id="alice", org_id=1)

        with (
            patch(
                "app.api.app_knowledge_sources.get_user_role_for_kb",
                new=AsyncMock(return_value="owner"),
            ),
            patch(
                "app.api.app_knowledge_sources._load_org_or_500",
                new=AsyncMock(return_value=MagicMock()),
            ),
            patch(
                "app.api.app_knowledge_sources.assert_can_add_item_to_kb",
                new=AsyncMock(),
            ),
        ):
            result = await _get_writable_kb_or_raise("personal", perms, db)
        assert result is kb

    @pytest.mark.asyncio
    async def test_get_writable_kb_or_raise_passes_for_company_on_org_kb(self):
        """Company effective_role MAY write to org-owned KBs (gate is
        personal-specific). Confirms the new check does not over-block."""
        from app.api.app_knowledge_sources import _get_writable_kb_or_raise

        kb = _kb_mock(owner_type="org")
        db = _kb_query_db_mock(kb)
        perms = make_perms(role="company", user_id="bob", org_id=1)

        with (
            patch(
                "app.api.app_knowledge_sources.get_user_role_for_kb",
                new=AsyncMock(return_value="contributor"),
            ),
            patch(
                "app.api.app_knowledge_sources._load_org_or_500",
                new=AsyncMock(return_value=MagicMock()),
            ),
            patch(
                "app.api.app_knowledge_sources.assert_can_add_item_to_kb",
                new=AsyncMock(),
            ),
        ):
            result = await _get_writable_kb_or_raise("shared", perms, db)
        assert result is kb

    @pytest.mark.asyncio
    async def test_create_connector_403_for_personal_on_org_kb(self):
        """Same REQ-7 gate must fire on the connector-create path."""
        from app.api.connectors import ConnectorCreateRequest, create_connector

        kb = _kb_mock(owner_type="org")
        db = AsyncMock()

        body = ConnectorCreateRequest(
            name="GitHub",
            connector_type="github",
            config={"repo": "klai/portal"},
            schedule="manual",
        )
        perms = make_perms(role="personal", user_id="alice", org_id=1)

        with patch(
            "app.api.connectors._get_kb_with_owner_check",
            new=AsyncMock(return_value=kb),
        ):
            with pytest.raises(HTTPException) as exc:
                await create_connector(kb_slug="shared", body=body, perms=perms, db=db)

        assert exc.value.status_code == 403
        assert exc.value.detail == {"error_code": "org_kb_write_requires_company"}


# ---------------------------------------------------------------------------
# REQ-12 / REQ-13 — plan-ceiling on admin endpoints
# ---------------------------------------------------------------------------


class TestPlanCeilingOnRoleAssignment:
    """REQ-12: invite_user / update_user_role / promote_admin reject roles
    outside the org plan's allow-set with HTTP 403 ``role_not_allowed_for_plan``."""

    @pytest.mark.asyncio
    async def test_invite_user_rejects_kb_manager_on_core(self):
        from app.api.admin.users import InviteRequest, invite_user

        org = MagicMock()
        org.id = 101
        org.seats = 100
        org.plan = "core"  # REQ-13: kb_manager NOT allowed on core

        mock_db = AsyncMock()
        locked_org_result = MagicMock()
        locked_org_result.scalar_one.return_value = org
        mock_db.execute.return_value = locked_org_result
        mock_db.scalar.return_value = 0

        body = InviteRequest(
            email="km@example.com",
            first_name="K",
            last_name="M",
            role="kb_manager",
            preferred_language="nl",
        )
        perms = make_perms(role="admin", user_id="admin-1", org_id=101, plan="core")

        with pytest.raises(HTTPException) as exc:
            await invite_user(body=body, perms=perms, db=mock_db)

        assert exc.value.status_code == 403
        assert exc.value.detail["error_code"] == "role_not_allowed_for_plan"
        assert exc.value.detail["role"] == "kb_manager"
        assert exc.value.detail["plan"] == "core"

    @pytest.mark.asyncio
    async def test_invite_user_accepts_kb_manager_on_complete(self):
        """The mirror case: ``complete`` plan accepts ``kb_manager``.

        We rely on the existing ``test_invite_user_grants_portal_role_to_zitadel``
        parametrize for full role-mapping coverage; this test pins the
        plan-ceiling path specifically does not over-block.
        """
        from app.api.admin.users import InviteRequest, invite_user

        org = MagicMock()
        org.id = 101
        org.seats = 100
        org.plan = "complete"

        mock_db = AsyncMock()
        locked_org_result = MagicMock()
        locked_org_result.scalar_one.return_value = org
        mock_db.execute.return_value = locked_org_result
        mock_db.scalar.return_value = 0

        body = InviteRequest(
            email="km@example.com",
            first_name="K",
            last_name="M",
            role="kb_manager",
            preferred_language="nl",
        )
        perms = make_perms(role="admin", user_id="admin-1", org_id=101, plan="complete")

        with (
            patch("app.api.admin.users.zitadel") as mock_zitadel,
            patch(
                "app.services.default_knowledge_bases.create_default_personal_kb",
                new=AsyncMock(),
            ),
        ):
            mock_zitadel.invite_user = AsyncMock(return_value={"userId": "new-km"})
            mock_zitadel.grant_user_role = AsyncMock()
            # Should not raise — plan permits the role.
            await invite_user(body=body, perms=perms, db=mock_db)

    @pytest.mark.asyncio
    async def test_update_user_role_rejects_company_on_free(self):
        from app.api.admin.users import RoleUpdateRequest, update_user_role

        mock_db = AsyncMock()
        body = RoleUpdateRequest(role="company")
        perms = make_perms(role="admin", user_id="admin-1", org_id=42, plan="free")

        with patch(
            "app.api.admin.users._lock_org_for_role_change",
            new=AsyncMock(),
        ):
            with pytest.raises(HTTPException) as exc:
                await update_user_role(zitadel_user_id="zit-2", body=body, perms=perms, db=mock_db)

        assert exc.value.status_code == 403
        assert exc.value.detail["error_code"] == "role_not_allowed_for_plan"
        assert exc.value.detail["role"] == "company"
        assert exc.value.detail["plan"] == "free"

    @pytest.mark.asyncio
    async def test_promote_admin_passes_on_every_plan(self):
        """``admin`` is the one role every plan must accept; this guards
        against an accidental tightening (e.g. dropping admin from the
        ``free`` set) silently breaking owner-self-promote flows.
        """
        from app.api.admin.users import promote_admin

        for plan in ("free", "core", "complete"):
            mock_db = AsyncMock()
            target = MagicMock()
            target.role = "company"
            target.zitadel_user_id = "zit-target"
            execute_result = MagicMock()
            execute_result.scalar_one_or_none.return_value = target
            mock_db.execute.return_value = execute_result
            mock_db.commit = AsyncMock()

            perms = make_perms(role="admin", user_id="admin-1", org_id=10, plan=plan)

            with (
                patch("app.api.admin.users.emit_event"),
                patch(
                    "app.api.admin.users.log_event",
                    new=AsyncMock(),
                ),
                patch(
                    "app.api.admin.users.zitadel.grant_user_role",
                    new=AsyncMock(),
                ),
            ):
                # Must not raise on any plan — admin is always allowed.
                await promote_admin(zitadel_user_id="zit-target", perms=perms, db=mock_db)


# ---------------------------------------------------------------------------
# REQ-6 — endpoint passes effective_role through to get_accessible_kb_slugs
# ---------------------------------------------------------------------------


class TestEndpointsPassRoleToAccessFilter:
    """REQ-6: the API layer must hand the caller's effective_role down to
    ``get_accessible_kb_slugs`` so the personal-role exclusion fires.

    The service-layer filtering itself is already covered by
    ``tests/test_profiles.py::test_personal_role_skips_default_org_role_kbs``.
    These tests pin the wiring contract.
    """

    @pytest.mark.asyncio
    async def test_list_kbs_with_access_forwards_effective_role(self):
        from app.api.app_knowledge_bases import list_kbs_with_access

        db = AsyncMock()
        empty_result = MagicMock()
        empty_result.scalars.return_value.all.return_value = []
        db.execute.return_value = empty_result
        perms = make_perms(role="personal", user_id="alice", org_id=1)

        with patch(
            "app.services.access.get_accessible_kb_slugs",
            new=AsyncMock(return_value=[]),
        ) as mock_slugs:
            await list_kbs_with_access(perms=perms, db=db)

        mock_slugs.assert_awaited_once()
        kwargs = mock_slugs.await_args.kwargs
        assert kwargs.get("user_role") == ProfileRole.PERSONAL.value, (
            "list_kbs_with_access MUST pass user_role=perms.effective_role.value to "
            "get_accessible_kb_slugs so personal-role callers do not see the org slug."
        )

    @pytest.mark.asyncio
    async def test_get_knowledge_stats_forwards_effective_role(self):
        from app.api.knowledge import get_knowledge_stats

        org = MagicMock()
        org.zitadel_org_id = "zit-org-1"
        db = AsyncMock()
        perms = make_perms(role="personal", user_id="alice", org_id=1)

        with (
            patch(
                "app.api.knowledge._load_org_or_500",
                new=AsyncMock(return_value=org),
            ),
            patch(
                "app.api.knowledge.get_accessible_kb_slugs",
                new=AsyncMock(return_value=["personal-alice"]),
            ) as mock_slugs,
            patch(
                "app.api.knowledge.asyncio.gather",
                new=AsyncMock(return_value=[0, 0]),
            ),
        ):
            await get_knowledge_stats(perms=perms, db=db)

        mock_slugs.assert_awaited_once()
        kwargs = mock_slugs.await_args.kwargs
        assert kwargs.get("user_role") == ProfileRole.PERSONAL.value
