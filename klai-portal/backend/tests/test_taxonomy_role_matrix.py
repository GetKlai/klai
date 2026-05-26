"""Characterization snapshot for `taxonomy.py` coverage capability gate.

SPEC-PORTAL-RBAC-REFACTOR-001 follow-up. `taxonomy_coverage` now follows the
same route-level `Depends(require_capability(KB_TAXONOMY))` pattern as the
other taxonomy endpoints; it should not add a second hardcoded admin gate.

These tests call the endpoint directly, so FastAPI router dependencies are
bypassed. They pin that no inner admin-only gate remains.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException, status

from app.api.taxonomy import taxonomy_coverage
from tests.conftest import make_perms
from tests.role_matrix_helpers import (
    NON_ADMIN_ROLES,
    assert_admin_passes_gate,
    assert_unauthenticated_blocked,
    make_db_mock,
)

_MODULE = "app.api.taxonomy"


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
# taxonomy_coverage — GET /api/app/knowledge-bases/{kb_slug}/taxonomy/coverage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_taxonomy_coverage_admin_passes_gate() -> None:
    await assert_admin_passes_gate(
        taxonomy_coverage,
        _MODULE,
        kb_slug="general",
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
@pytest.mark.asyncio
async def test_taxonomy_coverage_non_admin_not_admin_blocked_inside_endpoint(role: str) -> None:
    await _assert_role_reaches_endpoint(
        taxonomy_coverage,
        role,
        kb_slug="general",
        credentials=None,
        db=make_db_mock(),
    )


@pytest.mark.asyncio
async def test_taxonomy_coverage_unauthenticated() -> None:
    await assert_unauthenticated_blocked(
        taxonomy_coverage,
        _MODULE,
        kb_slug="general",
        credentials=None,
        db=make_db_mock(),
    )
