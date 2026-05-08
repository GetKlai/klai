"""Characterization snapshots for `admin_api_keys.py` admin gate.

SPEC-PORTAL-RBAC-REFACTOR-001 Pre-phase. Pins the role matrix for all five
partner-API-key admin endpoints (create/list/get/update/delete).

  - admin                          → gate passes
  - personal/company/kb_manager/group_manager → 403
  - unauthenticated                → 401
"""

from __future__ import annotations

import pytest

from app.api.admin_api_keys import (
    CreateApiKeyRequest,
    KbAccessEntry,
    UpdateApiKeyRequest,
    create_api_key,
    delete_api_key,
    get_api_key_detail,
    list_api_keys,
    update_api_key,
)
from tests.role_matrix_helpers import (
    NON_ADMIN_ROLES,
    assert_admin_passes_gate,
    assert_role_blocked_at_gate,
    assert_unauthenticated_blocked,
    make_db_mock,
)

_MODULE = "app.api.admin_api_keys"


def _create_body() -> CreateApiKeyRequest:
    return CreateApiKeyRequest(
        name="Test Key",
        description=None,
        permissions={"chat": True, "feedback": False, "knowledge_append": False},
        kb_access=[KbAccessEntry(kb_id=1, access_level="read")],
        rate_limit_rpm=60,
    )


def _update_body() -> UpdateApiKeyRequest:
    return UpdateApiKeyRequest(name="Renamed Key")


# ---------------------------------------------------------------------------
# create_api_key — POST /api/admin/api-keys
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_api_key_admin_passes_gate() -> None:
    await assert_admin_passes_gate(
        create_api_key,
        _MODULE,
        body=_create_body(),
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
@pytest.mark.asyncio
async def test_create_api_key_non_admin_blocked(role: str) -> None:
    await assert_role_blocked_at_gate(
        create_api_key,
        _MODULE,
        role,
        body=_create_body(),
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.asyncio
async def test_create_api_key_unauthenticated() -> None:
    await assert_unauthenticated_blocked(
        create_api_key,
        _MODULE,
        body=_create_body(),
        credentials=None,
        db=make_db_mock(),
    )


# ---------------------------------------------------------------------------
# list_api_keys — GET /api/admin/api-keys
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_api_keys_admin_passes_gate() -> None:
    await assert_admin_passes_gate(
        list_api_keys,
        _MODULE,
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
@pytest.mark.asyncio
async def test_list_api_keys_non_admin_blocked(role: str) -> None:
    await assert_role_blocked_at_gate(
        list_api_keys,
        _MODULE,
        role,
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.asyncio
async def test_list_api_keys_unauthenticated() -> None:
    await assert_unauthenticated_blocked(
        list_api_keys,
        _MODULE,
        credentials=None,
        db=make_db_mock(),
    )


# ---------------------------------------------------------------------------
# get_api_key_detail — GET /api/admin/api-keys/{key_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_api_key_detail_admin_passes_gate() -> None:
    await assert_admin_passes_gate(
        get_api_key_detail,
        _MODULE,
        key_id="key-test",
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
@pytest.mark.asyncio
async def test_get_api_key_detail_non_admin_blocked(role: str) -> None:
    await assert_role_blocked_at_gate(
        get_api_key_detail,
        _MODULE,
        role,
        key_id="key-test",
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.asyncio
async def test_get_api_key_detail_unauthenticated() -> None:
    await assert_unauthenticated_blocked(
        get_api_key_detail,
        _MODULE,
        key_id="key-test",
        credentials=None,
        db=make_db_mock(),
    )


# ---------------------------------------------------------------------------
# update_api_key — PATCH /api/admin/api-keys/{key_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_api_key_admin_passes_gate() -> None:
    await assert_admin_passes_gate(
        update_api_key,
        _MODULE,
        key_id="key-test",
        body=_update_body(),
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
@pytest.mark.asyncio
async def test_update_api_key_non_admin_blocked(role: str) -> None:
    await assert_role_blocked_at_gate(
        update_api_key,
        _MODULE,
        role,
        key_id="key-test",
        body=_update_body(),
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.asyncio
async def test_update_api_key_unauthenticated() -> None:
    await assert_unauthenticated_blocked(
        update_api_key,
        _MODULE,
        key_id="key-test",
        body=_update_body(),
        credentials=None,
        db=make_db_mock(),
    )


# ---------------------------------------------------------------------------
# delete_api_key — DELETE /api/admin/api-keys/{key_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_api_key_admin_passes_gate() -> None:
    await assert_admin_passes_gate(
        delete_api_key,
        _MODULE,
        key_id="key-test",
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
@pytest.mark.asyncio
async def test_delete_api_key_non_admin_blocked(role: str) -> None:
    await assert_role_blocked_at_gate(
        delete_api_key,
        _MODULE,
        role,
        key_id="key-test",
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.asyncio
async def test_delete_api_key_unauthenticated() -> None:
    await assert_unauthenticated_blocked(
        delete_api_key,
        _MODULE,
        key_id="key-test",
        credentials=None,
        db=make_db_mock(),
    )
