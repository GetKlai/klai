"""Characterization snapshots for `app_gaps.py` admin gate.

SPEC-PORTAL-RBAC-REFACTOR-001 Pre-phase. Pins the role matrix for the three
admin-gated gap endpoints (`list_gaps`, `get_gap_summary`, `get_gaps_by_taxonomy`).

  - admin                          → gate passes
  - personal/company/kb_manager/group_manager → 403
  - unauthenticated                → 401

Note: the router itself carries `dependencies=[Depends(require_capability(KB_GAPS))]`,
which is enforced by FastAPI BEFORE the endpoint body runs in real HTTP. These
snapshots call the endpoint functions directly so the router-level capability
gate is bypassed; only the inner `_require_admin` gate is exercised. That is
exactly what the SPEC's Phase 1+2 refactor changes — the capability gate is
out of scope here and pinned elsewhere.
"""

from __future__ import annotations

import pytest

from app.api.app_gaps import (
    get_gap_summary,
    get_gaps_by_taxonomy,
    list_gaps,
)
from tests.role_matrix_helpers import (
    NON_ADMIN_ROLES,
    assert_admin_passes_gate,
    assert_role_blocked_at_gate,
    assert_unauthenticated_blocked,
    make_db_mock,
)

_MODULE = "app.api.app_gaps"


# ---------------------------------------------------------------------------
# list_gaps — GET /api/app/gaps
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_gaps_admin_passes_gate() -> None:
    await assert_admin_passes_gate(
        list_gaps,
        _MODULE,
        days=30,
        gap_type=None,
        taxonomy_node_id=None,
        limit=50,
        include_resolved=False,
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
@pytest.mark.asyncio
async def test_list_gaps_non_admin_blocked(role: str) -> None:
    await assert_role_blocked_at_gate(
        list_gaps,
        _MODULE,
        role,
        days=30,
        gap_type=None,
        taxonomy_node_id=None,
        limit=50,
        include_resolved=False,
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.asyncio
async def test_list_gaps_unauthenticated() -> None:
    await assert_unauthenticated_blocked(
        list_gaps,
        _MODULE,
        days=30,
        gap_type=None,
        taxonomy_node_id=None,
        limit=50,
        include_resolved=False,
        credentials=None,
        db=make_db_mock(),
    )


# ---------------------------------------------------------------------------
# get_gap_summary — GET /api/app/gaps/summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_gap_summary_admin_passes_gate() -> None:
    await assert_admin_passes_gate(
        get_gap_summary,
        _MODULE,
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
@pytest.mark.asyncio
async def test_get_gap_summary_non_admin_blocked(role: str) -> None:
    await assert_role_blocked_at_gate(
        get_gap_summary,
        _MODULE,
        role,
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.asyncio
async def test_get_gap_summary_unauthenticated() -> None:
    await assert_unauthenticated_blocked(
        get_gap_summary,
        _MODULE,
        credentials=None,
        db=make_db_mock(),
    )


# ---------------------------------------------------------------------------
# get_gaps_by_taxonomy — GET /api/app/gaps/by-taxonomy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_gaps_by_taxonomy_admin_passes_gate() -> None:
    await assert_admin_passes_gate(
        get_gaps_by_taxonomy,
        _MODULE,
        days=30,
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
@pytest.mark.asyncio
async def test_get_gaps_by_taxonomy_non_admin_blocked(role: str) -> None:
    await assert_role_blocked_at_gate(
        get_gaps_by_taxonomy,
        _MODULE,
        role,
        days=30,
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.asyncio
async def test_get_gaps_by_taxonomy_unauthenticated() -> None:
    await assert_unauthenticated_blocked(
        get_gaps_by_taxonomy,
        _MODULE,
        days=30,
        credentials=None,
        db=make_db_mock(),
    )
