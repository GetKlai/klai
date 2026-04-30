from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestPendingEntrySchema:
    def test_pending_entry_importable(self) -> None:
        from app.services.pending_session import PendingEntry

        assert PendingEntry is not None

    def test_pending_entry_fields(self) -> None:
        from app.services.pending_session import PendingEntry

        ann = PendingEntry.__annotations__
        assert "org_id" in ann
        assert "name" in ann
        assert "slug" in ann
        assert "kind" in ann
        assert "auto_accept" in ann

    def test_pending_session_store_accepts_entries(self) -> None:
        import inspect

        from app.services.pending_session import PendingSessionService

        sig = inspect.signature(PendingSessionService.store)
        params = list(sig.parameters.keys())
        assert "entries" in params

    def test_pending_session_store_no_org_ids(self) -> None:
        import inspect

        from app.services.pending_session import PendingSessionService

        sig = inspect.signature(PendingSessionService.store)
        params = list(sig.parameters.keys())
        assert "org_ids" not in params


def _make_portal_user(org_id: int, org_name: str = "Acme", slug: str = "acme") -> MagicMock:
    u = MagicMock()
    u.org_id = org_id
    u.org = MagicMock()
    u.org.name = org_name
    u.org.slug = slug
    return u


def _make_portal_org(
    org_id: int, name: str = "Acme", slug: str = "acme", primary_domain: str = "acme.nl", auto_accept: bool = False
) -> MagicMock:
    org = MagicMock()
    org.id = org_id
    org.name = name
    org.slug = slug
    org.primary_domain = primary_domain
    org.auto_accept_same_domain = auto_accept
    return org


def _zitadel_mocks(email: str = "test@acme.nl") -> MagicMock:
    zit = MagicMock()
    zit.create_session_with_idp_intent = AsyncMock(return_value={"sessionId": "sid", "sessionToken": "stk"})
    zit.get_session_details = AsyncMock(return_value={"zitadel_user_id": "zuser1", "email": email})
    zit.finalize_auth_request = AsyncMock(return_value="https://acme.getklai.com/callback")
    return zit


class TestIdpCallbackCase1NoMemberNoDomain:
    @pytest.mark.asyncio
    async def test_redirects_to_no_account(self) -> None:
        from app.api.auth import idp_callback

        mr = MagicMock()
        mr.scalars.return_value.all.return_value = []
        dr = MagicMock()
        dr.scalars.return_value.all.return_value = []
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=[mr, dr])
        with patch("app.api.auth.zitadel", _zitadel_mocks()), patch("app.api.auth.emit_event"):
            response = await idp_callback(id="intent-1", token="tok-1", auth_request_id="ar-1", db=mock_db)
        assert response.status_code == 302
        assert "/no-account" in response.headers.get("location", "")


class TestIdpCallbackCase2SingleMemberNoDomain:
    @pytest.mark.asyncio
    async def test_single_member_finalizes_directly(self) -> None:
        from app.api.auth import idp_callback

        mr = MagicMock()
        mr.scalars.return_value.all.return_value = [_make_portal_user(1)]
        dr = MagicMock()
        dr.scalars.return_value.all.return_value = []
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=[mr, dr])
        with patch("app.api.auth.zitadel", _zitadel_mocks()), patch("app.api.auth.emit_event"):
            response = await idp_callback(id="intent-1", token="tok-1", auth_request_id="ar-1", db=mock_db)
        assert response.status_code == 302
        assert "acme.getklai.com" in response.headers.get("location", "")
        assert "klai_sso" in response.headers.get("set-cookie", "")


class TestIdpCallbackCase3SingleDomainMatch:
    @pytest.mark.asyncio
    async def test_redirects_to_select_workspace(self) -> None:
        from app.api.auth import idp_callback

        mr = MagicMock()
        mr.scalars.return_value.all.return_value = []
        dr = MagicMock()
        dr.scalars.return_value.all.return_value = [_make_portal_org(7)]
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=[mr, dr])
        with (
            patch("app.api.auth.zitadel", _zitadel_mocks()),
            patch("app.api.auth.emit_event"),
            patch("app.api.auth.PendingSessionService") as MockSvc,
        ):
            inst = AsyncMock()
            inst.store = AsyncMock(return_value="test-ref-uuid")
            MockSvc.return_value = inst
            response = await idp_callback(id="intent-1", token="tok-1", auth_request_id="ar-1", db=mock_db)
        assert response.status_code == 302
        loc = response.headers.get("location", "")
        assert "/select-workspace" in loc
        assert "ref=test-ref-uuid" in loc

    @pytest.mark.asyncio
    async def test_entry_has_domain_match_kind(self) -> None:
        from app.api.auth import idp_callback

        mr = MagicMock()
        mr.scalars.return_value.all.return_value = []
        dr = MagicMock()
        dr.scalars.return_value.all.return_value = [_make_portal_org(7, auto_accept=False)]
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=[mr, dr])
        with (
            patch("app.api.auth.zitadel", _zitadel_mocks()),
            patch("app.api.auth.emit_event"),
            patch("app.api.auth.PendingSessionService") as MockSvc,
        ):
            inst = AsyncMock()
            inst.store = AsyncMock(return_value="r")
            MockSvc.return_value = inst
            await idp_callback(id="intent-1", token="tok-1", auth_request_id="ar-1", db=mock_db)
        kw = inst.store.call_args.kwargs
        entries = kw.get("entries", [])
        assert len(entries) == 1
        assert entries[0]["kind"] == "domain_match"
        assert entries[0]["org_id"] == 7
        assert entries[0]["auto_accept"] is False


class TestIdpCallbackCase4Multiple:
    @pytest.mark.asyncio
    async def test_two_member_orgs_uses_picker(self) -> None:
        from app.api.auth import idp_callback

        mr = MagicMock()
        mr.scalars.return_value.all.return_value = [_make_portal_user(1), _make_portal_user(2, "Pinger", "pinger")]
        dr = MagicMock()
        dr.scalars.return_value.all.return_value = []
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=[mr, dr])
        with (
            patch("app.api.auth.zitadel", _zitadel_mocks()),
            patch("app.api.auth.emit_event"),
            patch("app.api.auth.PendingSessionService") as MockSvc,
        ):
            inst = AsyncMock()
            inst.store = AsyncMock(return_value="multi")
            MockSvc.return_value = inst
            response = await idp_callback(id="intent-1", token="tok-1", auth_request_id="ar-1", db=mock_db)
        assert response.status_code == 302
        assert "/select-workspace" in response.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_member_and_domain_entries_merged(self) -> None:
        from app.api.auth import idp_callback

        mr = MagicMock()
        mr.scalars.return_value.all.return_value = [_make_portal_user(1, "Acme", "acme")]
        dr = MagicMock()
        dr.scalars.return_value.all.return_value = [_make_portal_org(2, "Voys", "voys", "acme.nl", True)]
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=[mr, dr])
        with (
            patch("app.api.auth.zitadel", _zitadel_mocks("test@acme.nl")),
            patch("app.api.auth.emit_event"),
            patch("app.api.auth.PendingSessionService") as MockSvc,
        ):
            inst = AsyncMock()
            inst.store = AsyncMock(return_value="mixed")
            MockSvc.return_value = inst
            await idp_callback(id="intent-1", token="tok-1", auth_request_id="ar-1", db=mock_db)
        kw = inst.store.call_args.kwargs
        entries = kw.get("entries", [])
        kinds = {e["kind"] for e in entries}
        assert "member" in kinds
        assert "domain_match" in kinds
        assert len(entries) == 2
        de = next(e for e in entries if e["kind"] == "domain_match")
        assert de["auto_accept"] is True
