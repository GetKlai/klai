"""RED: Verify admin integration endpoints (SPEC-API-001 REQ-6).

TASK-012: POST /api/integrations, GET /api/integrations
TASK-013: GET /api/integrations/{id}, PATCH /api/integrations/{id}, POST /api/integrations/{id}/revoke
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db():
    """Mock async DB session with transaction support."""
    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.add = MagicMock()
    db.delete = AsyncMock()
    return db


@pytest.fixture
def mock_credentials():
    """Mock OIDC credentials."""
    creds = MagicMock()
    creds.credentials = "fake-oidc-token"
    return creds


@pytest.fixture
def admin_user():
    """Mock admin PortalUser."""
    user = MagicMock()
    user.role = "admin"
    user.zitadel_user_id = "admin-user-123"
    return user


@pytest.fixture
def member_user():
    """Mock non-admin PortalUser."""
    user = MagicMock()
    user.role = "member"
    user.zitadel_user_id = "member-user-456"
    return user


@pytest.fixture
def mock_org():
    """Mock PortalOrg."""
    org = MagicMock()
    org.id = 42
    org.zitadel_org_id = "zit-org-1"
    return org


@pytest.fixture
def valid_create_body():
    """Valid CreateIntegrationRequest data."""
    from app.api.admin_integrations import CreateIntegrationRequest

    return CreateIntegrationRequest(
        name="Test Partner",
        description="A test integration",
        permissions={"chat": True, "feedback": True, "knowledge_append": False},
        kb_access=[{"kb_id": 1, "access_level": "read"}],
        rate_limit_rpm=60,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_scalar_result(value):
    """Create a mock result that returns value from scalar_one_or_none."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _mock_scalars_result(values):
    """Create a mock result that returns values from scalars().all()."""
    result = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = values
    result.scalars.return_value = scalars_mock
    return result


# ===========================================================================
# TASK-012: POST /api/integrations
# ===========================================================================


class TestCreateIntegration:
    """POST /api/integrations — REQ-6.2."""

    @pytest.mark.asyncio
    async def test_non_admin_returns_403(self, mock_db, mock_credentials, member_user, mock_org):
        """REQ-6.1: Non-admin user gets 403."""
        from app.api.admin_integrations import CreateIntegrationRequest, create_integration

        body = CreateIntegrationRequest(
            name="Test",
            permissions={"chat": True, "feedback": False, "knowledge_append": False},
            kb_access=[],
            rate_limit_rpm=60,
        )

        with patch("app.api.admin_integrations._get_caller_org", return_value=("user-id", mock_org, member_user)):
            with pytest.raises(HTTPException) as exc_info:
                await create_integration(body=body, credentials=mock_credentials, db=mock_db)
            assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_create_happy_path_returns_plaintext_key(
        self, mock_db, mock_credentials, admin_user, mock_org
    ):
        """REQ-6.2: Create returns plaintext key + metadata."""
        from app.api.admin_integrations import CreateIntegrationRequest, create_integration

        # Mock KB validation: kb_id=1 belongs to org
        mock_kb = MagicMock()
        mock_kb.id = 1
        mock_kb.name = "Test KB"

        mock_db.execute = AsyncMock(
            side_effect=[
                # First call: KB validation query
                _mock_scalars_result([mock_kb]),
                # Remaining calls for inserts
                MagicMock(),
                MagicMock(),
            ]
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

        # Must return plaintext key starting with pk_live_
        assert hasattr(result, "api_key")
        assert result.api_key.startswith("pk_live_")
        assert result.name == "Test Partner"
        assert result.active is True
        assert result.key_prefix.startswith("pk_live_")
        assert len(result.key_prefix) == 12

    @pytest.mark.asyncio
    async def test_create_with_out_of_org_kb_returns_400(
        self, mock_db, mock_credentials, admin_user, mock_org
    ):
        """REQ-6.2: KB not in org -> 400."""
        from app.api.admin_integrations import CreateIntegrationRequest, create_integration

        # Mock: no KBs found for org (kb_id=999 doesn't belong)
        mock_db.execute = AsyncMock(return_value=_mock_scalars_result([]))

        body = CreateIntegrationRequest(
            name="Test Partner",
            permissions={"chat": True, "feedback": False, "knowledge_append": False},
            kb_access=[{"kb_id": 999, "access_level": "read"}],
            rate_limit_rpm=60,
        )

        with patch("app.api.admin_integrations._get_caller_org", return_value=("admin-user-123", mock_org, admin_user)):
            with pytest.raises(HTTPException) as exc_info:
                await create_integration(body=body, credentials=mock_credentials, db=mock_db)
            assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_create_knowledge_append_without_read_write_returns_400(
        self, mock_db, mock_credentials, admin_user, mock_org
    ):
        """knowledge_append=true but no KBs with read_write -> 400."""
        from app.api.admin_integrations import CreateIntegrationRequest, create_integration

        mock_kb = MagicMock()
        mock_kb.id = 1
        mock_db.execute = AsyncMock(return_value=_mock_scalars_result([mock_kb]))

        body = CreateIntegrationRequest(
            name="Test Partner",
            permissions={"chat": True, "feedback": False, "knowledge_append": True},
            kb_access=[{"kb_id": 1, "access_level": "read"}],  # only read, not read_write
            rate_limit_rpm=60,
        )

        with patch("app.api.admin_integrations._get_caller_org", return_value=("admin-user-123", mock_org, admin_user)):
            with pytest.raises(HTTPException) as exc_info:
                await create_integration(body=body, credentials=mock_credentials, db=mock_db)
            assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_create_emits_product_event(
        self, mock_db, mock_credentials, admin_user, mock_org
    ):
        """REQ-6.7: integration.created event emitted."""
        from app.api.admin_integrations import CreateIntegrationRequest, create_integration

        mock_kb = MagicMock()
        mock_kb.id = 1
        mock_db.execute = AsyncMock(
            side_effect=[
                _mock_scalars_result([mock_kb]),
                MagicMock(),
                MagicMock(),
            ]
        )

        body = CreateIntegrationRequest(
            name="Event Test",
            permissions={"chat": True, "feedback": False, "knowledge_append": False},
            kb_access=[{"kb_id": 1, "access_level": "read"}],
            rate_limit_rpm=60,
        )

        with (
            patch("app.api.admin_integrations._get_caller_org", return_value=("admin-user-123", mock_org, admin_user)),
            patch("app.api.admin_integrations.emit_event") as mock_emit,
        ):
            await create_integration(body=body, credentials=mock_credentials, db=mock_db)

        mock_emit.assert_called_once()
        call_args = mock_emit.call_args
        assert call_args[0][0] == "integration.created"
        assert call_args[1]["org_id"] == 42
        assert call_args[1]["user_id"] == "admin-user-123"


# ===========================================================================
# TASK-012: GET /api/integrations
# ===========================================================================


class TestListIntegrations:
    """GET /api/integrations — REQ-6.3."""

    @pytest.mark.asyncio
    async def test_non_admin_returns_403(self, mock_db, mock_credentials, member_user, mock_org):
        """REQ-6.1: Non-admin user gets 403."""
        from app.api.admin_integrations import list_integrations

        with patch("app.api.admin_integrations._get_caller_org", return_value=("user-id", mock_org, member_user)):
            with pytest.raises(HTTPException) as exc_info:
                await list_integrations(credentials=mock_credentials, db=mock_db)
            assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_list_returns_entries_without_plaintext_key(
        self, mock_db, mock_credentials, admin_user, mock_org
    ):
        """REQ-6.3: List returns metadata without plaintext key."""
        from app.api.admin_integrations import list_integrations

        # Mock a partner key row
        key_row = MagicMock()
        key_row.id = "uuid-1"
        key_row.name = "Partner A"
        key_row.description = "Desc"
        key_row.key_prefix = "pk_live_abcd"
        key_row.permissions = {"chat": True, "feedback": False, "knowledge_append": False}
        key_row.active = True
        key_row.rate_limit_rpm = 60
        key_row.last_used_at = None
        key_row.created_at = "2026-01-01T00:00:00Z"
        key_row.created_by = "admin-user-123"

        # Mock KB access count
        kb_access_row = MagicMock()
        kb_access_row.partner_api_key_id = "uuid-1"
        kb_access_row.kb_id = 1
        kb_access_row.access_level = "read"

        mock_db.execute = AsyncMock(
            side_effect=[
                # First: list keys
                _mock_scalars_result([key_row]),
                # Second: KB access entries
                _mock_scalars_result([kb_access_row]),
            ]
        )

        with patch("app.api.admin_integrations._get_caller_org", return_value=("admin-user-123", mock_org, admin_user)):
            result = await list_integrations(credentials=mock_credentials, db=mock_db)

        assert len(result) == 1
        entry = result[0]
        assert entry.name == "Partner A"
        assert entry.key_prefix == "pk_live_abcd"
        # Must NOT have api_key attribute with plaintext
        assert not hasattr(entry, "api_key")
        assert entry.kb_access_count == 1


# ===========================================================================
# TASK-013: GET /api/integrations/{id}
# ===========================================================================


class TestGetIntegrationDetail:
    """GET /api/integrations/{id} — REQ-6.4."""

    @pytest.mark.asyncio
    async def test_detail_returns_full_kb_list_with_names(
        self, mock_db, mock_credentials, admin_user, mock_org
    ):
        """REQ-6.4: Detail returns per-KB access list with KB names."""
        from app.api.admin_integrations import get_integration_detail

        key_row = MagicMock()
        key_row.id = "uuid-1"
        key_row.org_id = 42
        key_row.name = "Partner A"
        key_row.description = "Desc"
        key_row.key_prefix = "pk_live_abcd"
        key_row.permissions = {"chat": True, "feedback": False, "knowledge_append": False}
        key_row.active = True
        key_row.rate_limit_rpm = 60
        key_row.last_used_at = None
        key_row.created_at = "2026-01-01T00:00:00Z"
        key_row.created_by = "admin-user-123"

        kb_access_entry = MagicMock()
        kb_access_entry.kb_id = 1
        kb_access_entry.access_level = "read"

        kb_model = MagicMock()
        kb_model.id = 1
        kb_model.name = "Sales KB"
        kb_model.slug = "sales"

        mock_db.execute = AsyncMock(
            side_effect=[
                # Key lookup
                _mock_scalar_result(key_row),
                # KB access entries
                _mock_scalars_result([kb_access_entry]),
                # KB details lookup
                _mock_scalars_result([kb_model]),
            ]
        )

        with patch("app.api.admin_integrations._get_caller_org", return_value=("admin-user-123", mock_org, admin_user)):
            result = await get_integration_detail(
                integration_id="uuid-1", credentials=mock_credentials, db=mock_db
            )

        assert result.name == "Partner A"
        assert len(result.kb_access) == 1
        assert result.kb_access[0]["kb_id"] == 1
        assert result.kb_access[0]["kb_name"] == "Sales KB"
        assert result.kb_access[0]["access_level"] == "read"

    @pytest.mark.asyncio
    async def test_detail_not_found_returns_404(
        self, mock_db, mock_credentials, admin_user, mock_org
    ):
        """Integration not in org -> 404."""
        from app.api.admin_integrations import get_integration_detail

        mock_db.execute = AsyncMock(return_value=_mock_scalar_result(None))

        with patch("app.api.admin_integrations._get_caller_org", return_value=("admin-user-123", mock_org, admin_user)):
            with pytest.raises(HTTPException) as exc_info:
                await get_integration_detail(
                    integration_id="nonexistent", credentials=mock_credentials, db=mock_db
                )
            assert exc_info.value.status_code == 404


# ===========================================================================
# TASK-013: PATCH /api/integrations/{id}
# ===========================================================================


class TestUpdateIntegration:
    """PATCH /api/integrations/{id} — REQ-6.5."""

    @pytest.mark.asyncio
    async def test_patch_partial_fields_works(
        self, mock_db, mock_credentials, admin_user, mock_org
    ):
        """REQ-6.5: Partial update of name/description."""
        from app.api.admin_integrations import UpdateIntegrationRequest, update_integration

        key_row = MagicMock()
        key_row.id = "uuid-1"
        key_row.org_id = 42
        key_row.name = "Old Name"
        key_row.description = "Old Desc"
        key_row.key_prefix = "pk_live_abcd"
        key_row.permissions = {"chat": True, "feedback": False, "knowledge_append": False}
        key_row.active = True
        key_row.rate_limit_rpm = 60
        key_row.last_used_at = None
        key_row.created_at = "2026-01-01T00:00:00Z"
        key_row.created_by = "admin-user-123"

        mock_db.execute = AsyncMock(return_value=_mock_scalar_result(key_row))

        body = UpdateIntegrationRequest(name="New Name")

        with (
            patch("app.api.admin_integrations._get_caller_org", return_value=("admin-user-123", mock_org, admin_user)),
            patch("app.api.admin_integrations.emit_event") as mock_emit,
        ):
            await update_integration(
                integration_id="uuid-1", body=body, credentials=mock_credentials, db=mock_db
            )

        assert key_row.name == "New Name"
        mock_emit.assert_called_once()
        assert mock_emit.call_args[0][0] == "integration.updated"

    @pytest.mark.asyncio
    async def test_patch_kb_access_replaces_rows_atomically(
        self, mock_db, mock_credentials, admin_user, mock_org
    ):
        """REQ-6.5: kb_access update deletes old rows and inserts new ones."""
        from app.api.admin_integrations import UpdateIntegrationRequest, update_integration

        key_row = MagicMock()
        key_row.id = "uuid-1"
        key_row.org_id = 42
        key_row.name = "Partner"
        key_row.description = None
        key_row.key_prefix = "pk_live_abcd"
        key_row.permissions = {"chat": True, "feedback": False, "knowledge_append": False}
        key_row.active = True
        key_row.rate_limit_rpm = 60
        key_row.last_used_at = None
        key_row.created_at = "2026-01-01T00:00:00Z"
        key_row.created_by = "admin-user-123"

        mock_kb = MagicMock()
        mock_kb.id = 2

        mock_db.execute = AsyncMock(
            side_effect=[
                # Key lookup
                _mock_scalar_result(key_row),
                # KB validation
                _mock_scalars_result([mock_kb]),
                # DELETE old kb_access
                MagicMock(),
                # (inserts happen via db.add)
            ]
        )

        body = UpdateIntegrationRequest(
            kb_access=[{"kb_id": 2, "access_level": "read_write"}]
        )

        with (
            patch("app.api.admin_integrations._get_caller_org", return_value=("admin-user-123", mock_org, admin_user)),
            patch("app.api.admin_integrations.emit_event"),
        ):
            await update_integration(
                integration_id="uuid-1", body=body, credentials=mock_credentials, db=mock_db
            )

        # db.add should have been called for the new kb_access row
        mock_db.add.assert_called()

    @pytest.mark.asyncio
    async def test_patch_revoked_key_active_true_returns_400(
        self, mock_db, mock_credentials, admin_user, mock_org
    ):
        """Cannot set active=true on a revoked key -> 400."""
        from app.api.admin_integrations import UpdateIntegrationRequest, update_integration

        key_row = MagicMock()
        key_row.id = "uuid-1"
        key_row.org_id = 42
        key_row.active = False  # revoked

        mock_db.execute = AsyncMock(return_value=_mock_scalar_result(key_row))

        # The SPEC says no setting active=true on revoked key
        # UpdateIntegrationRequest should not even support active field,
        # but we test that revoked keys cannot be reactivated via PATCH
        body = UpdateIntegrationRequest(name="Try Reactivate")

        with patch("app.api.admin_integrations._get_caller_org", return_value=("admin-user-123", mock_org, admin_user)):
            with pytest.raises(HTTPException) as exc_info:
                await update_integration(
                    integration_id="uuid-1", body=body, credentials=mock_credentials, db=mock_db
                )
            assert exc_info.value.status_code == 400


# ===========================================================================
# TASK-013: POST /api/integrations/{id}/revoke
# ===========================================================================


class TestRevokeIntegration:
    """POST /api/integrations/{id}/revoke — REQ-6.6."""

    @pytest.mark.asyncio
    async def test_revoke_sets_active_false(
        self, mock_db, mock_credentials, admin_user, mock_org
    ):
        """REQ-6.6: Revoke sets active=false."""
        from app.api.admin_integrations import revoke_integration

        key_row = MagicMock()
        key_row.id = "uuid-1"
        key_row.org_id = 42
        key_row.name = "Partner"
        key_row.description = None
        key_row.key_prefix = "pk_live_abcd"
        key_row.permissions = {"chat": True, "feedback": False, "knowledge_append": False}
        key_row.active = True
        key_row.rate_limit_rpm = 60
        key_row.last_used_at = None
        key_row.created_at = "2026-01-01T00:00:00Z"
        key_row.created_by = "admin-user-123"

        mock_db.execute = AsyncMock(return_value=_mock_scalar_result(key_row))

        with (
            patch("app.api.admin_integrations._get_caller_org", return_value=("admin-user-123", mock_org, admin_user)),
            patch("app.api.admin_integrations.emit_event") as mock_emit,
        ):
            await revoke_integration(
                integration_id="uuid-1", credentials=mock_credentials, db=mock_db
            )

        assert key_row.active is False
        mock_emit.assert_called_once()
        assert mock_emit.call_args[0][0] == "integration.revoked"

    @pytest.mark.asyncio
    async def test_revoke_already_revoked_returns_400(
        self, mock_db, mock_credentials, admin_user, mock_org
    ):
        """Revoking an already-revoked key -> 400."""
        from app.api.admin_integrations import revoke_integration

        key_row = MagicMock()
        key_row.id = "uuid-1"
        key_row.org_id = 42
        key_row.active = False  # already revoked

        mock_db.execute = AsyncMock(return_value=_mock_scalar_result(key_row))

        with patch("app.api.admin_integrations._get_caller_org", return_value=("admin-user-123", mock_org, admin_user)):
            with pytest.raises(HTTPException) as exc_info:
                await revoke_integration(
                    integration_id="uuid-1", credentials=mock_credentials, db=mock_db
                )
            assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_revoke_emits_product_event(
        self, mock_db, mock_credentials, admin_user, mock_org
    ):
        """REQ-6.7: integration.revoked event emitted."""
        from app.api.admin_integrations import revoke_integration

        key_row = MagicMock()
        key_row.id = "uuid-1"
        key_row.org_id = 42
        key_row.name = "Partner"
        key_row.description = None
        key_row.key_prefix = "pk_live_abcd"
        key_row.permissions = {"chat": True, "feedback": False, "knowledge_append": False}
        key_row.active = True
        key_row.rate_limit_rpm = 60
        key_row.last_used_at = None
        key_row.created_at = "2026-01-01T00:00:00Z"
        key_row.created_by = "admin-user-123"

        mock_db.execute = AsyncMock(return_value=_mock_scalar_result(key_row))

        with (
            patch("app.api.admin_integrations._get_caller_org", return_value=("admin-user-123", mock_org, admin_user)),
            patch("app.api.admin_integrations.emit_event") as mock_emit,
        ):
            await revoke_integration(
                integration_id="uuid-1", credentials=mock_credentials, db=mock_db
            )

        mock_emit.assert_called_once()
        assert mock_emit.call_args[0][0] == "integration.revoked"
        assert mock_emit.call_args[1]["org_id"] == 42
