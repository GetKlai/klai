"""DELETE /api/admin/users/me lets any member leave their own organisation.

The request goes through the admin router the application mounts, so the route
that answers is the one the router dispatches to, not a handler called by name.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.admin import router as admin_router
from app.core.database import get_db
from app.core.permissions import get_caller
from tests.conftest import make_perms

ORG_ID = 101
USER_ID = "member-1"


async def _leave(role: str, admin_count: int) -> tuple[Any, AsyncMock]:
    perms = make_perms(role=role, user_id=USER_ID, org_id=ORG_ID)
    caller_row = MagicMock()
    db = AsyncMock()
    db.execute.return_value = MagicMock(scalar_one=MagicMock(return_value=caller_row))
    db.scalar.return_value = admin_count

    app = FastAPI()
    app.include_router(admin_router)
    app.dependency_overrides[get_caller] = lambda: perms
    app.dependency_overrides[get_db] = lambda: db
    with (
        patch("app.api.admin.users._lock_org_for_role_change", new=AsyncMock()),
        patch("app.api.admin.users.forget_internal_cases", new=AsyncMock()),
        patch("app.api.admin.users.emit_event"),
        patch("app.api.admin.users._sync_identity_login_or_502", new=AsyncMock()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.delete("/api/admin/users/me")
    return response, db


@pytest.mark.asyncio
@pytest.mark.parametrize(("role", "admin_count"), [("company", 1), ("admin", 2)])
async def test_member_leaves_own_organisation(role: str, admin_count: int) -> None:
    response, db = await _leave(role, admin_count)

    assert response.status_code == 200, response.text
    assert response.json() == {"message": "You have left the workspace."}
    db.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_last_admin_cannot_leave() -> None:
    response, db = await _leave("admin", 1)

    assert response.status_code == 409
    assert response.json() == {"detail": "Promote another admin or delete the workspace before leaving."}
    db.delete.assert_not_awaited()
