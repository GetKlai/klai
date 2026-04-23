"""
Integration tests for SPEC-PORTAL-UNIFY-KB-001 R-E2: item quota enforcement
at the trigger_sync endpoint.

Verifies that POST /api/app/knowledge-bases/{kb_slug}/connectors/{id}/sync
returns 403 kb_quota_items_exceeded when the KB is already at the item limit.

The `connector_credentials` library is a shared package not installed in the
unit-test environment (it ships as a compiled wheel for production). We stub it
at sys.modules level before importing app.api.connectors so collection succeeds.
"""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

# ---------------------------------------------------------------------------
# Stub connector_credentials before the connectors module is imported.
# ---------------------------------------------------------------------------
_stub_mod = ModuleType("connector_credentials")
_stub_mod.SENSITIVE_FIELDS = {}  # type: ignore[attr-defined]
_stub_mod.ConnectorCredentialStore = MagicMock  # type: ignore[attr-defined]
_stub_cipher = ModuleType("connector_credentials.cipher")
_stub_cipher.AESGCMCipher = MagicMock  # type: ignore[attr-defined]
sys.modules.setdefault("connector_credentials", _stub_mod)
sys.modules.setdefault("connector_credentials.cipher", _stub_cipher)


def _make_org(plan: str = "core") -> MagicMock:
    org = MagicMock()
    org.plan = plan
    org.id = 1
    org.slug = "test-org"
    org.zitadel_org_id = "zitadel-org-1"
    return org


def _make_kb(owner_type: str = "user", slug: str = "my-kb", kb_id: int = 1) -> MagicMock:
    kb = MagicMock()
    kb.id = kb_id
    kb.slug = slug
    kb.owner_type = owner_type
    return kb


def _make_connector(connector_id: str = "conn-1", kb_id: int = 1, enabled: bool = True) -> MagicMock:
    connector = MagicMock()
    connector.id = connector_id
    connector.kb_id = kb_id
    connector.is_enabled = enabled
    connector.last_sync_status = "idle"
    connector.connector_type = "web_crawler"
    return connector


def _make_db(kb: MagicMock, connector: MagicMock) -> AsyncMock:
    """Build a minimal AsyncSession mock that returns kb then connector on successive execute calls."""
    db = AsyncMock()

    kb_result = MagicMock()
    kb_result.scalar_one_or_none.return_value = kb

    connector_result = MagicMock()
    connector_result.scalar_one_or_none.return_value = connector

    # First execute → KB lookup (via _get_kb_with_owner_check / _get_kb_or_404)
    # Second execute → connector lookup
    db.execute.side_effect = [kb_result, connector_result]
    return db


class TestTriggerSyncItemQuota:
    """trigger_sync enforces per-KB item quota (R-E2)."""

    @pytest.mark.asyncio
    async def test_returns_403_when_core_kb_at_item_limit(self) -> None:
        """Core-user personal KB with 20 items → 403 kb_quota_items_exceeded."""
        from app.api.connectors import trigger_sync

        org = _make_org("core")
        kb = _make_kb(owner_type="user", slug="my-kb")
        connector = _make_connector()
        db = _make_db(kb, connector)
        mock_credentials = MagicMock()

        with (
            patch(
                "app.api.connectors._get_caller_org",
                return_value=("user-core", org, MagicMock()),
            ),
            patch(
                "app.api.connectors._get_kb_with_owner_check",
                new_callable=AsyncMock,
                return_value=kb,
            ),
            patch(
                "app.services.kb_quota.knowledge_ingest_client.get_source_count",
                new_callable=AsyncMock,
                return_value=20,
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await trigger_sync(
                    kb_slug="my-kb",
                    connector_id="conn-1",
                    credentials=mock_credentials,
                    db=db,
                )

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail["error_code"] == "kb_quota_items_exceeded"

    @pytest.mark.asyncio
    async def test_proceeds_when_core_kb_below_item_limit(self) -> None:
        """Core-user personal KB with 19 items → sync is triggered (no 403)."""
        from app.api.connectors import trigger_sync

        org = _make_org("core")
        kb = _make_kb(owner_type="user", slug="my-kb")
        connector = _make_connector()
        db = _make_db(kb, connector)
        mock_credentials = MagicMock()

        fake_sync_run = MagicMock()

        with (
            patch(
                "app.api.connectors._get_caller_org",
                return_value=("user-core", org, MagicMock()),
            ),
            patch(
                "app.api.connectors._get_kb_with_owner_check",
                new_callable=AsyncMock,
                return_value=kb,
            ),
            patch(
                "app.services.kb_quota.knowledge_ingest_client.get_source_count",
                new_callable=AsyncMock,
                return_value=19,
            ),
            patch(
                "app.api.connectors.klai_connector_client.trigger_sync",
                new_callable=AsyncMock,
                return_value=fake_sync_run,
            ),
            patch("app.api.connectors.emit_event"),
        ):
            result = await trigger_sync(
                kb_slug="my-kb",
                connector_id="conn-1",
                credentials=mock_credentials,
                db=db,
            )

        assert result is fake_sync_run

    @pytest.mark.asyncio
    async def test_complete_plan_is_not_limited(self) -> None:
        """Complete-plan user: no item limit → sync proceeds even with 100 items."""
        from app.api.connectors import trigger_sync

        org = _make_org("complete")
        kb = _make_kb(owner_type="user", slug="my-kb")
        connector = _make_connector()
        db = _make_db(kb, connector)
        mock_credentials = MagicMock()

        fake_sync_run = MagicMock()

        with (
            patch(
                "app.api.connectors._get_caller_org",
                return_value=("user-complete", org, MagicMock()),
            ),
            patch(
                "app.api.connectors._get_kb_with_owner_check",
                new_callable=AsyncMock,
                return_value=kb,
            ),
            patch(
                "app.services.kb_quota.knowledge_ingest_client.get_source_count",
                new_callable=AsyncMock,
                return_value=100,
            ) as mock_count,
            patch(
                "app.api.connectors.klai_connector_client.trigger_sync",
                new_callable=AsyncMock,
                return_value=fake_sync_run,
            ),
            patch("app.api.connectors.emit_event"),
        ):
            result = await trigger_sync(
                kb_slug="my-kb",
                connector_id="conn-1",
                credentials=mock_credentials,
                db=db,
            )

        assert result is fake_sync_run
        # Complete plan skips the count query entirely
        mock_count.assert_not_awaited()


class TestConnectorCapabilityGate:
    """Connector endpoints require kb.connectors capability (R-X2, AC-3).

    Tests that the require_capability("kb.connectors") dependency on the
    router rejects core-plan users before any business logic runs.
    """

    @pytest.mark.asyncio
    async def test_core_user_cannot_list_connectors(self) -> None:
        """GET /connectors → 403 capability_required for core-plan user."""
        from app.api.connectors import list_connectors  # type: ignore[attr-defined]
        from app.api.dependencies import require_capability

        mock_db = AsyncMock()
        mock_org = MagicMock()
        mock_org.plan = "core"
        mock_user = MagicMock()
        mock_user.role = "member"
        mock_result = MagicMock()
        mock_result.one_or_none.return_value = (mock_user, mock_org)
        mock_db.execute.return_value = mock_result

        dep = require_capability("kb.connectors")
        with pytest.raises(HTTPException) as exc_info:
            await dep(user_id="user-core", db=mock_db)

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail["error_code"] == "capability_required"
        assert exc_info.value.detail["capability"] == "kb.connectors"

    @pytest.mark.asyncio
    async def test_admin_bypasses_kb_connectors_capability(self) -> None:
        """Admin users bypass capability checks (always get complete-tier)."""
        from app.api.dependencies import require_capability

        mock_db = AsyncMock()
        mock_org = MagicMock()
        mock_org.plan = "core"
        mock_user = MagicMock()
        mock_user.role = "admin"
        mock_result = MagicMock()
        mock_result.one_or_none.return_value = (mock_user, mock_org)
        mock_db.execute.return_value = mock_result

        dep = require_capability("kb.connectors")
        # Admin on core plan: should NOT raise
        await dep(user_id="admin-user", db=mock_db)
