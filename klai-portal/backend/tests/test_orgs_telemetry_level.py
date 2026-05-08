"""SPEC-PRIVACY-QUERY-SHADOW-001 Unit 9 — tenant self-service endpoint tests.

POST /api/orgs/me/telemetry-level. Mirrors the operator-side endpoint
contract (REQ-11) but with a different auth path (tenant-admin JWT
rather than internal-secret) and a hardcoded ``operator_kind='tenant_admin'``
audit field. Authorization is REQUIRED — non-admin role returns 403.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _FakeUser:
    def __init__(self, role: str = "admin") -> None:
        self.role = role
        self.zitadel_user_id = "zit-user-1"


class _FakeOrg:
    def __init__(self, level: str = "shadow") -> None:
        self.id = 42
        self.telemetry_level = level


@pytest.mark.asyncio
async def test_admin_can_set_telemetry_level() -> None:
    """REQ-15 happy path: admin flips shadow → full, gets 200 + new level."""
    from app.api.orgs import TelemetryLevelUpdate, set_my_org_telemetry_level

    org = _FakeOrg(level="shadow")
    user = _FakeUser(role="admin")
    creds = MagicMock()
    db = AsyncMock()

    with (
        patch(
            "app.api.orgs._get_caller_org",
            AsyncMock(return_value=("zit-user-1", org, user)),
        ),
        patch(
            "app.api.orgs.set_telemetry_level",
            AsyncMock(return_value=("shadow", "full")),
        ) as mock_set,
    ):
        out = await set_my_org_telemetry_level(TelemetryLevelUpdate(level="full"), creds, db)

    assert out.telemetry_level == "full"
    mock_set.assert_awaited_once()
    call = mock_set.await_args
    assert call.kwargs["org_id"] == 42
    assert call.kwargs["operator_kind"] == "tenant_admin"
    assert call.kwargs["operator_user_id"] == "zit-user-1"
    assert call.kwargs["reason"] == "tenant self-service via admin UI"


@pytest.mark.asyncio
async def test_non_admin_user_gets_403() -> None:
    """REQ-15 negative test: a regular user can't flip the level."""
    from fastapi import HTTPException

    from app.api.orgs import TelemetryLevelUpdate, set_my_org_telemetry_level

    org = _FakeOrg(level="shadow")
    user = _FakeUser(role="company")  # non-admin
    creds = MagicMock()
    db = AsyncMock()

    with (
        patch(
            "app.api.orgs._get_caller_org",
            AsyncMock(return_value=("zit-user-2", org, user)),
        ),
        patch("app.api.orgs.set_telemetry_level", AsyncMock()) as mock_set,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await set_my_org_telemetry_level(TelemetryLevelUpdate(level="full"), creds, db)

    assert exc_info.value.status_code == 403
    # Service-layer must NOT be called when role check fails.
    mock_set.assert_not_called()


@pytest.mark.asyncio
async def test_lookup_error_translates_to_404() -> None:
    """Defense-in-depth: race condition org-deletion → 404 not 500."""
    from fastapi import HTTPException

    from app.api.orgs import TelemetryLevelUpdate, set_my_org_telemetry_level

    org = _FakeOrg(level="shadow")
    user = _FakeUser(role="admin")
    creds = MagicMock()
    db = AsyncMock()

    with (
        patch(
            "app.api.orgs._get_caller_org",
            AsyncMock(return_value=("zit-user-1", org, user)),
        ),
        patch(
            "app.api.orgs.set_telemetry_level",
            AsyncMock(side_effect=LookupError("org_id=42 not found")),
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await set_my_org_telemetry_level(TelemetryLevelUpdate(level="full"), creds, db)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_admin_can_set_back_to_shadow() -> None:
    """Idempotent flip back to default works (covers the 'reset' flow)."""
    from app.api.orgs import TelemetryLevelUpdate, set_my_org_telemetry_level

    org = _FakeOrg(level="full")
    user = _FakeUser(role="admin")
    creds = MagicMock()
    db = AsyncMock()

    with (
        patch(
            "app.api.orgs._get_caller_org",
            AsyncMock(return_value=("zit-user-1", org, user)),
        ),
        patch(
            "app.api.orgs.set_telemetry_level",
            AsyncMock(return_value=("full", "shadow")),
        ),
    ):
        out = await set_my_org_telemetry_level(TelemetryLevelUpdate(level="shadow"), creds, db)

    assert out.telemetry_level == "shadow"
