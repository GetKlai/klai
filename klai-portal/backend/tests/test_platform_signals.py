"""The signal overview is for platform admins only, and returns the documentation."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.admin.platform_signals import list_observability_signals
from app.core.permissions import ProfileRole, get_caller
from app.main import app
from app.services.observability_signals import SIGNALS


@pytest.mark.asyncio
async def test_it_returns_every_documented_signal():
    result = await list_observability_signals(_perms=MagicMock())

    assert result == SIGNALS
    assert all(signal.purpose and signal.how_to_read for signal in result), (
        "a signal without a reason or a way to read it is the invisibility this page exists to end"
    )


def _as(*, is_platform_admin: bool, role: ProfileRole) -> TestClient:
    app.dependency_overrides[get_caller] = lambda: MagicMock(is_platform_admin=is_platform_admin, effective_role=role)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_overrides():
    yield
    app.dependency_overrides.pop(get_caller, None)


def test_a_platform_admin_sees_the_overview():
    """Through the real app, so the test proves the route USES the gate.

    Testing the dependency on its own, as other platform tests do, would stay
    green if someone dropped it from this route's signature.
    """
    response = _as(is_platform_admin=True, role=ProfileRole.ADMIN).get("/api/admin/platform/signals")

    assert response.status_code == 200
    assert len(response.json()) == len(SIGNALS)


@pytest.mark.parametrize(
    ("is_platform_admin", "role", "reason"),
    [
        (False, ProfileRole.ADMIN, "platform admin org required"),
        (True, ProfileRole.COMPANY, "admin role required"),
    ],
)
def test_anyone_else_is_refused_by_this_gate(is_platform_admin, role, reason):
    """The reason is asserted too: a 403 from some other layer would pass a
    status-only check while this route stood open."""
    response = _as(is_platform_admin=is_platform_admin, role=role).get("/api/admin/platform/signals")

    assert response.status_code == 403
    assert reason in response.json()["detail"]
