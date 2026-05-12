"""SPEC-PORTAL-EXTENSIONS-UNIFY-001 Phase 1 — partner_api platform-unlock gate.

Pins the platform-unlock gate on all 5 /api/admin/api-keys endpoints:
tenants without ``partner_api`` in ``platform_unlocked_features`` get 403,
tenants with it pass through to the role gate.

The actual gate is enforced by ``Depends(require_platform_unlocked("partner_api"))``.
We test the inner ``_dep`` directly with a synthetic ``UserPermissions`` —
the same pattern test_admin_api_keys_role_matrix.py uses for the role gate.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException, status

from app.core.permissions import require_platform_unlocked
from tests.conftest import make_perms


class TestPartnerApiGate:
    """Gate semantics: feature in unlocks → pass; missing → 403."""

    @pytest.mark.asyncio
    async def test_unlocked_tenant_passes_gate(self) -> None:
        """An admin in a tenant with partner_api unlocked is allowed through."""
        _dep = require_platform_unlocked("partner_api")
        perms = make_perms(role="admin", platform_unlocked_features=["partner_api"])
        # Should NOT raise.
        result = await _dep(perms=perms)
        assert result is perms

    @pytest.mark.asyncio
    async def test_locked_tenant_blocked_with_403(self) -> None:
        """An admin in a tenant WITHOUT partner_api gets 403, even if other
        unlocks are present."""
        _dep = require_platform_unlocked("partner_api")
        perms = make_perms(
            role="admin",
            platform_unlocked_features=["widgets", "custom_mcps", "scribe", "docs"],
        )
        with pytest.raises(HTTPException) as exc:
            await _dep(perms=perms)
        assert exc.value.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_empty_unlocks_blocked_with_403(self) -> None:
        """A tenant with no unlocks at all is blocked."""
        _dep = require_platform_unlocked("partner_api")
        perms = make_perms(role="admin", platform_unlocked_features=[])
        with pytest.raises(HTTPException) as exc:
            await _dep(perms=perms)
        assert exc.value.status_code == status.HTTP_403_FORBIDDEN


class TestEndpointsHavePlatformGate:
    """Static guard: every /api/admin/api-keys endpoint must declare the gate.

    Refactor-resistance: if a future change accidentally drops the gate on
    a single endpoint, this test catches it without needing a live HTTP
    round-trip per endpoint.
    """

    def test_all_five_endpoints_depend_on_partner_api_unlock(self) -> None:
        """Every router operation in admin_api_keys must include
        require_platform_unlocked('partner_api') in its FastAPI dependencies."""
        from app.api.admin_api_keys import router

        # router.routes contains the five APIRoute objects.
        operation_count = 0
        for route in router.routes:
            # Skip non-APIRoute (e.g. catch-all WebSocket routes — none here).
            if not hasattr(route, "dependant"):
                continue
            operation_count += 1
            # Each dependency tree must contain a call to require_platform_unlocked
            # for the "partner_api" feature. We assert by walking the closure of
            # any nested _dep functions and checking their bound feature.
            depends_on_partner_api = _depends_on_partner_api_unlock(route.dependant)
            assert depends_on_partner_api, (
                f"Endpoint {route.path} ({route.methods}) is missing require_platform_unlocked('partner_api') gate"
            )

        assert operation_count == 5, f"Expected 5 /api/admin/api-keys operations, found {operation_count}"


def _depends_on_partner_api_unlock(dependant) -> bool:
    """Walk a FastAPI Dependant tree looking for require_platform_unlocked('partner_api')."""
    for sub in dependant.dependencies:
        call = sub.call
        # The factory creates a nested function `_dep` whose closure binds
        # the `feature` string. Inspect cell contents.
        if getattr(call, "__name__", None) == "_dep" and getattr(call, "__closure__", None):
            for cell in call.__closure__:
                try:
                    val = cell.cell_contents
                except ValueError:
                    continue
                if val == "partner_api":
                    return True
        # Recurse to handle nested dependency trees.
        if _depends_on_partner_api_unlock(sub):
            return True
    return False
