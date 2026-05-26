"""Characterization snapshots for `app_gaps.py` capability gate.

SPEC-PORTAL-RBAC-REFACTOR-001 follow-up. The route-level gate is
`Depends(require_capability(KB_GAPS))`; endpoint bodies should not add a
second hardcoded admin gate.

These snapshots call endpoint functions directly, so FastAPI router dependencies
are bypassed. They pin that no inner admin-only gate remains.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException, status

from app.api.app_gaps import (
    get_gap_summary,
    get_gaps_by_taxonomy,
    list_gaps,
)
from tests.conftest import make_perms
from tests.role_matrix_helpers import (
    NON_ADMIN_ROLES,
    assert_admin_passes_gate,
    assert_unauthenticated_blocked,
    make_db_mock,
)

_MODULE = "app.api.app_gaps"


async def _assert_role_reaches_endpoint(endpoint, role: str, **kwargs) -> None:
    filtered = {k: v for k, v in kwargs.items() if k != "credentials"}
    try:
        await endpoint(perms=make_perms(role=role), **filtered)
    except HTTPException as exc:
        assert exc.status_code not in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ), f"role={role!r} unexpectedly blocked inside endpoint: {exc.detail!r}"
    except Exception:  # noqa: S110 - post-gate explosion is acceptable; gate-pass is the assertion.
        pass


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
async def test_list_gaps_non_admin_not_admin_blocked_inside_endpoint(role: str) -> None:
    await _assert_role_reaches_endpoint(
        list_gaps,
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
async def test_get_gap_summary_non_admin_not_admin_blocked_inside_endpoint(role: str) -> None:
    await _assert_role_reaches_endpoint(
        get_gap_summary,
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
async def test_get_gaps_by_taxonomy_non_admin_not_admin_blocked_inside_endpoint(role: str) -> None:
    await _assert_role_reaches_endpoint(
        get_gaps_by_taxonomy,
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
