"""SPEC-INFRA-TENANT-DELETE-001 R1/R8/R10 — deprovision endpoint tests.

Tests cover:
- DELETE /org/me: owner auth, admin-role check, state guard, 202 + background task
- DELETE /orgs/{slug}/deprovision: platform-admin auth, slug 404, 409 states, 202
- POST /orgs/{slug}/retry-deprovisioning: platform-admin, wrong state 409, 202
- GET /org/me/deprovision-status: polling, 404 on gone, failed_deprovisioning + last_failure

SPEC-PORTAL-RBAC-REFACTOR-001 Phase 2b: endpoints take ``perms`` directly.
``_require_platform_admin`` is no longer imported by `deprovision_org.py`;
the role+platform-org gate is enforced by `Depends(require_platform_admin())`
and pinned in `tests/test_permissions.py`. The `get_own_org` and
`get_deprovision_status` endpoints use `Depends(get_caller_during_deprovisioning)`
so the tenant-deleting 403 doesn't fire on the polling/danger-zone path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.admin.deprovision_org import (
    _find_org_by_slug,
    _guard_entry_state,
    _lock_org_for_deprovision,
    deprovision_org_by_slug,
    deprovision_own_org,
    get_deprovision_status,
    get_own_org,
    retry_deprovisioning,
)
from tests.conftest import make_perms

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


# ---------------------------------------------------------------------------
# Unit tests — helpers
# ---------------------------------------------------------------------------


class TestLockOrgForDeprovision:
    @pytest.mark.asyncio
    async def test_returns_locked_org(self):
        org = _make_org()
        result_mock = MagicMock()
        result_mock.scalar_one.return_value = org
        db = AsyncMock()
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


# ---------------------------------------------------------------------------
# DELETE /org/me — owner self-service (admin role gate via Depends)
# ---------------------------------------------------------------------------


class TestDeprovisionOwnOrg:
    @pytest.mark.asyncio
    async def test_returns_202_queued(self):
        org = _make_org(provisioning_status="ready")
        db = AsyncMock()
        db.add = MagicMock()
        background_tasks = MagicMock()

        locked_result = MagicMock()
        locked_result.scalar_one.return_value = org
        db.execute = AsyncMock(return_value=locked_result)

        result = await deprovision_own_org(
            background_tasks,
            perms=make_perms(role="admin", user_id="zit-user-1", org_id=1),
            db=db,
        )

        assert result == {"status": "queued", "org_slug": "acme"}
        assert org.provisioning_status == "deprovisioning"
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_schedules_background_task(self):
        org = _make_org(provisioning_status="ready")
        db = AsyncMock()
        db.add = MagicMock()
        background_tasks = MagicMock()

        locked_result = MagicMock()
        locked_result.scalar_one.return_value = org
        db.execute = AsyncMock(return_value=locked_result)

        with patch("app.api.admin.deprovision_org.deprovision_tenant") as mock_dt:
            await deprovision_own_org(
                background_tasks,
                perms=make_perms(role="admin", user_id="zit-user-1", org_id=1),
                db=db,
            )

        background_tasks.add_task.assert_called_once_with(
            mock_dt,
            org.id,
            "zit-user-1",
            "owner",
        )

    @pytest.mark.asyncio
    async def test_raises_409_when_already_deprovisioning(self):
        org = _make_org(provisioning_status="deprovisioning")
        db = AsyncMock()
        db.add = MagicMock()
        background_tasks = MagicMock()

        locked_result = MagicMock()
        locked_result.scalar_one.return_value = org
        db.execute = AsyncMock(return_value=locked_result)

        with pytest.raises(HTTPException) as exc_info:
            await deprovision_own_org(
                background_tasks,
                perms=make_perms(role="admin", user_id="zit-user-1", org_id=1),
                db=db,
            )

        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["error"] == "already_deprovisioning"


# ---------------------------------------------------------------------------
# DELETE /orgs/{slug}/deprovision — platform-admin
# ---------------------------------------------------------------------------


class TestDeprovisionOrgBySlug:
    @pytest.mark.asyncio
    async def test_returns_202_queued(self):
        target_org = _make_org(org_id=2, slug="acme", provisioning_status="ready")
        db = AsyncMock()
        db.add = MagicMock()
        background_tasks = MagicMock()

        find_result = MagicMock()
        find_result.scalar_one_or_none.return_value = target_org
        lock_result = MagicMock()
        lock_result.scalar_one.return_value = target_org
        db.execute = AsyncMock(side_effect=[find_result, lock_result])

        result = await deprovision_org_by_slug(
            "acme",
            background_tasks,
            perms=make_perms(role="admin", user_id="zit-user-1", org_slug="getklai", is_platform_admin=True),
            db=db,
        )

        assert result == {"status": "queued", "org_slug": "acme"}
        assert target_org.provisioning_status == "deprovisioning"

    @pytest.mark.asyncio
    async def test_raises_404_when_slug_not_found(self):
        db = AsyncMock()
        db.add = MagicMock()
        background_tasks = MagicMock()

        find_result = MagicMock()
        find_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=find_result)

        with pytest.raises(HTTPException) as exc_info:
            await deprovision_org_by_slug(
                "nonexistent",
                background_tasks,
                perms=make_perms(role="admin", org_slug="getklai", is_platform_admin=True),
                db=db,
            )

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# POST /orgs/{slug}/retry-deprovisioning
# ---------------------------------------------------------------------------


class TestRetryDeprovisioning:
    @pytest.mark.asyncio
    async def test_returns_202_queued_from_failed_state(self):
        target_org = _make_org(org_id=2, slug="acme", provisioning_status="failed_deprovisioning")
        db = AsyncMock()
        db.add = MagicMock()
        background_tasks = MagicMock()

        find_result = MagicMock()
        find_result.scalar_one_or_none.return_value = target_org
        lock_result = MagicMock()
        lock_result.scalar_one.return_value = target_org
        db.execute = AsyncMock(side_effect=[find_result, lock_result, MagicMock()])

        result = await retry_deprovisioning(
            "acme",
            background_tasks,
            perms=make_perms(role="admin", user_id="zit-user-1", org_slug="getklai", is_platform_admin=True),
            db=db,
        )

        assert result == {"status": "queued"}
        assert target_org.provisioning_status == "deprovisioning"

    @pytest.mark.asyncio
    async def test_raises_409_when_not_in_failed_state(self):
        target_org = _make_org(org_id=2, slug="acme", provisioning_status="deprovisioning")
        db = AsyncMock()
        db.add = MagicMock()
        background_tasks = MagicMock()

        find_result = MagicMock()
        find_result.scalar_one_or_none.return_value = target_org
        lock_result = MagicMock()
        lock_result.scalar_one.return_value = target_org
        db.execute = AsyncMock(side_effect=[find_result, lock_result])

        with pytest.raises(HTTPException) as exc_info:
            await retry_deprovisioning(
                "acme",
                background_tasks,
                perms=make_perms(role="admin", org_slug="getklai", is_platform_admin=True),
                db=db,
            )

        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["error"] == "not_in_retryable_deprovision_state"

    @pytest.mark.asyncio
    async def test_raises_404_when_slug_not_found(self):
        db = AsyncMock()
        db.add = MagicMock()
        background_tasks = MagicMock()

        find_result = MagicMock()
        find_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=find_result)

        with pytest.raises(HTTPException) as exc_info:
            await retry_deprovisioning(
                "nonexistent",
                background_tasks,
                perms=make_perms(role="admin", org_slug="getklai", is_platform_admin=True),
                db=db,
            )

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# GET /org/me/deprovision-status (uses get_caller_during_deprovisioning)
# ---------------------------------------------------------------------------


class TestGetDeprovisionStatus:
    @pytest.mark.asyncio
    async def test_returns_deprovisioning_status(self):
        org = _make_org(provisioning_status="deprovisioning")
        db = AsyncMock()
        db.get = AsyncMock(return_value=org)

        result = await get_deprovision_status(
            perms=make_perms(role="admin", org_id=1, provisioning_status="deprovisioning"),
            db=db,
        )

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
        db = AsyncMock()
        db.get = AsyncMock(return_value=org)

        result = await get_deprovision_status(
            perms=make_perms(role="admin", org_id=1, provisioning_status="failed_deprovisioning"),
            db=db,
        )

        assert result["status"] == "failed_deprovisioning"
        # Sanitized: only step + failed_at exposed, NOT error string or attempt.
        assert result["last_failure"] == {
            "step": "_delete_caddy_upstream",
            "failed_at": "2026-05-03T12:00:00+00:00",
        }
        assert "error" not in result["last_failure"]
        assert "attempt" not in result["last_failure"]

    @pytest.mark.asyncio
    async def test_non_admin_member_blocked(self):
        """Members and group-admins MUST NOT see deprovision status — admin only.

        get_caller_during_deprovisioning passes any role (no min-role check),
        so the inline admin guard inside the endpoint is what enforces this.
        """
        db = AsyncMock()
        db.get = AsyncMock(return_value=_make_org(provisioning_status="deprovisioning"))

        with pytest.raises(HTTPException) as exc_info:
            await get_deprovision_status(
                perms=make_perms(role="company", org_id=1, provisioning_status="deprovisioning"),
                db=db,
            )

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_returns_ready_when_not_deprovisioning(self):
        org = _make_org(provisioning_status="ready")
        db = AsyncMock()
        db.get = AsyncMock(return_value=org)

        result = await get_deprovision_status(
            perms=make_perms(role="admin", org_id=1, provisioning_status="ready"),
            db=db,
        )

        assert result == {"status": "ready"}

    @pytest.mark.asyncio
    async def test_raises_404_when_org_row_gone(self):
        """A successfully-deprovisioned org has its row deleted; ``db.get``
        returns None and the endpoint returns 404."""
        db = AsyncMock()
        db.get = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc_info:
            await get_deprovision_status(
                perms=make_perms(role="admin", org_id=1, provisioning_status="deprovisioning"),
                db=db,
            )

        assert exc_info.value.status_code == 404


class TestGetOwnOrg:
    """SPEC-INFRA-TENANT-DELETE-001 R10 — owner-readable org metadata.

    Discovered during 2026-05-03 e2e walkthrough on voys.getklai.com:
    danger-zone page issued GET /api/admin/org/me and got 405 because
    only DELETE was registered. Added the GET handler to render the
    delete-modal precondition (org slug + name).

    Phase 2b: uses ``get_caller_during_deprovisioning`` so the modal
    stays renderable while the org is being deleted. Inline admin guard
    prevents members from seeing this.
    """

    @pytest.mark.asyncio
    async def test_returns_slug_and_name_for_admin(self):
        org = _make_org(slug="voys")
        org.name = "Voys"
        db = AsyncMock()
        db.get = AsyncMock(return_value=org)

        result = await get_own_org(
            perms=make_perms(role="admin", org_id=1, org_slug="voys"),
            db=db,
        )

        assert result == {"slug": "voys", "name": "Voys"}

    @pytest.mark.asyncio
    async def test_raises_403_for_non_admin(self):
        db = AsyncMock()
        with pytest.raises(HTTPException) as exc_info:
            await get_own_org(
                perms=make_perms(role="company", org_id=1, org_slug="voys"),
                db=db,
            )

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_allows_during_deprovisioning(self):
        """The danger-zone modal needs slug+name to render the polling UI
        even while deprovisioning is in progress. The endpoint depends on
        ``get_caller_during_deprovisioning``, so the perms snapshot can
        carry ``provisioning_status='deprovisioning'`` and still pass."""
        org = _make_org(slug="voys", provisioning_status="deprovisioning")
        org.name = "Voys"
        db = AsyncMock()
        db.get = AsyncMock(return_value=org)

        result = await get_own_org(
            perms=make_perms(
                role="admin",
                org_id=1,
                org_slug="voys",
                provisioning_status="deprovisioning",
            ),
            db=db,
        )

        assert result == {"slug": "voys", "name": "Voys"}
