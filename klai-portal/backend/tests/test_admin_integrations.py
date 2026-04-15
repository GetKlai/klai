"""Tests for admin integration endpoints (SPEC-API-001 REQ-6).

Uses a mock DB that auto-responds to all execute() calls with sensible defaults.
Tests verify behavior through the return value and side effects, not through
counting exact db.execute() calls.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db():
    """Mock async DB session that auto-responds to queries."""
    db = AsyncMock()
    db.commit = AsyncMock()
    db.add = MagicMock()
    db.refresh = AsyncMock()
    db.connection = AsyncMock()  # connection pinning
    return db


@pytest.fixture
def mock_credentials():
    creds = MagicMock()
    creds.credentials = "fake-oidc-token"
    return creds


@pytest.fixture
def admin_user():
    user = MagicMock()
    user.role = "admin"
    user.zitadel_user_id = "admin-user-123"
    return user


@pytest.fixture
def member_user():
    user = MagicMock()
    user.role = "member"
    user.zitadel_user_id = "member-user-456"
    return user


@pytest.fixture
def mock_org():
    org = MagicMock()
    org.id = 42
    org.zitadel_org_id = "zitadel-org-42"
    return org


def _make_key_row(**overrides):
    """Create a mock PartnerAPIKey row with sensible defaults."""
    row = MagicMock()
    defaults = {
        "id": "uuid-1",
        "org_id": 42,
        "name": "Partner",
        "description": None,
        "key_prefix": "pk_live_abcd",
        "permissions": {"chat": True, "feedback": False, "knowledge_append": False},
        "active": True,
        "rate_limit_rpm": 60,
        "last_used_at": None,
        "created_at": "2026-01-01T00:00:00Z",
        "created_by": "admin-user-123",
    }
    defaults.update(overrides)
    for k, v in defaults.items():
        setattr(row, k, v)
    return row


def _make_kb_row(kb_id=1, name="Test KB", slug="test-kb"):
    row = MagicMock()
    row.id = kb_id
    row.name = name
    row.slug = slug
    return row


class _FakeResult:
    """A mock DB result that responds to all common access patterns."""

    def __init__(self, rows=None, scalar_value=None):
        self._rows = rows or []
        self._scalar_value = scalar_value

    def scalars(self):
        mock = MagicMock()
        mock.all.return_value = self._rows
        return mock

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._scalar_value

    def fetchall(self):
        return self._rows

    def __iter__(self):
        return iter(self._rows)


def _setup_db(mock_db, results: list):
    """Set up mock_db.execute to return results in order, cycling the last one."""
    call_count = 0

    async def _execute(*args, **kwargs):
        nonlocal call_count
        idx = min(call_count, len(results) - 1)
        call_count += 1
        return results[idx] if results else _FakeResult()

    mock_db.execute = AsyncMock(side_effect=_execute)


# ---------------------------------------------------------------------------
# POST /api/integrations
# ---------------------------------------------------------------------------


class TestCreateIntegration:
    @pytest.mark.asyncio
    async def test_non_admin_returns_403(self, mock_db, mock_credentials, member_user, mock_org):
        from app.api.admin_integrations import CreateIntegrationRequest, create_integration

        body = CreateIntegrationRequest(
            name="Test",
            permissions={"chat": True},
            kb_access=[],
            rate_limit_rpm=60,
        )
        with patch("app.api.admin_integrations._get_caller_org", return_value=("u", mock_org, member_user)):
            with pytest.raises(HTTPException) as exc:
                await create_integration(body=body, credentials=mock_credentials, db=mock_db)
            assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_create_happy_path(self, mock_db, mock_credentials, admin_user, mock_org):
        from app.api.admin_integrations import CreateIntegrationRequest, create_integration

        kb = _make_kb_row()
        _setup_db(
            mock_db,
            [
                _FakeResult([kb]),  # _validate_kb_ids
                _FakeResult(),  # remaining queries (inserts, refresh, etc.)
            ],
        )

        body = CreateIntegrationRequest(
            name="Test Partner",
            permissions={"chat": True, "feedback": True, "knowledge_append": False},
            kb_access=[{"kb_id": 1, "access_level": "read"}],
            rate_limit_rpm=60,
        )
        with (
            patch("app.api.admin_integrations._get_caller_org", return_value=("admin-user-123", mock_org, admin_user)),
            patch("app.api.admin_integrations.emit_event"),
        ):
            result = await create_integration(body=body, credentials=mock_credentials, db=mock_db)

        assert result.api_key.startswith("pk_live_")
        assert result.name == "Test Partner"
        assert result.active is True
        assert len(result.key_prefix) == 12

    @pytest.mark.asyncio
    async def test_create_out_of_org_kb_returns_400(self, mock_db, mock_credentials, admin_user, mock_org):
        from app.api.admin_integrations import CreateIntegrationRequest, create_integration

        _setup_db(mock_db, [_FakeResult()])  # no KBs found

        body = CreateIntegrationRequest(
            name="Test",
            permissions={"chat": True},
            kb_access=[{"kb_id": 999, "access_level": "read"}],
            rate_limit_rpm=60,
        )
        with patch("app.api.admin_integrations._get_caller_org", return_value=("u", mock_org, admin_user)):
            with pytest.raises(HTTPException) as exc:
                await create_integration(body=body, credentials=mock_credentials, db=mock_db)
            assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_knowledge_append_without_read_write_returns_400(
        self, mock_db, mock_credentials, admin_user, mock_org
    ):
        from app.api.admin_integrations import CreateIntegrationRequest, create_integration

        kb = _make_kb_row()
        _setup_db(mock_db, [_FakeResult([kb])])

        body = CreateIntegrationRequest(
            name="Test",
            permissions={"chat": True, "knowledge_append": True},
            kb_access=[{"kb_id": 1, "access_level": "read"}],
            rate_limit_rpm=60,
        )
        with patch("app.api.admin_integrations._get_caller_org", return_value=("u", mock_org, admin_user)):
            with pytest.raises(HTTPException) as exc:
                await create_integration(body=body, credentials=mock_credentials, db=mock_db)
            assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_create_emits_event(self, mock_db, mock_credentials, admin_user, mock_org):
        from app.api.admin_integrations import CreateIntegrationRequest, create_integration

        kb = _make_kb_row()
        _setup_db(mock_db, [_FakeResult([kb]), _FakeResult()])

        body = CreateIntegrationRequest(
            name="Event Test",
            permissions={"chat": True},
            kb_access=[{"kb_id": 1, "access_level": "read"}],
            rate_limit_rpm=60,
        )
        with (
            patch("app.api.admin_integrations._get_caller_org", return_value=("admin-user-123", mock_org, admin_user)),
            patch("app.api.admin_integrations.emit_event") as mock_emit,
        ):
            await create_integration(body=body, credentials=mock_credentials, db=mock_db)

        mock_emit.assert_called_once()
        assert mock_emit.call_args[0][0] == "integration.created"


# ---------------------------------------------------------------------------
# GET /api/integrations
# ---------------------------------------------------------------------------


class TestListIntegrations:
    @pytest.mark.asyncio
    async def test_non_admin_returns_403(self, mock_db, mock_credentials, member_user, mock_org):
        from app.api.admin_integrations import list_integrations

        with patch("app.api.admin_integrations._get_caller_org", return_value=("u", mock_org, member_user)):
            with pytest.raises(HTTPException) as exc:
                await list_integrations(credentials=mock_credentials, db=mock_db)
            assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_list_returns_entries(self, mock_db, mock_credentials, admin_user, mock_org):
        from app.api.admin_integrations import list_integrations

        key_row = _make_key_row()
        count_row = MagicMock()
        count_row.partner_api_key_id = "uuid-1"
        count_row.cnt = 2

        _setup_db(
            mock_db,
            [
                _FakeResult([key_row]),  # list keys
                _FakeResult([count_row]),  # count query
            ],
        )

        with patch("app.api.admin_integrations._get_caller_org", return_value=("u", mock_org, admin_user)):
            result = await list_integrations(credentials=mock_credentials, db=mock_db)

        assert len(result) == 1
        assert result[0].name == "Partner"
        assert not hasattr(result[0], "api_key")


# ---------------------------------------------------------------------------
# PATCH /api/integrations/{id}
# ---------------------------------------------------------------------------


class TestUpdateIntegration:
    @pytest.mark.asyncio
    async def test_patch_partial_fields(self, mock_db, mock_credentials, admin_user, mock_org):
        from app.api.admin_integrations import UpdateIntegrationRequest, update_integration

        key_row = _make_key_row()
        _setup_db(
            mock_db,
            [
                _FakeResult([key_row]),  # key lookup
                _FakeResult(scalar_value=1),  # count query (fallback)
            ],
        )

        body = UpdateIntegrationRequest(name="New Name")
        with (
            patch("app.api.admin_integrations._get_caller_org", return_value=("admin-user-123", mock_org, admin_user)),
            patch("app.api.admin_integrations.emit_event"),
        ):
            await update_integration(integration_id="uuid-1", body=body, credentials=mock_credentials, db=mock_db)

        assert key_row.name == "New Name"

    @pytest.mark.asyncio
    async def test_patch_kb_access_replaces_atomically(self, mock_db, mock_credentials, admin_user, mock_org):
        from app.api.admin_integrations import UpdateIntegrationRequest, update_integration

        key_row = _make_key_row()
        kb = _make_kb_row(kb_id=2, name="Other KB")

        _setup_db(
            mock_db,
            [
                _FakeResult([key_row]),  # key lookup
                _FakeResult([kb]),  # kb validation
                _FakeResult(),  # delete + insert
            ],
        )

        body = UpdateIntegrationRequest(kb_access=[{"kb_id": 2, "access_level": "read_write"}])
        with (
            patch("app.api.admin_integrations._get_caller_org", return_value=("admin-user-123", mock_org, admin_user)),
            patch("app.api.admin_integrations.emit_event"),
        ):
            await update_integration(integration_id="uuid-1", body=body, credentials=mock_credentials, db=mock_db)

        mock_db.commit.assert_called()

    @pytest.mark.asyncio
    async def test_patch_revoked_key_returns_400(self, mock_db, mock_credentials, admin_user, mock_org):
        from app.api.admin_integrations import UpdateIntegrationRequest, update_integration

        key_row = _make_key_row(active=False)
        _setup_db(mock_db, [_FakeResult([key_row])])

        body = UpdateIntegrationRequest(name="Try Update")
        with patch("app.api.admin_integrations._get_caller_org", return_value=("u", mock_org, admin_user)):
            with pytest.raises(HTTPException) as exc:
                await update_integration(integration_id="uuid-1", body=body, credentials=mock_credentials, db=mock_db)
            assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/integrations/{id}/revoke
# ---------------------------------------------------------------------------


class TestRevokeIntegration:
    @pytest.mark.asyncio
    async def test_revoke_sets_active_false(self, mock_db, mock_credentials, admin_user, mock_org):
        from app.api.admin_integrations import revoke_integration

        key_row = _make_key_row()
        _setup_db(
            mock_db,
            [
                _FakeResult([key_row]),  # key lookup
                _FakeResult(scalar_value=1),  # count
            ],
        )

        with (
            patch("app.api.admin_integrations._get_caller_org", return_value=("admin-user-123", mock_org, admin_user)),
            patch("app.api.admin_integrations.emit_event") as mock_emit,
        ):
            await revoke_integration(integration_id="uuid-1", credentials=mock_credentials, db=mock_db)

        assert key_row.active is False
        mock_emit.assert_called_once()
        assert mock_emit.call_args[0][0] == "integration.revoked"

    @pytest.mark.asyncio
    async def test_revoke_already_revoked_returns_400(self, mock_db, mock_credentials, admin_user, mock_org):
        from app.api.admin_integrations import revoke_integration

        key_row = _make_key_row(active=False)
        _setup_db(mock_db, [_FakeResult([key_row])])

        with patch("app.api.admin_integrations._get_caller_org", return_value=("u", mock_org, admin_user)):
            with pytest.raises(HTTPException) as exc:
                await revoke_integration(integration_id="uuid-1", credentials=mock_credentials, db=mock_db)
            assert exc.value.status_code == 400
