"""SPEC-PRIVACY-QUERY-SHADOW-001 Unit 9 — tenant self-service endpoint tests.

POST /api/orgs/me/telemetry-level. Mirrors the operator-side endpoint
contract (REQ-11) but with a different auth path (tenant-admin JWT
rather than internal-secret) and a hardcoded ``operator_kind='tenant_admin'``
audit field. Authorization is REQUIRED — non-admin role returns 403.

After SPEC-PORTAL-RBAC-REFACTOR-001 Phase 2-cleanup the endpoint uses the
declarative ``Depends(get_caller_at_least(ProfileRole.ADMIN))`` gate. The
non-admin 403 case therefore tests the gate itself (via
``assert_role_blocked_at_gate``), and admin-pass cases call the endpoint
directly with a synthetic ``UserPermissions``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_perms
from tests.role_matrix_helpers import assert_role_blocked_at_gate


@pytest.mark.asyncio
async def test_admin_can_set_telemetry_level() -> None:
    """REQ-15 happy path: admin flips shadow → full, gets 200 + new level."""
    from app.api.orgs import TelemetryLevelUpdate, set_my_org_telemetry_level

    db = AsyncMock()
    perms = make_perms(role="admin", user_id="zit-user-1", org_id=42)

    with patch(
        "app.api.orgs.set_telemetry_level",
        AsyncMock(return_value=("shadow", "full")),
    ) as mock_set:
        out = await set_my_org_telemetry_level(TelemetryLevelUpdate(level="full"), perms=perms, db=db)

    assert out.telemetry_level == "full"
    mock_set.assert_awaited_once()
    call = mock_set.await_args
    assert call.kwargs["org_id"] == 42
    assert call.kwargs["operator_kind"] == "tenant_admin"
    assert call.kwargs["operator_user_id"] == "zit-user-1"
    assert call.kwargs["reason"] == "tenant self-service via admin UI"


@pytest.mark.asyncio
async def test_non_admin_user_gets_403() -> None:
    """REQ-15 negative test: a regular user can't flip the level.

    The 403 fires at the ``get_caller_at_least(ADMIN)`` gate before the
    endpoint body executes — tested via ``assert_role_blocked_at_gate``
    against the canonical helper. ``set_telemetry_level`` is therefore
    never invoked, which is the security-relevant invariant.
    """
    from app.api.orgs import set_my_org_telemetry_level

    await assert_role_blocked_at_gate(
        endpoint=set_my_org_telemetry_level,
        module_path="app.api.orgs",
        role="company",
    )


@pytest.mark.asyncio
async def test_lookup_error_translates_to_404() -> None:
    """Defense-in-depth: race condition org-deletion → 404 not 500."""
    from fastapi import HTTPException

    from app.api.orgs import TelemetryLevelUpdate, set_my_org_telemetry_level

    db = AsyncMock()
    perms = make_perms(role="admin", user_id="zit-user-1", org_id=42)

    with patch(
        "app.api.orgs.set_telemetry_level",
        AsyncMock(side_effect=LookupError("org_id=42 not found")),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await set_my_org_telemetry_level(
                TelemetryLevelUpdate(level="full"),
                perms=perms,
                db=db,
            )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_admin_can_set_back_to_shadow() -> None:
    """Idempotent flip back to default works (covers the 'reset' flow)."""
    from app.api.orgs import TelemetryLevelUpdate, set_my_org_telemetry_level

    db = AsyncMock()
    perms = make_perms(role="admin", user_id="zit-user-1", org_id=42)

    with patch(
        "app.api.orgs.set_telemetry_level",
        AsyncMock(return_value=("full", "shadow")),
    ):
        out = await set_my_org_telemetry_level(
            TelemetryLevelUpdate(level="shadow"),
            perms=perms,
            db=db,
        )

    assert out.telemetry_level == "shadow"


# ``MagicMock`` retained for runtime introspection helpers; explicit import
# avoids "unused" warnings even though pytest fixtures may take MagicMocks.
_ = MagicMock
