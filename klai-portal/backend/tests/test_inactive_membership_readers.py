"""Readers that hand out role, products, capabilities or tokens see only active memberships.

A suspended or offboarded ``portal_users`` row keeps its role and seat for a
later reactivation; none of these readers may grant anything from it.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status

from tests.conftest import make_perms


def _db_one(row: object) -> AsyncMock:
    db = AsyncMock()
    result = MagicMock()
    result.one_or_none.return_value = row
    result.all.return_value = [row] if row is not None else []
    db.execute.return_value = result
    return db


@pytest.mark.asyncio
@pytest.mark.parametrize("membership_status", ["suspended", "offboarded"])
async def test_internal_permissions_route_refuses_membership_that_is_not_active(membership_status: str) -> None:
    from app.api import internal

    with (
        patch.object(internal, "_require_internal_token", new=AsyncMock()),
        patch.object(internal, "_audit_internal_call", new=AsyncMock()),
        patch.object(
            internal, "resolve_user_permissions", new=AsyncMock(return_value=make_perms(status=membership_status))
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await internal.get_user_permissions(zitadel_user_id="user-1", request=MagicMock(), db=AsyncMock())

    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
    assert isinstance(exc.value.detail, dict)
    assert exc.value.detail.get("error_code") == f"user_{membership_status}"


@pytest.mark.asyncio
@pytest.mark.parametrize("membership_status", ["suspended", "offboarded"])
async def test_products_of_membership_that_is_not_active_are_empty(membership_status: str) -> None:
    from app.services.entitlements import get_effective_products

    assert await get_effective_products("user-1", _db_one(("admin", "knowledge", [], membership_status))) == []


@pytest.mark.asyncio
async def test_products_of_active_membership_are_granted() -> None:
    from app.services.entitlements import get_effective_products

    assert "chat" in await get_effective_products("user-1", _db_one(("admin", "knowledge", [], "active")))


@pytest.mark.asyncio
@pytest.mark.parametrize("membership_status", ["suspended", "offboarded"])
async def test_capabilities_of_membership_that_is_not_active_are_empty(membership_status: str) -> None:
    from app.api.dependencies import get_effective_capabilities

    user = SimpleNamespace(role="admin", seat_type="knowledge", status=membership_status)
    org = SimpleNamespace(plan="knowledge")

    assert await get_effective_capabilities("user-1", _db_one((user, org))) == set()


@pytest.mark.asyncio
async def test_mcp_consent_uses_the_active_membership() -> None:
    from app.api.mcp_oauth import _resolve_user_org

    suspended = (SimpleNamespace(status="suspended"), SimpleNamespace(name="Org A"))
    active = (SimpleNamespace(status="active"), SimpleNamespace(name="Org B"))
    db = AsyncMock()
    result = MagicMock()
    result.all.return_value = [suspended, active]
    db.execute.return_value = result

    assert await _resolve_user_org(db, "user-1") == active


@pytest.mark.asyncio
async def test_mcp_consent_without_active_membership_finds_no_user() -> None:
    from app.api.mcp_oauth import _resolve_user_org

    db = AsyncMock()
    result = MagicMock()
    result.all.return_value = [(SimpleNamespace(status="offboarded"), SimpleNamespace(name="Org A"))]
    db.execute.return_value = result

    assert await _resolve_user_org(db, "user-1") is None
