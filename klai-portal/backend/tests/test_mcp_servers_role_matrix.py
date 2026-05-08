"""Characterization snapshots for `mcp_servers.py` admin gate.

SPEC-PORTAL-RBAC-REFACTOR-001 Pre-phase. Pins the role matrix for the three
admin-gated MCP-server endpoints (list/update/test).

  - admin                          → gate passes
  - personal/company/kb_manager/group_manager → 403
  - unauthenticated                → 401

Phase 5 of the SPEC adds an additional `require_platform_unlocked("custom_mcps")`
gate on `update_mcp_server` for non-managed catalog entries. That layer is NOT
exercised here — these snapshots only pin the existing admin-role gate. After
Phase 5 lands, an additional set of tests pins the platform-unlock layer.
"""

from __future__ import annotations

import pytest

# Aliased import for `test_mcp_server` so pytest doesn't collect the imported
# endpoint function as a test (any module-level callable starting with `test_`
# is collected).
from app.api.mcp_servers import (
    McpServerUpdateRequest,
    list_mcp_servers,
    update_mcp_server,
)
from app.api.mcp_servers import test_mcp_server as _test_mcp_server_endpoint
from tests.role_matrix_helpers import (
    NON_ADMIN_ROLES,
    assert_admin_passes_gate,
    assert_role_blocked_at_gate,
    assert_unauthenticated_blocked,
    make_db_mock,
)

_MODULE = "app.api.mcp_servers"


def _update_body() -> McpServerUpdateRequest:
    return McpServerUpdateRequest(enabled=False, env={})


# ---------------------------------------------------------------------------
# list_mcp_servers — GET /api/mcp-servers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_mcp_servers_admin_passes_gate() -> None:
    await assert_admin_passes_gate(
        list_mcp_servers,
        _MODULE,
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
@pytest.mark.asyncio
async def test_list_mcp_servers_non_admin_blocked(role: str) -> None:
    await assert_role_blocked_at_gate(
        list_mcp_servers,
        _MODULE,
        role,
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.asyncio
async def test_list_mcp_servers_unauthenticated() -> None:
    await assert_unauthenticated_blocked(
        list_mcp_servers,
        _MODULE,
        credentials=None,
        db=make_db_mock(),
    )


# ---------------------------------------------------------------------------
# update_mcp_server — PUT /api/mcp-servers/{server_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_mcp_server_admin_passes_gate() -> None:
    await assert_admin_passes_gate(
        update_mcp_server,
        _MODULE,
        server_id="some-server",
        body=_update_body(),
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
@pytest.mark.asyncio
async def test_update_mcp_server_non_admin_blocked(role: str) -> None:
    await assert_role_blocked_at_gate(
        update_mcp_server,
        _MODULE,
        role,
        server_id="some-server",
        body=_update_body(),
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.asyncio
async def test_update_mcp_server_unauthenticated() -> None:
    await assert_unauthenticated_blocked(
        update_mcp_server,
        _MODULE,
        server_id="some-server",
        body=_update_body(),
        credentials=None,
        db=make_db_mock(),
    )


# ---------------------------------------------------------------------------
# test_mcp_server — POST /api/mcp-servers/{server_id}/test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_endpoint_admin_passes_gate() -> None:
    await assert_admin_passes_gate(
        _test_mcp_server_endpoint,
        _MODULE,
        server_id="some-server",
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
@pytest.mark.asyncio
async def test_probe_endpoint_non_admin_blocked(role: str) -> None:
    await assert_role_blocked_at_gate(
        _test_mcp_server_endpoint,
        _MODULE,
        role,
        server_id="some-server",
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.asyncio
async def test_probe_endpoint_unauthenticated() -> None:
    await assert_unauthenticated_blocked(
        _test_mcp_server_endpoint,
        _MODULE,
        server_id="some-server",
        credentials=None,
        db=make_db_mock(),
    )
