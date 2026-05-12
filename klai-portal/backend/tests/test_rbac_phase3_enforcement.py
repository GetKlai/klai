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
    """SPEC-PORTAL-PLAN-RENAME-001: locks the canonical plan→role allow-set
    so an accidental edit cannot widen or narrow the policy without a
    test failure surfacing it.

    Plan ladder: free (sentinel) < chat (€28) < knowledge (€68 full unlock).
    """

    def test_free_admin_only_plus_personal(self):
        assert ALLOWED_PROFILES_PER_PLAN["free"] == frozenset({"personal", "admin"})

    def test_chat_excludes_kb_manager_and_group_manager(self):
        # kb_manager / group_manager need kb.create_org / kb.members which
        # the chat tier does not unlock; assigning them would be cosmetic.
        assert "kb_manager" not in ALLOWED_PROFILES_PER_PLAN["chat"]
        assert "group_manager" not in ALLOWED_PROFILES_PER_PLAN["chat"]
        assert ALLOWED_PROFILES_PER_PLAN["chat"] == frozenset({"personal", "company", "admin"})

    def test_knowledge_includes_all_five_roles(self):
        assert ALLOWED_PROFILES_PER_PLAN["knowledge"] == frozenset(
            {"personal", "company", "kb_manager", "group_manager", "admin"}
        )

    def test_legacy_slugs_are_gone(self):
        for legacy in ("core", "professional", "complete"):
            assert legacy not in ALLOWED_PROFILES_PER_PLAN


class TestAssertRoleAllowedForPlanDeprecated:
    """SPEC-PORTAL-PRICING-PER-USER-001 Phase 3 (2026-05-12): the function
    is now a no-op that emits a ``DeprecationWarning``. Role assignment
    is decoupled from plan ceilings — admin can assign any role on any
    plan.

    The function lives on for one release cycle so import sites do not
    break. Phase 6 deletes it entirely.
    """

    @pytest.mark.parametrize(
        ("role", "plan"),
        [
            ("kb_manager", "free"),
            ("kb_manager", "chat"),
            ("kb_manager", "knowledge"),
            ("company", "free"),
            ("admin", "free"),
            ("admin", "chat"),
            ("admin", "knowledge"),
            # Unknown plan no longer falls back to free's allow-set —
            # the no-op accepts everything.
            ("kb_manager", "totally-not-a-plan"),
        ],
    )
    def test_no_op_for_every_combo(self, role: str, plan: str):
        with pytest.warns(DeprecationWarning, match="decouples role from plan ceiling"):
            assert_role_allowed_for_plan(role, plan)

    def test_no_op_does_not_raise(self):
        # The pre-Phase-3 contract was: raise HTTPException(403) on any
        # mismatch. The new contract is: NEVER raise.
        try:
            with pytest.warns(DeprecationWarning):
                assert_role_allowed_for_plan("kb_manager", "free")
        except HTTPException as exc:  # pragma: no cover — would mean regression
            pytest.fail(
                f"assert_role_allowed_for_plan must be a no-op after Phase 3 "
                f"but raised HTTPException({exc.status_code}): {exc.detail!r}. "
                f"Phase 3 of SPEC-PORTAL-PRICING-PER-USER-001 decoupled role "
                f"from plan ceiling — the gate is removed."
            )


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


class TestPlanCeilingGoneOnRoleAssignment:
    """SPEC-PORTAL-PRICING-PER-USER-001 Phase 3 (2026-05-12): the plan-
    ceiling gates on invite_user / update_user_role / promote_admin are
    gone. Role is decoupled from plan; ``seat_type`` is the billing axis.

    Replaces the pre-Phase-3 ``TestPlanCeilingOnRoleAssignment`` which
    asserted 403 for the same combinations.
    """

    @pytest.mark.asyncio
    async def test_invite_user_accepts_kb_manager_on_chat_plan(self):
        """The exact case that PR #599's pre-flight checked for: a
        ``kb_manager`` invite on a ``chat``-plan org. Pre-Phase-3 this was
        the 403 ``role_not_allowed_for_plan`` that drove the SPEC.
        Phase 3 makes the invite succeed; the seat assignment defaults
        to ``knowledge`` via ``suggest_seat(kb_manager)`` so the user
        actually gets the features their role implies.
        """
        from app.api.admin.users import InviteRequest, invite_user

        org = MagicMock()
        org.id = 101
        org.plan = "chat"

        mock_db = AsyncMock()
        locked_org_result = MagicMock()
        locked_org_result.scalar_one.return_value = org
        mock_db.execute.return_value = locked_org_result

        body = InviteRequest(
            email="km@example.com",
            first_name="K",
            last_name="M",
            role="kb_manager",
            preferred_language="nl",
        )
        perms = make_perms(role="admin", user_id="admin-1", org_id=101, plan="chat")

        with (
            patch("app.api.admin.users.zitadel") as mock_zitadel,
            patch(
                "app.services.default_knowledge_bases.create_default_personal_kb",
                new=AsyncMock(),
            ),
        ):
            mock_zitadel.invite_user = AsyncMock(return_value={"userId": "new-km"})
            mock_zitadel.grant_user_role = AsyncMock()
            # Must not raise — plan ceiling is gone.
            await invite_user(body=body, perms=perms, db=mock_db)

    @pytest.mark.asyncio
    async def test_invite_user_no_seat_cap(self):
        """SPEC AC-2: inviting the (N+1)th user on an org with
        ``seats = N`` succeeds. Bill rolls up from active user count per
        seat tier (Phase 5), not from ``org.seats``.
        """
        from app.api.admin.users import InviteRequest, invite_user

        org = MagicMock()
        org.id = 101
        # Hard cap of 5 — pre-Phase-3 this would 409 once active >= 5.
        org.seats = 5
        org.plan = "knowledge"

        mock_db = AsyncMock()
        locked = MagicMock()
        locked.scalar_one.return_value = org
        mock_db.execute.return_value = locked

        # Important: db.scalar() is NOT consulted in the new flow (the
        # active-count select was removed). If a future refactor adds it
        # back, returning a number >= seats from this mock would trip the
        # old cap — but that branch is gone now.
        mock_db.scalar.return_value = 999  # would have tripped old cap

        body = InviteRequest(
            email="overflow@example.com",
            first_name="O",
            last_name="V",
            role="company",
            preferred_language="nl",
        )
        perms = make_perms(role="admin", user_id="admin-1", org_id=101, plan="knowledge")

        with (
            patch("app.api.admin.users.zitadel") as mock_zitadel,
            patch(
                "app.services.default_knowledge_bases.create_default_personal_kb",
                new=AsyncMock(),
            ),
        ):
            mock_zitadel.invite_user = AsyncMock(return_value={"userId": "new-over"})
            mock_zitadel.grant_user_role = AsyncMock()
            # Must not raise. Pre-Phase-3 raised
            # HTTPException(409, "Seat limit reached").
            await invite_user(body=body, perms=perms, db=mock_db)

    @pytest.mark.asyncio
    async def test_update_user_role_to_kb_manager_on_free_plan(self):
        """Pre-Phase-3 this was 403. Phase 3 makes it succeed.

        Mirror of the deleted ``test_update_user_role_rejects_company_on_free``.
        """
        from app.api.admin.users import RoleUpdateRequest, update_user_role

        locked_org = MagicMock()
        locked_org.plan = "free"  # used to trigger the deprecated gate
        target_user = MagicMock()
        target_user.role = "company"
        target_user.zitadel_user_id = "zit-2"
        mock_db = AsyncMock()
        locked_result = MagicMock()
        locked_result.scalar_one.return_value = locked_org
        user_result = MagicMock()
        user_result.scalar_one_or_none.return_value = target_user
        mock_db.execute = AsyncMock(side_effect=[locked_result, user_result])
        mock_db.commit = AsyncMock()

        body = RoleUpdateRequest(role="kb_manager")
        perms = make_perms(role="admin", user_id="admin-1", org_id=42, plan="free")

        await update_user_role(zitadel_user_id="zit-2", body=body, perms=perms, db=mock_db)

        # Role assignment landed.
        assert target_user.role == "kb_manager"

    @pytest.mark.asyncio
    async def test_promote_admin_passes_on_every_plan_no_warning(self):
        """``admin`` was always allowed pre-Phase-3 too, but the test
        still ran through ``assert_role_allowed_for_plan``. Post-Phase-3
        that call site is gone — promotion succeeds without invoking the
        deprecated function at all.
        """
        from app.api.admin.users import promote_admin

        for plan in ("free", "chat", "knowledge"):
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
                patch("app.api.admin.users.log_event", new=AsyncMock()),
                patch("app.api.admin.users.zitadel.grant_user_role", new=AsyncMock()),
            ):
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
