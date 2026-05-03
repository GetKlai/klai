"""SPEC-INFRA-TENANT-DELETE-001 R1/R8/R10 — deprovision endpoint tests.

Tests cover:
- DELETE /org/me: owner auth, admin-role check, state guard, 202 + background task
- DELETE /orgs/{slug}/deprovision: platform-admin auth, slug 404, 409 states, 202
- POST /orgs/{slug}/retry-deprovisioning: platform-admin, wrong state 409, 202
- GET /org/me/deprovision-status: polling, 404 on gone, failed_deprovisioning + last_failure
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from helpers import FakeResult, setup_db

from app.api.admin.deprovision_org import (
    _find_org_by_slug,
    _guard_entry_state,
    _lock_org_for_deprovision,
    _require_platform_admin,
    deprovision_org_by_slug,
    deprovision_own_org,
    get_deprovision_status,
    get_own_org,
    retry_deprovisioning,
)

# ---------------------------------------------------------------------------
# Shared factories
# ---------------------------------------------------------------------------


def _make_org(
    *,
    org_id: int = 1,
    slug: str = "acme",
    provisioning_status: str = "ready",
    last_failure: dict | None = None,
    deleted_at=None,
) -> MagicMock:
    org = MagicMock()
    org.id = org_id
    org.slug = slug
    org.provisioning_status = provisioning_status
    org.last_failure = last_failure
    org.deleted_at = deleted_at
    return org


def _make_user(role: str = "admin") -> MagicMock:
    user = MagicMock()
    user.role = role
    user.zitadel_user_id = "zit-user-1"
    return user


def _make_db(rows: list | None = None) -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    if rows is not None:
        setup_db(db, [FakeResult(rows=rows)])
    return db


def _make_credentials(token: str = "tok") -> MagicMock:
    creds = MagicMock()
    creds.credentials = token
    return creds


def _caller_org_patch(org: MagicMock, user: MagicMock, zitadel_user_id: str = "zit-user-1"):
    """Patch _get_caller_org to return (zitadel_user_id, org, user)."""
    return patch(
        "app.api.admin.deprovision_org._get_caller_org",
        AsyncMock(return_value=(zitadel_user_id, org, user)),
    )


# ---------------------------------------------------------------------------
# Unit tests — helpers
# ---------------------------------------------------------------------------


class TestLockOrgForDeprovision:
    @pytest.mark.asyncio
    async def test_returns_locked_org(self):
        org = _make_org()
        db = AsyncMock()
        db.execute = AsyncMock(return_value=FakeResult(rows=[org]))
        # scalar_one() is called on the result
        result_mock = MagicMock()
        result_mock.scalar_one.return_value = org
        db.execute = AsyncMock(return_value=result_mock)

        locked = await _lock_org_for_deprovision(1, db)
        assert locked is org


class TestFindOrgBySlug:
    @pytest.mark.asyncio
    async def test_returns_org_when_found(self):
        org = _make_org()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = org
        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        found = await _find_org_by_slug("acme", db)
        assert found is org

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self):
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        found = await _find_org_by_slug("nonexistent", db)
        assert found is None


class TestGuardEntryState:
    def test_allows_ready(self):
        org = _make_org(provisioning_status="ready")
        _guard_entry_state(org)  # no exception

    def test_allows_failed_rollback_complete(self):
        org = _make_org(provisioning_status="failed_rollback_complete")
        _guard_entry_state(org)  # no exception

    @pytest.mark.parametrize("state", ["deprovisioning", "deprovisioned", "failed_deprovisioning"])
    def test_raises_409_on_already_deprovisioning(self, state: str):
        org = _make_org(provisioning_status=state)
        with pytest.raises(HTTPException) as exc_info:
            _guard_entry_state(org)
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["error"] == "already_deprovisioning"

    @pytest.mark.parametrize("state", ["queued", "provisioning", "failed_rollback_pending"])
    def test_raises_409_on_non_deprovisionable_state(self, state: str):
        org = _make_org(provisioning_status=state)
        with pytest.raises(HTTPException) as exc_info:
            _guard_entry_state(org)
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["error"] == "not_in_deprovisionable_state"


class TestRequirePlatformAdmin:
    def test_allows_platform_org(self):
        org = _make_org(slug="getklai")
        with patch("app.api.admin.deprovision_org.settings") as mock_settings:
            mock_settings.platform_org_slug = "getklai"
            _require_platform_admin(org)  # no exception

    def test_raises_403_for_non_platform_org(self):
        org = _make_org(slug="acme")
        with patch("app.api.admin.deprovision_org.settings") as mock_settings:
            mock_settings.platform_org_slug = "getklai"
            with pytest.raises(HTTPException) as exc_info:
                _require_platform_admin(org)
        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# DELETE /org/me — owner self-service
# ---------------------------------------------------------------------------


class TestDeprovisionOwnOrg:
    @pytest.mark.asyncio
    async def test_returns_202_queued(self):
        org = _make_org(provisioning_status="ready")
        user = _make_user(role="admin")
        db = AsyncMock()
        db.add = MagicMock()
        creds = _make_credentials()
        background_tasks = MagicMock()

        locked_result = MagicMock()
        locked_result.scalar_one.return_value = org
        db.execute = AsyncMock(return_value=locked_result)

        with _caller_org_patch(org, user):
            result = await deprovision_own_org(background_tasks, creds, db)

        assert result == {"status": "queued", "org_slug": "acme"}
        assert org.provisioning_status == "deprovisioning"
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_schedules_background_task(self):
        org = _make_org(provisioning_status="ready")
        user = _make_user(role="admin")
        db = AsyncMock()
        db.add = MagicMock()
        creds = _make_credentials()
        background_tasks = MagicMock()

        locked_result = MagicMock()
        locked_result.scalar_one.return_value = org
        db.execute = AsyncMock(return_value=locked_result)

        with (
            _caller_org_patch(org, user),
            patch(
                "app.api.admin.deprovision_org.deprovision_tenant",
            ) as mock_dt,
        ):
            await deprovision_own_org(background_tasks, creds, db)

        background_tasks.add_task.assert_called_once_with(
            mock_dt,
            org.id,
            "zit-user-1",
            "owner",
        )

    @pytest.mark.asyncio
    async def test_raises_403_for_non_admin_user(self):
        org = _make_org(provisioning_status="ready")
        user = _make_user(role="member")
        db = AsyncMock()
        db.add = MagicMock()
        creds = _make_credentials()
        background_tasks = MagicMock()

        with _caller_org_patch(org, user):
            with pytest.raises(HTTPException) as exc_info:
                await deprovision_own_org(background_tasks, creds, db)

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_raises_409_when_already_deprovisioning(self):
        org = _make_org(provisioning_status="deprovisioning")
        user = _make_user(role="admin")
        db = AsyncMock()
        db.add = MagicMock()
        creds = _make_credentials()
        background_tasks = MagicMock()

        locked_result = MagicMock()
        locked_result.scalar_one.return_value = org
        db.execute = AsyncMock(return_value=locked_result)

        with _caller_org_patch(org, user):
            with pytest.raises(HTTPException) as exc_info:
                await deprovision_own_org(background_tasks, creds, db)

        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["error"] == "already_deprovisioning"


# ---------------------------------------------------------------------------
# DELETE /orgs/{slug}/deprovision — platform-admin
# ---------------------------------------------------------------------------


class TestDeprovisionOrgBySlug:
    @pytest.mark.asyncio
    async def test_returns_202_queued(self):
        caller_org = _make_org(slug="getklai", provisioning_status="ready")
        target_org = _make_org(org_id=2, slug="acme", provisioning_status="ready")
        user = _make_user(role="admin")
        db = AsyncMock()
        db.add = MagicMock()
        creds = _make_credentials()
        background_tasks = MagicMock()

        # First execute: _find_org_by_slug, second: _lock_org_for_deprovision
        find_result = MagicMock()
        find_result.scalar_one_or_none.return_value = target_org
        lock_result = MagicMock()
        lock_result.scalar_one.return_value = target_org
        db.execute = AsyncMock(side_effect=[find_result, lock_result])

        with (
            _caller_org_patch(caller_org, user),
            patch("app.api.admin.deprovision_org.settings") as mock_settings,
        ):
            mock_settings.platform_org_slug = "getklai"
            result = await deprovision_org_by_slug("acme", background_tasks, creds, db)

        assert result == {"status": "queued", "org_slug": "acme"}
        assert target_org.provisioning_status == "deprovisioning"

    @pytest.mark.asyncio
    async def test_raises_404_when_slug_not_found(self):
        caller_org = _make_org(slug="getklai", provisioning_status="ready")
        user = _make_user(role="admin")
        db = AsyncMock()
        db.add = MagicMock()
        creds = _make_credentials()
        background_tasks = MagicMock()

        find_result = MagicMock()
        find_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=find_result)

        with (
            _caller_org_patch(caller_org, user),
            patch("app.api.admin.deprovision_org.settings") as mock_settings,
        ):
            mock_settings.platform_org_slug = "getklai"
            with pytest.raises(HTTPException) as exc_info:
                await deprovision_org_by_slug("nonexistent", background_tasks, creds, db)

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_403_for_non_platform_admin(self):
        caller_org = _make_org(slug="acme", provisioning_status="ready")
        user = _make_user(role="admin")
        db = AsyncMock()
        db.add = MagicMock()
        creds = _make_credentials()
        background_tasks = MagicMock()

        with (
            _caller_org_patch(caller_org, user),
            patch("app.api.admin.deprovision_org.settings") as mock_settings,
        ):
            mock_settings.platform_org_slug = "getklai"
            with pytest.raises(HTTPException) as exc_info:
                await deprovision_org_by_slug("acme", background_tasks, creds, db)

        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# POST /orgs/{slug}/retry-deprovisioning
# ---------------------------------------------------------------------------


class TestRetryDeprovisioning:
    @pytest.mark.asyncio
    async def test_returns_202_queued_from_failed_state(self):
        caller_org = _make_org(slug="getklai", provisioning_status="ready")
        target_org = _make_org(org_id=2, slug="acme", provisioning_status="failed_deprovisioning")
        user = _make_user(role="admin")
        db = AsyncMock()
        db.add = MagicMock()
        creds = _make_credentials()
        background_tasks = MagicMock()

        find_result = MagicMock()
        find_result.scalar_one_or_none.return_value = target_org
        lock_result = MagicMock()
        lock_result.scalar_one.return_value = target_org
        db.execute = AsyncMock(side_effect=[find_result, lock_result, MagicMock()])

        with (
            _caller_org_patch(caller_org, user),
            patch("app.api.admin.deprovision_org.settings") as mock_settings,
        ):
            mock_settings.platform_org_slug = "getklai"
            result = await retry_deprovisioning("acme", background_tasks, creds, db)

        assert result == {"status": "queued"}
        assert target_org.provisioning_status == "deprovisioning"

    @pytest.mark.asyncio
    async def test_raises_409_when_not_in_failed_state(self):
        caller_org = _make_org(slug="getklai", provisioning_status="ready")
        target_org = _make_org(org_id=2, slug="acme", provisioning_status="deprovisioning")
        user = _make_user(role="admin")
        db = AsyncMock()
        db.add = MagicMock()
        creds = _make_credentials()
        background_tasks = MagicMock()

        find_result = MagicMock()
        find_result.scalar_one_or_none.return_value = target_org
        lock_result = MagicMock()
        lock_result.scalar_one.return_value = target_org
        db.execute = AsyncMock(side_effect=[find_result, lock_result])

        with (
            _caller_org_patch(caller_org, user),
            patch("app.api.admin.deprovision_org.settings") as mock_settings,
        ):
            mock_settings.platform_org_slug = "getklai"
            with pytest.raises(HTTPException) as exc_info:
                await retry_deprovisioning("acme", background_tasks, creds, db)

        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["error"] == "not_in_retryable_deprovision_state"

    @pytest.mark.asyncio
    async def test_raises_404_when_slug_not_found(self):
        caller_org = _make_org(slug="getklai", provisioning_status="ready")
        user = _make_user(role="admin")
        db = AsyncMock()
        db.add = MagicMock()
        creds = _make_credentials()
        background_tasks = MagicMock()

        find_result = MagicMock()
        find_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=find_result)

        with (
            _caller_org_patch(caller_org, user),
            patch("app.api.admin.deprovision_org.settings") as mock_settings,
        ):
            mock_settings.platform_org_slug = "getklai"
            with pytest.raises(HTTPException) as exc_info:
                await retry_deprovisioning("nonexistent", background_tasks, creds, db)

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# GET /org/me/deprovision-status
# ---------------------------------------------------------------------------


class TestGetDeprovisionStatus:
    @pytest.mark.asyncio
    async def test_returns_deprovisioning_status(self):
        org = _make_org(provisioning_status="deprovisioning")
        user = _make_user()
        db = AsyncMock()
        db.add = MagicMock()
        creds = _make_credentials()

        with patch(
            "app.api.admin.deprovision_org._get_caller_org",
            AsyncMock(return_value=("zit-user-1", org, user)),
        ):
            result = await get_deprovision_status(creds, db)

        assert result == {"status": "deprovisioning"}

    @pytest.mark.asyncio
    async def test_includes_sanitized_last_failure_on_failed_state(self):
        """Owner sees only step + failed_at; error string with infra detail
        and attempt count are NOT exposed (would leak internal hostnames /
        DSN fragments)."""
        last_failure = {
            "step": "_delete_caddy_upstream",
            "error": "Connection refused to klai-core-knowledge-ingest-1:8000",
            "attempt": 3,
            "failed_at": "2026-05-03T12:00:00+00:00",
        }
        org = _make_org(provisioning_status="failed_deprovisioning", last_failure=last_failure)
        user = _make_user()
        db = AsyncMock()
        db.add = MagicMock()
        creds = _make_credentials()

        with patch(
            "app.api.admin.deprovision_org._get_caller_org",
            AsyncMock(return_value=("zit-user-1", org, user)),
        ):
            result = await get_deprovision_status(creds, db)

        assert result["status"] == "failed_deprovisioning"
        # Sanitized: only step + failed_at exposed, NOT error string or attempt.
        assert result["last_failure"] == {
            "step": "_delete_caddy_upstream",
            "failed_at": "2026-05-03T12:00:00+00:00",
        }
        assert "error" not in result["last_failure"]
        assert "attempt" not in result["last_failure"]

    @pytest.mark.asyncio
    async def test_non_admin_member_blocked_by_require_admin(self):
        """Members and group-admins MUST NOT see deprovision status — admin only."""
        org = _make_org(provisioning_status="deprovisioning")
        user = _make_user(role="member")  # not admin
        db = AsyncMock()
        db.add = MagicMock()
        creds = _make_credentials()

        with patch(
            "app.api.admin.deprovision_org._get_caller_org",
            AsyncMock(return_value=("zit-user-1", org, user)),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_deprovision_status(creds, db)

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_returns_ready_when_not_deprovisioning(self):
        org = _make_org(provisioning_status="ready")
        user = _make_user()
        db = AsyncMock()
        db.add = MagicMock()
        creds = _make_credentials()

        with patch(
            "app.api.admin.deprovision_org._get_caller_org",
            AsyncMock(return_value=("zit-user-1", org, user)),
        ):
            result = await get_deprovision_status(creds, db)

        assert result == {"status": "ready"}

    @pytest.mark.asyncio
    async def test_passes_allow_during_deprovisioning_true(self):
        """Verifies the status endpoint calls _get_caller_org with allow_during_deprovisioning=True."""
        org = _make_org(provisioning_status="deprovisioning")
        user = _make_user()
        db = AsyncMock()
        db.add = MagicMock()
        creds = _make_credentials()
        mock_get_caller_org = AsyncMock(return_value=("zit-user-1", org, user))

        with patch("app.api.admin.deprovision_org._get_caller_org", mock_get_caller_org):
            await get_deprovision_status(creds, db)

        mock_get_caller_org.assert_called_once_with(creds, db, allow_during_deprovisioning=True)

    @pytest.mark.asyncio
    async def test_raises_404_when_org_not_found(self):
        """_get_caller_org raises 404 when org row is gone (successful deprovisioning)."""
        db = AsyncMock()
        db.add = MagicMock()
        creds = _make_credentials()

        with patch(
            "app.api.admin.deprovision_org._get_caller_org",
            AsyncMock(side_effect=HTTPException(status_code=404, detail="Organisation not found")),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_deprovision_status(creds, db)

        assert exc_info.value.status_code == 404


class TestGetOwnOrg:
    """SPEC-INFRA-TENANT-DELETE-001 R10 — owner-readable org metadata.

    Discovered during 2026-05-03 e2e walkthrough on voys.getklai.com:
    danger-zone page issued GET /api/admin/org/me and got 405 because
    only DELETE was registered. Added the GET handler to render the
    delete-modal precondition (org slug + name).
    """

    @pytest.mark.asyncio
    async def test_returns_slug_and_name_for_admin(self):
        org = _make_org(slug="voys")
        org.name = "Voys"
        user = _make_user(role="admin")
        db = AsyncMock()
        creds = _make_credentials()

        with _caller_org_patch(org, user):
            result = await get_own_org(creds, db)

        assert result == {"slug": "voys", "name": "Voys"}

    @pytest.mark.asyncio
    async def test_raises_403_for_non_admin(self):
        org = _make_org(slug="voys")
        org.name = "Voys"
        user = _make_user(role="member")
        db = AsyncMock()
        creds = _make_credentials()

        with _caller_org_patch(org, user):
            with pytest.raises(HTTPException) as exc_info:
                await get_own_org(creds, db)

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_allows_during_deprovisioning(self):
        # The modal needs slug+name to render the polling UI even after
        # deprovisioning has started. allow_during_deprovisioning=True is
        # the contract — verify _get_caller_org is called with that flag.
        org = _make_org(slug="voys", provisioning_status="deprovisioning")
        org.name = "Voys"
        user = _make_user(role="admin")
        db = AsyncMock()
        creds = _make_credentials()

        with patch(
            "app.api.admin.deprovision_org._get_caller_org",
            AsyncMock(return_value=("zit-user-1", org, user)),
        ) as mock_caller:
            result = await get_own_org(creds, db)

        # First positional or keyword arg lands on credentials/db, then the flag.
        mock_caller.assert_awaited_once_with(creds, db, allow_during_deprovisioning=True)
        assert result == {"slug": "voys", "name": "Voys"}
