"""Unit tests for the platform-admin per-org widget_messages retention endpoint.

SPEC-CHAT-QUALITY-LOOP-001 open item #2: ``GET/PATCH
/api/admin/orgs/{slug}/widget-retention``, guarded by ``require_platform_admin()``
same as ``platform-unlocks`` (tests/test_platform_unlocks_phase5.py).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status

from tests.conftest import make_org, make_perms


def _make_db_async() -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    return db


def _make_scalar_result(value: object) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=value)
    return result


def _make_platform_admin_perms():
    return make_perms(is_platform_admin=True, org_slug="getklai")


def _make_non_platform_admin_perms():
    return make_perms(is_platform_admin=False, org_slug="voys")


def _make_target_org(retention_days: int | None = None):
    org = make_org(slug="customer-org")
    org.id = 202
    org.name = "Customer Org"
    org.deleted_at = None
    org.widget_messages_retention_days = retention_days
    return org


# ---------------------------------------------------------------------------
# GET endpoint
# ---------------------------------------------------------------------------


class TestGetWidgetRetention:
    @pytest.mark.asyncio
    async def test_get_returns_override_and_default(self) -> None:
        from app.api.admin.widget_retention import get_widget_retention

        perms = _make_platform_admin_perms()
        org = _make_target_org(retention_days=90)

        db = _make_db_async()
        db.execute = AsyncMock(return_value=_make_scalar_result(org))

        with patch("app.api.admin.widget_retention.settings") as mock_settings:
            mock_settings.widget_messages_retention_days = 7
            result = await get_widget_retention(slug="customer-org", perms=perms, db=db)

        assert result.days == 90
        assert result.default_days == 7

    @pytest.mark.asyncio
    async def test_get_returns_null_when_no_override(self) -> None:
        from app.api.admin.widget_retention import get_widget_retention

        perms = _make_platform_admin_perms()
        org = _make_target_org(retention_days=None)

        db = _make_db_async()
        db.execute = AsyncMock(return_value=_make_scalar_result(org))

        with patch("app.api.admin.widget_retention.settings") as mock_settings:
            mock_settings.widget_messages_retention_days = 7
            result = await get_widget_retention(slug="customer-org", perms=perms, db=db)

        assert result.days is None
        assert result.default_days == 7

    @pytest.mark.asyncio
    async def test_get_404_when_org_not_found(self) -> None:
        from app.api.admin.widget_retention import get_widget_retention

        perms = _make_platform_admin_perms()
        db = _make_db_async()
        db.execute = AsyncMock(return_value=_make_scalar_result(None))

        with pytest.raises(HTTPException) as exc:
            await get_widget_retention(slug="nonexistent", perms=perms, db=db)
        assert exc.value.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_get_403_for_non_platform_admin(self) -> None:
        from app.core.permissions import require_platform_admin

        perms = _make_non_platform_admin_perms()
        _dep = require_platform_admin()
        with pytest.raises(HTTPException) as exc:
            await _dep(perms=perms)
        assert exc.value.status_code == status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# PATCH endpoint
# ---------------------------------------------------------------------------


class TestPatchWidgetRetention:
    @pytest.mark.asyncio
    async def test_patch_sets_override_and_emits_audit(self) -> None:
        from app.api.admin.widget_retention import (
            PatchWidgetRetentionRequest,
            patch_widget_retention,
        )

        perms = _make_platform_admin_perms()
        org = _make_target_org(retention_days=None)

        db = _make_db_async()
        db.execute = AsyncMock(return_value=_make_scalar_result(org))

        body = PatchWidgetRetentionRequest(days=90)

        with (
            patch("app.api.admin.widget_retention.emit_lifecycle_event", new_callable=AsyncMock) as mock_emit,
            patch("app.api.admin.widget_retention.settings") as mock_settings,
        ):
            mock_settings.widget_messages_retention_days = 7
            result = await patch_widget_retention(slug="customer-org", body=body, perms=perms, db=db)

        assert result.days == 90
        assert result.default_days == 7
        assert org.widget_messages_retention_days == 90
        db.commit.assert_awaited_once()

        mock_emit.assert_awaited_once()
        call_kwargs = mock_emit.call_args.kwargs
        assert call_kwargs["event_type"] == "widget_retention_updated"
        assert call_kwargs["actor_type"] == "platform_admin"
        assert call_kwargs["properties"] == {"previous_days": None, "new_days": 90}

    @pytest.mark.asyncio
    async def test_patch_null_resets_to_default(self) -> None:
        from app.api.admin.widget_retention import (
            PatchWidgetRetentionRequest,
            patch_widget_retention,
        )

        perms = _make_platform_admin_perms()
        org = _make_target_org(retention_days=90)

        db = _make_db_async()
        db.execute = AsyncMock(return_value=_make_scalar_result(org))

        body = PatchWidgetRetentionRequest(days=None)

        with (
            patch("app.api.admin.widget_retention.emit_lifecycle_event", new_callable=AsyncMock),
            patch("app.api.admin.widget_retention.settings") as mock_settings,
        ):
            mock_settings.widget_messages_retention_days = 7
            result = await patch_widget_retention(slug="customer-org", body=body, perms=perms, db=db)

        assert result.days is None
        assert org.widget_messages_retention_days is None

    @pytest.mark.asyncio
    async def test_patch_404_when_org_not_found(self) -> None:
        from app.api.admin.widget_retention import (
            PatchWidgetRetentionRequest,
            patch_widget_retention,
        )

        perms = _make_platform_admin_perms()
        db = _make_db_async()
        db.execute = AsyncMock(return_value=_make_scalar_result(None))

        body = PatchWidgetRetentionRequest(days=90)
        with pytest.raises(HTTPException) as exc:
            await patch_widget_retention(slug="nonexistent", body=body, perms=perms, db=db)
        assert exc.value.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_patch_403_for_non_platform_admin(self) -> None:
        from app.core.permissions import require_platform_admin

        perms = _make_non_platform_admin_perms()
        _dep = require_platform_admin()
        with pytest.raises(HTTPException) as exc:
            await _dep(perms=perms)
        assert exc.value.status_code == status.HTTP_403_FORBIDDEN

    def test_body_rejects_zero_days(self) -> None:
        """0 is outside the 1..365 domain — FastAPI turns this ValidationError into a 422."""
        from pydantic import ValidationError

        from app.api.admin.widget_retention import PatchWidgetRetentionRequest

        with pytest.raises(ValidationError):
            PatchWidgetRetentionRequest(days=0)

    def test_body_rejects_400_days(self) -> None:
        """400 is outside the 1..365 domain — FastAPI turns this ValidationError into a 422."""
        from pydantic import ValidationError

        from app.api.admin.widget_retention import PatchWidgetRetentionRequest

        with pytest.raises(ValidationError):
            PatchWidgetRetentionRequest(days=400)

    def test_body_accepts_boundary_values(self) -> None:
        from app.api.admin.widget_retention import PatchWidgetRetentionRequest

        assert PatchWidgetRetentionRequest(days=1).days == 1
        assert PatchWidgetRetentionRequest(days=365).days == 365
        assert PatchWidgetRetentionRequest(days=None).days is None


def test_widget_retention_audit_event_type_is_valid_in_code_and_sql() -> None:
    """The route emits ``widget_retention_updated``; if the validator or the DB
    CHECK does not list it, every PATCH fails before its commit (found in
    review: the emitter was mocked in the route tests). The CHECK lives in the
    platform_features script because post-deploy scripts run alphabetically
    and that one recreates the constraint."""
    from pathlib import Path

    from app.services.audit.tenant_lifecycle import _VALID_EVENT_TYPES

    assert "widget_retention_updated" in _VALID_EVENT_TYPES
    sql = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "post_deploy_c0d5e2a7b9f3_tenant_lifecycle_platform_features.sql"
    )
    assert "'widget_retention_updated'::text" in sql.read_text()
