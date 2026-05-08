"""Characterization snapshots for `admin_widgets.py` admin gate.

SPEC-PORTAL-RBAC-REFACTOR-001 Pre-phase. Pins the current behaviour of the
five widget endpoints (create/list/get/update/delete) against the role
matrix:

  - admin                          → gate passes (no 401/403)
  - personal/company/kb_manager/group_manager → 403 from `_require_admin`
  - unauthenticated                → 401 from `_get_caller_org`

Snapshots stay after the refactor lands as the regression-suite for the
uniform `Depends(get_caller_at_least(ProfileRole.ADMIN))` gate.
"""

from __future__ import annotations

import pytest

from app.api.admin_widgets import (
    CreateWidgetRequest,
    UpdateWidgetRequest,
    WidgetConfig,
    create_widget,
    delete_widget,
    get_widget_detail,
    list_widgets,
    update_widget,
)
from tests.role_matrix_helpers import (
    NON_ADMIN_ROLES,
    assert_admin_passes_gate,
    assert_role_blocked_at_gate,
    assert_unauthenticated_blocked,
    make_db_mock,
)

_MODULE = "app.api.admin_widgets"


def _create_body() -> CreateWidgetRequest:
    return CreateWidgetRequest(
        name="Help Bot",
        description=None,
        kb_ids=[1],
        rate_limit_rpm=60,
        widget_config=WidgetConfig(),
    )


def _update_body() -> UpdateWidgetRequest:
    return UpdateWidgetRequest(name="Renamed Bot")


# ---------------------------------------------------------------------------
# create_widget — POST /api/admin/widgets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_widget_admin_passes_gate() -> None:
    await assert_admin_passes_gate(
        create_widget,
        _MODULE,
        body=_create_body(),
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
@pytest.mark.asyncio
async def test_create_widget_non_admin_blocked(role: str) -> None:
    await assert_role_blocked_at_gate(
        create_widget,
        _MODULE,
        role,
        body=_create_body(),
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.asyncio
async def test_create_widget_unauthenticated() -> None:
    await assert_unauthenticated_blocked(
        create_widget,
        _MODULE,
        body=_create_body(),
        credentials=None,
        db=make_db_mock(),
    )


# ---------------------------------------------------------------------------
# list_widgets — GET /api/admin/widgets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_widgets_admin_passes_gate() -> None:
    await assert_admin_passes_gate(
        list_widgets,
        _MODULE,
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
@pytest.mark.asyncio
async def test_list_widgets_non_admin_blocked(role: str) -> None:
    await assert_role_blocked_at_gate(
        list_widgets,
        _MODULE,
        role,
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.asyncio
async def test_list_widgets_unauthenticated() -> None:
    await assert_unauthenticated_blocked(
        list_widgets,
        _MODULE,
        credentials=None,
        db=make_db_mock(),
    )


# ---------------------------------------------------------------------------
# get_widget_detail — GET /api/admin/widgets/{widget_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_widget_detail_admin_passes_gate() -> None:
    await assert_admin_passes_gate(
        get_widget_detail,
        _MODULE,
        widget_id="wgt_test",
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
@pytest.mark.asyncio
async def test_get_widget_detail_non_admin_blocked(role: str) -> None:
    await assert_role_blocked_at_gate(
        get_widget_detail,
        _MODULE,
        role,
        widget_id="wgt_test",
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.asyncio
async def test_get_widget_detail_unauthenticated() -> None:
    await assert_unauthenticated_blocked(
        get_widget_detail,
        _MODULE,
        widget_id="wgt_test",
        credentials=None,
        db=make_db_mock(),
    )


# ---------------------------------------------------------------------------
# update_widget — PATCH /api/admin/widgets/{widget_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_widget_admin_passes_gate() -> None:
    await assert_admin_passes_gate(
        update_widget,
        _MODULE,
        widget_id="wgt_test",
        body=_update_body(),
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
@pytest.mark.asyncio
async def test_update_widget_non_admin_blocked(role: str) -> None:
    await assert_role_blocked_at_gate(
        update_widget,
        _MODULE,
        role,
        widget_id="wgt_test",
        body=_update_body(),
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.asyncio
async def test_update_widget_unauthenticated() -> None:
    await assert_unauthenticated_blocked(
        update_widget,
        _MODULE,
        widget_id="wgt_test",
        body=_update_body(),
        credentials=None,
        db=make_db_mock(),
    )


# ---------------------------------------------------------------------------
# delete_widget — DELETE /api/admin/widgets/{widget_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_widget_admin_passes_gate() -> None:
    await assert_admin_passes_gate(
        delete_widget,
        _MODULE,
        widget_id="wgt_test",
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
@pytest.mark.asyncio
async def test_delete_widget_non_admin_blocked(role: str) -> None:
    await assert_role_blocked_at_gate(
        delete_widget,
        _MODULE,
        role,
        widget_id="wgt_test",
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.asyncio
async def test_delete_widget_unauthenticated() -> None:
    await assert_unauthenticated_blocked(
        delete_widget,
        _MODULE,
        widget_id="wgt_test",
        credentials=None,
        db=make_db_mock(),
    )
