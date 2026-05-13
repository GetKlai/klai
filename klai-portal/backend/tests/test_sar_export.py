"""
Tests for POST /api/me/sar-export (AVG Art. 15 Subject Access Request).

Pure unit tests — no real DB, all async sessions are mocked.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _mock_org() -> MagicMock:
    org = MagicMock()
    org.id = 1
    org.moneybird_contact_id = "mb-123"
    org.librechat_container = "librechat-test"
    return org


def _mock_portal_user() -> MagicMock:
    user = MagicMock()
    user.role = "member"
    user.status = "active"
    user.preferred_language = "nl"
    user.github_username = None
    user.display_name = "Test User"
    user.email = "test@example.com"
    user.kb_retrieval_enabled = True
    user.kb_personal_enabled = True
    user.kb_slugs_filter = None
    user.created_at = datetime(2024, 1, 1, tzinfo=UTC)
    user.librechat_user_id = None
    return user


def _zitadel_user_response() -> dict:
    return {
        "user": {
            "human": {
                "profile": {
                    "firstName": "Test",
                    "lastName": "User",
                    "displayName": "Test User",
                },
                "email": {"email": "test@example.com"},
            },
            "details": {"creationDate": "2024-01-01T00:00:00Z"},
        }
    }


class TestSarExport:
    @pytest.fixture(autouse=True)
    def _stub_set_tenant(self, monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
        """set_tenant uses db.execute internally; stub it so tests keep
        controlling the side_effect list of canned query results.
        The real call is covered by test_sets_tenant_before_rls_queries.
        """
        stub = AsyncMock()
        monkeypatch.setattr("app.api.me.set_tenant", stub)
        return stub

    @pytest.fixture(autouse=True)
    def _stub_rate_limit(self, monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
        redis_pool = MagicMock()
        monkeypatch.setattr("app.api.me.get_redis_pool", AsyncMock(return_value=redis_pool))
        stub = AsyncMock(return_value=(True, 0))
        monkeypatch.setattr("app.api.me.check_rate_limit", stub)
        return stub

    @pytest.fixture(autouse=True)
    def _stub_external_sources(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.api.me._load_twenty_records", AsyncMock(return_value=[]))
        monkeypatch.setattr("app.api.me._write_sar_audit", AsyncMock())

    @pytest.mark.asyncio
    async def test_sets_tenant_before_rls_queries(self, _stub_set_tenant: AsyncMock) -> None:
        """Regression: without set_tenant the RLS-strict tables queried below
        (portal_groups, portal_knowledge_bases, portal_user_kb_access,
        vexa_meetings) raise InsufficientPrivilegeError on production
        PostgreSQL. This test asserts that set_tenant(db, org.id) fires
        once — before any RLS-protected query.
        """
        from app.api.me import sar_export

        org = _mock_org()
        org.id = 8
        portal_user = _mock_portal_user()

        mock_org_user = MagicMock()
        mock_org_user.one_or_none.return_value = (org, portal_user)
        mock_empty = MagicMock()
        mock_empty.all.return_value = []
        mock_meetings = MagicMock()
        mock_meetings.scalars.return_value.all.return_value = []

        db = AsyncMock()
        db.execute.side_effect = [
            mock_org_user,  # portal_users + portal_orgs lookup (permissive)
            mock_empty,  # group memberships
            mock_empty,  # KB access
            mock_empty,  # audit events
            mock_empty,  # usage events
            mock_meetings,  # meetings
        ]

        with patch("app.api.me.zitadel") as mock_zitadel:
            mock_zitadel.get_userinfo = AsyncMock(return_value={"sub": "user-xyz"})
            mock_zitadel.get_user_by_id = AsyncMock(return_value=_zitadel_user_response())
            mock_zitadel.has_any_mfa = AsyncMock(return_value=False)

            await sar_export(credentials=MagicMock(), db=db)

        _stub_set_tenant.assert_called_once()
        args, _ = _stub_set_tenant.call_args
        assert args[1] == 8, f"set_tenant must carry the real org_id; got {args[1]!r}"

    @pytest.mark.asyncio
    async def test_returns_expected_top_level_keys(self) -> None:
        from app.api.me import sar_export

        org = _mock_org()
        portal_user = _mock_portal_user()

        # Call 1: PortalOrg + PortalUser join query
        mock_result_org_user = MagicMock()
        mock_result_org_user.one_or_none.return_value = (org, portal_user)

        # Calls 2-5: empty .all() for group memberships, KB access, audit, usage events
        mock_result_empty = MagicMock()
        mock_result_empty.all.return_value = []

        # Call 6: meetings — .scalars().all()
        mock_result_meetings = MagicMock()
        mock_result_meetings.scalars.return_value.all.return_value = []

        db = AsyncMock()
        db.execute.side_effect = [
            mock_result_org_user,
            mock_result_empty,  # group memberships
            mock_result_empty,  # KB access
            mock_result_empty,  # audit events
            mock_result_empty,  # usage events
            mock_result_meetings,  # meetings
        ]

        mock_credentials = MagicMock()

        with patch("app.api.me.zitadel") as mock_zitadel:
            mock_zitadel.get_userinfo = AsyncMock(return_value={"sub": "user-123"})
            mock_zitadel.get_user_by_id = AsyncMock(return_value=_zitadel_user_response())
            mock_zitadel.has_any_mfa = AsyncMock(return_value=False)

            result = await sar_export(credentials=mock_credentials, db=db)

        result_dict = result.model_dump()

        assert result_dict["request_user_id"] == "user-123"
        assert "generated_at" in result_dict

        portal = result_dict["klai_portal"]
        assert "identity" in portal
        assert "account" in portal
        assert "group_memberships" in portal
        assert "knowledge_base_access" in portal
        assert "audit_events" in portal
        assert "usage_events" in portal
        assert "meetings" in portal
        assert "librechat_conversations" in portal

        ext = result_dict["external_systems"]
        assert "moneybird" in ext
        assert "librechat" in ext
        assert "twenty_crm" in ext
        assert ext["twenty_crm"]["records"] == []

    @pytest.mark.asyncio
    async def test_identity_includes_mfa_status(self) -> None:
        from app.api.me import sar_export

        org = _mock_org()
        portal_user = _mock_portal_user()

        mock_result_org_user = MagicMock()
        mock_result_org_user.one_or_none.return_value = (org, portal_user)
        mock_result_empty = MagicMock()
        mock_result_empty.all.return_value = []
        mock_result_meetings = MagicMock()
        mock_result_meetings.scalars.return_value.all.return_value = []

        db = AsyncMock()
        db.execute.side_effect = [
            mock_result_org_user,
            mock_result_empty,  # group memberships
            mock_result_empty,  # KB access
            mock_result_empty,  # audit events
            mock_result_empty,  # usage events
            mock_result_meetings,  # meetings
        ]

        mock_credentials = MagicMock()

        with patch("app.api.me.zitadel") as mock_zitadel:
            mock_zitadel.get_userinfo = AsyncMock(return_value={"sub": "user-456"})
            mock_zitadel.get_user_by_id = AsyncMock(return_value=_zitadel_user_response())
            mock_zitadel.has_any_mfa = AsyncMock(return_value=True)

            result = await sar_export(credentials=mock_credentials, db=db)

        assert result.klai_portal.identity.mfa_enrolled is True

    @pytest.mark.asyncio
    async def test_external_systems_include_moneybird_contact_id(self) -> None:
        from app.api.me import sar_export

        org = _mock_org()  # moneybird_contact_id = "mb-123"
        portal_user = _mock_portal_user()

        mock_result_org_user = MagicMock()
        mock_result_org_user.one_or_none.return_value = (org, portal_user)
        mock_result_empty = MagicMock()
        mock_result_empty.all.return_value = []
        mock_result_meetings = MagicMock()
        mock_result_meetings.scalars.return_value.all.return_value = []

        db = AsyncMock()
        db.execute.side_effect = [
            mock_result_org_user,
            mock_result_empty,  # group memberships
            mock_result_empty,  # KB access
            mock_result_empty,  # audit events
            mock_result_empty,  # usage events
            mock_result_meetings,  # meetings
        ]

        mock_credentials = MagicMock()

        with patch("app.api.me.zitadel") as mock_zitadel:
            mock_zitadel.get_userinfo = AsyncMock(return_value={"sub": "user-789"})
            mock_zitadel.get_user_by_id = AsyncMock(return_value=_zitadel_user_response())
            mock_zitadel.has_any_mfa = AsyncMock(return_value=False)

            result = await sar_export(credentials=mock_credentials, db=db)

        assert result.external_systems.moneybird.contact_id == "mb-123"
        assert "mb-123" in result.external_systems.moneybird.note

    @pytest.mark.asyncio
    async def test_user_not_found_returns_404(self) -> None:
        from app.api.me import sar_export

        mock_result_no_user = MagicMock()
        mock_result_no_user.one_or_none.return_value = None

        db = AsyncMock()
        db.execute.side_effect = [mock_result_no_user]

        mock_credentials = MagicMock()

        with patch("app.api.me.zitadel") as mock_zitadel:
            mock_zitadel.get_userinfo = AsyncMock(return_value={"sub": "user-unknown"})

            with pytest.raises(HTTPException) as exc_info:
                await sar_export(credentials=mock_credentials, db=db)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "User not found"

    @pytest.mark.asyncio
    async def test_zitadel_identity_fetch_fails_graceful_degradation(self) -> None:
        from app.api.me import sar_export

        org = _mock_org()
        portal_user = _mock_portal_user()

        mock_result_org_user = MagicMock()
        mock_result_org_user.one_or_none.return_value = (org, portal_user)
        mock_result_empty = MagicMock()
        mock_result_empty.all.return_value = []
        mock_result_meetings = MagicMock()
        mock_result_meetings.scalars.return_value.all.return_value = []

        db = AsyncMock()
        db.execute.side_effect = [
            mock_result_org_user,
            mock_result_empty,  # group memberships
            mock_result_empty,  # KB access
            mock_result_empty,  # audit events
            mock_result_empty,  # usage events
            mock_result_meetings,  # meetings
        ]

        mock_credentials = MagicMock()

        with patch("app.api.me.zitadel") as mock_zitadel:
            mock_zitadel.get_userinfo = AsyncMock(return_value={"sub": "user-degrade"})
            mock_zitadel.get_user_by_id = AsyncMock(side_effect=Exception("Zitadel timeout"))
            mock_zitadel.has_any_mfa = AsyncMock(return_value=False)

            result = await sar_export(credentials=mock_credentials, db=db)

        # Identity fields are None when Zitadel call fails (graceful degradation)
        assert result.klai_portal.identity.first_name is None
        assert result.klai_portal.identity.last_name is None
        assert result.klai_portal.identity.display_name is None
        assert result.klai_portal.identity.email is None
        assert result.klai_portal.identity.created_at is None

        # Account and other portal data is still populated normally
        assert result.klai_portal.account.role == "member"
        assert result.klai_portal.account.preferred_language == "nl"
        assert result.klai_portal.group_memberships == []
        assert result.klai_portal.meetings == []

    @pytest.mark.asyncio
    async def test_rate_limit_exceeded_returns_429_and_audits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.api import me as me_module

        org = _mock_org()
        portal_user = _mock_portal_user()

        mock_result_org_user = MagicMock()
        mock_result_org_user.one_or_none.return_value = (org, portal_user)

        db = AsyncMock()
        db.execute.side_effect = [mock_result_org_user]

        audit_stub = AsyncMock()
        monkeypatch.setattr(me_module, "_write_sar_audit", audit_stub)
        monkeypatch.setattr(me_module, "check_rate_limit", AsyncMock(return_value=(False, 123)))

        with patch("app.api.me.zitadel") as mock_zitadel:
            mock_zitadel.get_userinfo = AsyncMock(return_value={"sub": "user-limited"})

            with pytest.raises(HTTPException) as exc_info:
                await me_module.sar_export(credentials=MagicMock(), db=db)

        assert exc_info.value.status_code == 429
        assert exc_info.value.headers == {"Retry-After": "123"}
        audit_stub.assert_awaited_once_with(org.id, "user-limited", "sar.rate_limited")

    @pytest.mark.asyncio
    async def test_successful_export_writes_audit_entry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.api import me as me_module

        org = _mock_org()
        portal_user = _mock_portal_user()

        mock_result_org_user = MagicMock()
        mock_result_org_user.one_or_none.return_value = (org, portal_user)
        mock_result_empty = MagicMock()
        mock_result_empty.all.return_value = []
        mock_result_meetings = MagicMock()
        mock_result_meetings.scalars.return_value.all.return_value = []

        db = AsyncMock()
        db.execute.side_effect = [
            mock_result_org_user,
            mock_result_empty,
            mock_result_empty,
            mock_result_empty,
            mock_result_empty,
            mock_result_meetings,
        ]

        audit_stub = AsyncMock()
        monkeypatch.setattr(me_module, "_write_sar_audit", audit_stub)

        with patch("app.api.me.zitadel") as mock_zitadel:
            mock_zitadel.get_userinfo = AsyncMock(return_value={"sub": "user-audit"})
            mock_zitadel.get_user_by_id = AsyncMock(return_value=_zitadel_user_response())
            mock_zitadel.has_any_mfa = AsyncMock(return_value=False)

            await me_module.sar_export(credentials=MagicMock(), db=db)

        audit_stub.assert_awaited_once_with(org.id, "user-audit", "sar.exported")

    @pytest.mark.asyncio
    async def test_twenty_records_are_included_when_lookup_matches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.api import me as me_module

        org = _mock_org()
        portal_user = _mock_portal_user()

        mock_result_org_user = MagicMock()
        mock_result_org_user.one_or_none.return_value = (org, portal_user)
        mock_result_empty = MagicMock()
        mock_result_empty.all.return_value = []
        mock_result_meetings = MagicMock()
        mock_result_meetings.scalars.return_value.all.return_value = []

        db = AsyncMock()
        db.execute.side_effect = [
            mock_result_org_user,
            mock_result_empty,
            mock_result_empty,
            mock_result_empty,
            mock_result_empty,
            mock_result_meetings,
        ]

        monkeypatch.setattr(
            me_module,
            "_load_twenty_records",
            AsyncMock(
                return_value=[
                    me_module.SarTwentyCRMRecord(
                        first_name="Test",
                        last_name="User",
                        email="test@example.com",
                        company_name="Acme",
                    )
                ]
            ),
        )

        with patch("app.api.me.zitadel") as mock_zitadel:
            mock_zitadel.get_userinfo = AsyncMock(return_value={"sub": "user-twenty"})
            mock_zitadel.get_user_by_id = AsyncMock(return_value=_zitadel_user_response())
            mock_zitadel.has_any_mfa = AsyncMock(return_value=False)

            result = await me_module.sar_export(credentials=MagicMock(), db=db)

        assert result.external_systems.twenty_crm.records is not None
        assert result.external_systems.twenty_crm.records[0].company_name == "Acme"
