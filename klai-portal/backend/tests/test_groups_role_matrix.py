"""Characterization snapshots for `groups.py` admin gate.

SPEC-PORTAL-RBAC-REFACTOR-001 Pre-phase. Pins the role matrix for the three
admin-gated group endpoints called out in the SPEC pre-phase scope:

  - update_group       (PATCH  /api/admin/groups/{id})
  - delete_group       (DELETE /api/admin/groups/{id})
  - toggle_group_admin (PATCH  /api/admin/groups/{id}/members/{user_id})

  - admin                          → gate passes
  - personal/company/kb_manager/group_manager → 403
  - unauthenticated                → 401

Note: REQ-8/9 of the SPEC explicitly REQUIRES that after the refactor a
`group_manager` user can rename / delete / toggle membership in groups
within their own org. The current code returns 403 — that is the bug the
SPEC fixes. These snapshots pin TODAY's (broken) behaviour so the Phase
2h refactor can demonstrate the change is intentional. Phase 3C will add
new tests asserting `group_manager` → 200 / 204.
"""

from __future__ import annotations

import pytest

from app.api.groups import (
    GroupAdminToggleRequest,
    GroupUpdateRequest,
    delete_group,
    toggle_group_admin,
    update_group,
)
from tests.role_matrix_helpers import (
    NON_ADMIN_ROLES,
    assert_admin_passes_gate,
    assert_role_blocked_at_gate,
    assert_unauthenticated_blocked,
    make_db_mock,
)

_MODULE = "app.api.groups"


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


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
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


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
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


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
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
