"""Characterization snapshot for `taxonomy.py` admin gate.

SPEC-PORTAL-RBAC-REFACTOR-001 Pre-phase. Pins the role matrix for the only
admin-gated endpoint in `taxonomy.py`: `taxonomy_coverage`. The other
taxonomy endpoints are gated via `Depends(require_capability(KB_TAXONOMY))`
at the router level (or are member-readable like `taxonomy_top_tags`); they
are out of scope for the imperative-`_require_admin` snapshot.

  - admin                          → gate passes
  - personal/company/kb_manager/group_manager → 403
  - unauthenticated                → 401
"""

from __future__ import annotations

import pytest

from app.api.taxonomy import taxonomy_coverage
from tests.role_matrix_helpers import (
    NON_ADMIN_ROLES,
    assert_admin_passes_gate,
    assert_role_blocked_at_gate,
    assert_unauthenticated_blocked,
    make_db_mock,
)

_MODULE = "app.api.taxonomy"


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
async def test_taxonomy_coverage_non_admin_blocked(role: str) -> None:
    await assert_role_blocked_at_gate(
        taxonomy_coverage,
        _MODULE,
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
