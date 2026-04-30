from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _ps(org_id, kind, auto_accept=False):
    return {
        "session_id": "sid",
        "session_token": "stk",
        "zitadel_user_id": "zu1",
        "email": "t@acme.nl",
        "auth_request_id": "ar1",
        "entries": [{"org_id": org_id, "name": "Acme", "slug": "acme", "kind": kind, "auto_accept": auto_accept}],
    }


def _org(org_id=1, slug="acme"):
    o = MagicMock()
    o.id = org_id
    o.slug = slug
    o.name = "Acme"
    return o


def _admin():
    u = MagicMock()
    u.email = "admin@acme.nl"
    u.display_name = "Admin"
    return u


class TestResponseModels:
    def test_member_model(self):
        from app.api.auth_select import SelectWorkspaceMember

        m = SelectWorkspaceMember(kind="member", workspace_url="https://x")
        assert m.kind == "member"

    def test_auto_join_model(self):
        from app.api.auth_select import SelectWorkspaceAutoJoin

        m = SelectWorkspaceAutoJoin(kind="auto_join", workspace_url="https://x")
        assert m.kind == "auto_join"

    def test_pending_model(self):
        from app.api.auth_select import SelectWorkspacePending

        m = SelectWorkspacePending(kind="join_request_pending", redirect_to="/join-request/sent")
        assert m.kind == "join_request_pending"
        assert m.redirect_to == "/join-request/sent"


class TestSessionValidation:
    @pytest.mark.asyncio
    async def test_unknown_org_id_returns_403(self):
        from fastapi import HTTPException

        from app.api.auth_select import SelectWorkspaceRequest, select_workspace

        session = _ps(1, "domain_match", True)
        with patch("app.api.auth_select.pending_session_svc") as svc:
            svc.consume = AsyncMock(return_value=session)
            with pytest.raises(HTTPException) as exc:
                await select_workspace(body=SelectWorkspaceRequest(ref="r", org_id=999), db=AsyncMock())
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_expired_session_returns_410(self):
        from fastapi import HTTPException

        from app.api.auth_select import SelectWorkspaceRequest, select_workspace

        with patch("app.api.auth_select.pending_session_svc") as svc:
            svc.consume = AsyncMock(return_value=None)
            with pytest.raises(HTTPException) as exc:
                await select_workspace(body=SelectWorkspaceRequest(ref="old", org_id=1), db=AsyncMock())
        assert exc.value.status_code == 410


class TestMemberPath:
    @pytest.mark.asyncio
    async def test_member_entry_returns_member_kind(self):
        from app.api.auth_select import SelectWorkspaceRequest, select_workspace

        session = _ps(1, "member", False)
        org = _org()
        with (
            patch("app.api.auth_select.pending_session_svc") as svc,
            patch("app.api.auth_select.zitadel") as zit,
            patch("app.api.auth_select.emit_event"),
        ):
            svc.consume = AsyncMock(return_value=session)
            zit.finalize_auth_request = AsyncMock(return_value="https://acme.getklai.com/cb")
            db = AsyncMock()
            db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=org)))
            result = await select_workspace(body=SelectWorkspaceRequest(ref="r", org_id=1), db=db)
        assert result.kind == "member"
        assert "acme" in result.workspace_url


class TestAutoJoinPath:
    @pytest.mark.asyncio
    async def test_auto_accept_returns_auto_join_kind(self):
        from app.api.auth_select import SelectWorkspaceRequest, select_workspace

        session = _ps(1, "domain_match", True)
        org = _org()
        with (
            patch("app.api.auth_select.pending_session_svc") as svc,
            patch("app.api.auth_select.zitadel") as zit,
            patch("app.api.auth_select.emit_event"),
            patch("app.api.auth_select.notify_auto_join_admins", AsyncMock()),
        ):
            svc.consume = AsyncMock(return_value=session)
            zit.finalize_auth_request = AsyncMock(return_value="https://acme.getklai.com/cb")
            db = AsyncMock()
            or_ = MagicMock()
            or_.scalar_one_or_none = MagicMock(return_value=org)
            ar_ = MagicMock()
            ar_.scalars.return_value.all.return_value = [_admin()]
            db.execute = AsyncMock(side_effect=[or_, ar_])
            db.flush = AsyncMock()
            result = await select_workspace(body=SelectWorkspaceRequest(ref="r", org_id=1), db=db)
        assert result.kind == "auto_join"
        assert "acme" in result.workspace_url

    @pytest.mark.asyncio
    async def test_auto_accept_inserts_portal_users_row(self):
        from app.api.auth_select import SelectWorkspaceRequest, select_workspace
        from app.models.portal import PortalUser

        session = _ps(1, "domain_match", True)
        org = _org()
        with (
            patch("app.api.auth_select.pending_session_svc") as svc,
            patch("app.api.auth_select.zitadel") as zit,
            patch("app.api.auth_select.emit_event"),
            patch("app.api.auth_select.notify_auto_join_admins", AsyncMock()),
        ):
            svc.consume = AsyncMock(return_value=session)
            zit.finalize_auth_request = AsyncMock(return_value="https://acme.getklai.com/cb")
            db = AsyncMock()
            or_ = MagicMock()
            or_.scalar_one_or_none = MagicMock(return_value=org)
            ar_ = MagicMock()
            ar_.scalars.return_value.all.return_value = [_admin()]
            db.execute = AsyncMock(side_effect=[or_, ar_])
            db.flush = AsyncMock()
            added = []
            db.add = lambda obj: added.append(obj)
            await select_workspace(body=SelectWorkspaceRequest(ref="r", org_id=1), db=db)
        rows = [r for r in added if isinstance(r, PortalUser)]
        assert len(rows) == 1
        assert rows[0].role == "member"
        assert rows[0].status == "active"


class TestJoinRequestPath:
    @pytest.mark.asyncio
    async def test_no_auto_accept_returns_join_request_pending(self):
        from app.api.auth_select import SelectWorkspaceRequest, select_workspace

        session = _ps(1, "domain_match", False)
        org = _org()
        with (
            patch("app.api.auth_select.pending_session_svc") as svc,
            patch("app.api.auth_select.zitadel"),
            patch("app.api.auth_select.emit_event"),
            patch("app.api.auth_select.notify_admin_join_request", AsyncMock()),
        ):
            svc.consume = AsyncMock(return_value=session)
            db = AsyncMock()
            db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=org)))
            result = await select_workspace(body=SelectWorkspaceRequest(ref="r", org_id=1), db=db)
        assert result.kind == "join_request_pending"
        assert result.redirect_to == "/join-request/sent"

    @pytest.mark.asyncio
    async def test_no_portal_users_row_for_join_request_path(self):
        from app.api.auth_select import SelectWorkspaceRequest, select_workspace
        from app.models.portal import PortalUser

        session = _ps(1, "domain_match", False)
        org = _org()
        with (
            patch("app.api.auth_select.pending_session_svc") as svc,
            patch("app.api.auth_select.zitadel"),
            patch("app.api.auth_select.emit_event"),
            patch("app.api.auth_select.notify_admin_join_request", AsyncMock()),
        ):
            svc.consume = AsyncMock(return_value=session)
            db = AsyncMock()
            db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=org)))
            added = []
            db.add = lambda obj: added.append(obj)
            await select_workspace(body=SelectWorkspaceRequest(ref="r", org_id=1), db=db)
        rows = [r for r in added if isinstance(r, PortalUser)]
        assert len(rows) == 0
