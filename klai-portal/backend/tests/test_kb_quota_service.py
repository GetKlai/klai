"""
Tests for SPEC-PORTAL-UNIFY-KB-001 Phase A: KBQuotaService (kb_quota.py).

Covers:
- assert_can_create_personal_kb raises 403 when user is at/above limit
- assert_can_create_personal_kb passes when user is below limit
- assert_can_create_personal_kb passes when limit is None (complete plan)
- assert_can_create_org_kb raises 403 when can_create_org_kbs is False
- assert_can_create_org_kb passes when can_create_org_kbs is True
- Error codes match SPEC: kb_quota_personal_kb_exceeded, kb_quota_org_kb_not_allowed
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException


def _make_db_mock() -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    return db


def _make_org(plan: str = "core") -> MagicMock:
    org = MagicMock()
    org.plan = plan
    org.id = 1
    return org


class TestAssertCanCreatePersonalKB:
    """assert_can_create_personal_kb enforces max_personal_kbs_per_user."""

    @pytest.mark.asyncio
    async def test_passes_when_user_has_no_kbs(self) -> None:
        from app.services.kb_quota import assert_can_create_personal_kb

        mock_db = _make_db_mock()
        # count query returns 0
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 0
        mock_db.execute.return_value = mock_result

        # Should not raise
        await assert_can_create_personal_kb(
            user_id="user-1",
            org=_make_org("core"),
            db=mock_db,
        )

    @pytest.mark.asyncio
    async def test_passes_when_user_has_less_than_limit(self) -> None:
        from app.services.kb_quota import assert_can_create_personal_kb

        mock_db = _make_db_mock()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 4  # 4 < 5 (core limit)
        mock_db.execute.return_value = mock_result

        await assert_can_create_personal_kb(
            user_id="user-1",
            org=_make_org("core"),
            db=mock_db,
        )

    @pytest.mark.asyncio
    async def test_raises_403_when_user_is_at_limit(self) -> None:
        from app.services.kb_quota import assert_can_create_personal_kb

        mock_db = _make_db_mock()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 5  # exactly at limit
        mock_db.execute.return_value = mock_result

        with pytest.raises(HTTPException) as exc_info:
            await assert_can_create_personal_kb(
                user_id="user-1",
                org=_make_org("core"),
                db=mock_db,
            )

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail["error_code"] == "kb_quota_personal_kb_exceeded"

    @pytest.mark.asyncio
    async def test_raises_403_when_user_is_above_limit(self) -> None:
        from app.services.kb_quota import assert_can_create_personal_kb

        mock_db = _make_db_mock()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 7  # above limit (grandfathered)
        mock_db.execute.return_value = mock_result

        with pytest.raises(HTTPException) as exc_info:
            await assert_can_create_personal_kb(
                user_id="user-1",
                org=_make_org("core"),
                db=mock_db,
            )

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail["error_code"] == "kb_quota_personal_kb_exceeded"

    @pytest.mark.asyncio
    async def test_error_detail_includes_plan_and_limit(self) -> None:
        from app.services.kb_quota import assert_can_create_personal_kb

        mock_db = _make_db_mock()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 5
        mock_db.execute.return_value = mock_result

        with pytest.raises(HTTPException) as exc_info:
            await assert_can_create_personal_kb(
                user_id="user-1",
                org=_make_org("core"),
                db=mock_db,
            )

        detail = exc_info.value.detail
        assert "plan" in detail
        assert "limit" in detail
        assert detail["plan"] == "core"
        assert detail["limit"] == 5

    @pytest.mark.asyncio
    async def test_passes_when_plan_is_complete_and_none_limit(self) -> None:
        """Complete plan has no limit (None = unlimited)."""
        from app.services.kb_quota import assert_can_create_personal_kb

        mock_db = _make_db_mock()
        # Even with 100 KBs, complete plan should pass
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 100
        mock_db.execute.return_value = mock_result

        await assert_can_create_personal_kb(
            user_id="user-complete",
            org=_make_org("complete"),
            db=mock_db,
        )

    @pytest.mark.asyncio
    async def test_db_execute_is_called_only_when_limit_exists(self) -> None:
        """When plan has None limit, skip the DB count query."""
        from app.services.kb_quota import assert_can_create_personal_kb

        mock_db = _make_db_mock()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 0
        mock_db.execute.return_value = mock_result

        await assert_can_create_personal_kb(
            user_id="user-complete",
            org=_make_org("complete"),
            db=mock_db,
        )
        # DB execute should NOT be called for unlimited plan
        mock_db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_professional_plan_same_limits_as_core(self) -> None:
        from app.services.kb_quota import assert_can_create_personal_kb

        mock_db = _make_db_mock()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 5  # at limit
        mock_db.execute.return_value = mock_result

        with pytest.raises(HTTPException) as exc_info:
            await assert_can_create_personal_kb(
                user_id="user-pro",
                org=_make_org("professional"),
                db=mock_db,
            )

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail["limit"] == 5


class TestAssertCanCreateOrgKB:
    """assert_can_create_org_kb enforces can_create_org_kbs."""

    @pytest.mark.asyncio
    async def test_passes_for_complete_plan(self) -> None:
        from app.services.kb_quota import assert_can_create_org_kb

        mock_db = _make_db_mock()

        await assert_can_create_org_kb(org=_make_org("complete"), db=mock_db)

    @pytest.mark.asyncio
    async def test_raises_403_for_core_plan(self) -> None:
        from app.services.kb_quota import assert_can_create_org_kb

        mock_db = _make_db_mock()

        with pytest.raises(HTTPException) as exc_info:
            await assert_can_create_org_kb(org=_make_org("core"), db=mock_db)

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail["error_code"] == "kb_quota_org_kb_not_allowed"

    @pytest.mark.asyncio
    async def test_raises_403_for_professional_plan(self) -> None:
        from app.services.kb_quota import assert_can_create_org_kb

        mock_db = _make_db_mock()

        with pytest.raises(HTTPException) as exc_info:
            await assert_can_create_org_kb(org=_make_org("professional"), db=mock_db)

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail["error_code"] == "kb_quota_org_kb_not_allowed"

    @pytest.mark.asyncio
    async def test_error_detail_includes_plan(self) -> None:
        from app.services.kb_quota import assert_can_create_org_kb

        mock_db = _make_db_mock()

        with pytest.raises(HTTPException) as exc_info:
            await assert_can_create_org_kb(org=_make_org("core"), db=mock_db)

        detail = exc_info.value.detail
        assert "plan" in detail
        assert detail["plan"] == "core"
