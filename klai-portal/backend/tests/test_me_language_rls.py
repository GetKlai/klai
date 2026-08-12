"""PATCH /api/me/language must set tenant context before writing portal_users.

Reproduction for the 2026-08-12 "Save failed" report: portal_users is a
Category-A RLS table — SELECT has the permissive IS-NULL branch (auth runs
before tenant context exists), but WITH CHECK is strict. The handler looked
the user up via the permissive read and then committed an UPDATE without ever
calling set_tenant, so every language save failed with
InsufficientPrivilegeError: new row violates row-level security policy for
table "portal_users" (portal-api logs 12:31 UTC, PATCH /api/me/language 500).

Contract: after resolving the caller's portal_users row, the handler calls
set_tenant(db, user.org_id) BEFORE the mutating commit.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _db_returning_user(user: MagicMock) -> AsyncMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    return db


class TestUpdateMyLanguageTenantContext:
    @pytest.mark.asyncio
    async def test_set_tenant_called_with_user_org_before_commit(self) -> None:
        from app.api.me import LanguageUpdate, update_my_language

        user = MagicMock()
        user.org_id = 8
        db = _db_returning_user(user)

        call_order: list[str] = []
        set_tenant_mock = AsyncMock(side_effect=lambda *_a, **_k: call_order.append("set_tenant"))
        db.commit = AsyncMock(side_effect=lambda: call_order.append("commit"))

        creds = MagicMock()
        creds.credentials = "token"

        zit = MagicMock()
        zit.get_userinfo = AsyncMock(return_value={"sub": "zuser-1"})
        zit.update_user_language = AsyncMock()

        with (
            patch("app.api.me.zitadel", zit),
            patch("app.api.me.set_tenant", set_tenant_mock),
        ):
            resp = await update_my_language(
                body=LanguageUpdate(preferred_language="en"),
                credentials=creds,
                db=db,
            )

        set_tenant_mock.assert_awaited_once_with(db, 8)
        assert call_order == ["set_tenant", "commit"], (
            "set_tenant must run BEFORE the mutating commit — a commit without "
            "tenant context trips portal_users' strict WITH CHECK policy"
        )
        assert user.preferred_language == "en"
        assert resp.message

    @pytest.mark.asyncio
    async def test_unknown_user_404s_without_tenant_or_commit(self) -> None:
        from fastapi import HTTPException

        from app.api.me import LanguageUpdate, update_my_language

        db = _db_returning_user(None)  # type: ignore[arg-type]
        set_tenant_mock = AsyncMock()

        creds = MagicMock()
        creds.credentials = "token"

        zit = MagicMock()
        zit.get_userinfo = AsyncMock(return_value={"sub": "zuser-unknown"})

        with (
            patch("app.api.me.zitadel", zit),
            patch("app.api.me.set_tenant", set_tenant_mock),
            pytest.raises(HTTPException) as exc,
        ):
            await update_my_language(
                body=LanguageUpdate(preferred_language="en"),
                credentials=creds,
                db=db,
            )

        assert exc.value.status_code == 404
        set_tenant_mock.assert_not_awaited()
        db.commit.assert_not_awaited()
