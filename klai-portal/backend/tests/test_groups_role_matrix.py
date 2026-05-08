"""Characterization snapshots for `groups.py` GROUP_MANAGER gate.

SPEC-PORTAL-RBAC-REFACTOR-001 Phase 2h. Pins the role matrix for the three
group-manager-gated endpoints after the SPEC refactor:

  - update_group       (PATCH  /api/admin/groups/{id})
  - delete_group       (DELETE /api/admin/groups/{id})
  - toggle_group_admin (PATCH  /api/admin/groups/{id}/members/{user_id})

Gate matrix after Phase 2h:
  - admin / group_manager          → gate passes (GROUP_MANAGER+ dep)
  - personal / company / kb_manager → 403
  - unauthenticated                → 401

Note: assert_role_blocked_at_gate hardcodes ProfileRole.ADMIN to test the
ADMIN dep directly. For GROUP_MANAGER-gated endpoints the three
NON_GROUP_MANAGER_ROLES (personal, company, kb_manager) also fail the
ADMIN dep — they still get 403. group_manager is no longer in the blocked
set because the gate was lowered to GROUP_MANAGER by REQ-8/9.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.groups import (
    GroupAdminToggleRequest,
    GroupUpdateRequest,
    delete_group,
    toggle_group_admin,
    update_group,
)
from tests.conftest import make_perms
from tests.role_matrix_helpers import (
    _PostGateSentinel,
    assert_admin_passes_gate,
    assert_role_blocked_at_gate,
    assert_unauthenticated_blocked,
    make_db_mock,
)

_MODULE = "app.api.groups"

# Roles that still fail the GROUP_MANAGER gate (personal < company < kb_manager < group_manager).
# group_manager is excluded because it now PASSES these endpoints.
NON_GROUP_MANAGER_ROLES: tuple[str, ...] = ("personal", "company", "kb_manager")


def _update_body() -> GroupUpdateRequest:
    return GroupUpdateRequest(name="Renamed Group")


def _toggle_body(*, is_group_admin: bool = True) -> GroupAdminToggleRequest:
    return GroupAdminToggleRequest(is_group_admin=is_group_admin)


# ---------------------------------------------------------------------------
# update_group — PATCH /api/admin/groups/{group_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_group_admin_passes_gate() -> None:
    await assert_admin_passes_gate(
        update_group,
        _MODULE,
        group_id=1,
        body=_update_body(),
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.parametrize("role", NON_GROUP_MANAGER_ROLES)
@pytest.mark.asyncio
async def test_update_group_non_admin_blocked(role: str) -> None:
    await assert_role_blocked_at_gate(
        update_group,
        _MODULE,
        role,
        group_id=1,
        body=_update_body(),
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.asyncio
async def test_update_group_group_manager_passes_gate() -> None:
    try:
        await update_group(
            group_id=1,
            body=_update_body(),
            perms=make_perms(role="group_manager"),
            db=make_db_mock(),
        )
    except _PostGateSentinel:
        pass  # gate passed — sentinel fired at first DB call
    except HTTPException as e:
        assert e.status_code not in (401, 403), f"group_manager unexpectedly blocked: {e.status_code}"


@pytest.mark.asyncio
async def test_update_group_unauthenticated() -> None:
    await assert_unauthenticated_blocked(
        update_group,
        _MODULE,
        group_id=1,
        body=_update_body(),
        credentials=None,
        db=make_db_mock(),
    )


# ---------------------------------------------------------------------------
# delete_group — DELETE /api/admin/groups/{group_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_group_admin_passes_gate() -> None:
    await assert_admin_passes_gate(
        delete_group,
        _MODULE,
        group_id=1,
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.parametrize("role", NON_GROUP_MANAGER_ROLES)
@pytest.mark.asyncio
async def test_delete_group_non_admin_blocked(role: str) -> None:
    await assert_role_blocked_at_gate(
        delete_group,
        _MODULE,
        role,
        group_id=1,
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.asyncio
async def test_delete_group_group_manager_passes_gate() -> None:
    try:
        await delete_group(
            group_id=1,
            perms=make_perms(role="group_manager"),
            db=make_db_mock(),
        )
    except _PostGateSentinel:
        pass  # gate passed — sentinel fired at first DB call
    except HTTPException as e:
        assert e.status_code not in (401, 403), f"group_manager unexpectedly blocked: {e.status_code}"


@pytest.mark.asyncio
async def test_delete_group_unauthenticated() -> None:
    await assert_unauthenticated_blocked(
        delete_group,
        _MODULE,
        group_id=1,
        credentials=None,
        db=make_db_mock(),
    )


# ---------------------------------------------------------------------------
# toggle_group_admin — PATCH /api/admin/groups/{group_id}/members/{user_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_toggle_group_admin_admin_passes_gate() -> None:
    await assert_admin_passes_gate(
        toggle_group_admin,
        _MODULE,
        group_id=1,
        user_id="uid-target",
        body=_toggle_body(),
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.parametrize("role", NON_GROUP_MANAGER_ROLES)
@pytest.mark.asyncio
async def test_toggle_group_admin_non_admin_blocked(role: str) -> None:
    await assert_role_blocked_at_gate(
        toggle_group_admin,
        _MODULE,
        role,
        group_id=1,
        user_id="uid-target",
        body=_toggle_body(),
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.asyncio
async def test_toggle_group_admin_group_manager_passes_gate() -> None:
    try:
        await toggle_group_admin(
            group_id=1,
            user_id="uid-target",
            body=_toggle_body(),
            perms=make_perms(role="group_manager"),
            db=make_db_mock(),
        )
    except _PostGateSentinel:
        pass  # gate passed — sentinel fired at first DB call
    except HTTPException as e:
        assert e.status_code not in (401, 403), f"group_manager unexpectedly blocked: {e.status_code}"


@pytest.mark.asyncio
async def test_toggle_group_admin_unauthenticated() -> None:
    await assert_unauthenticated_blocked(
        toggle_group_admin,
        _MODULE,
        group_id=1,
        user_id="uid-target",
        body=_toggle_body(),
        credentials=None,
        db=make_db_mock(),
    )
