"""SPEC-PROV-001 M4 — admin retry endpoint unit tests.

SPEC-PORTAL-RBAC-REFACTOR-001 Phase 2a: the gate is now declarative —
``Depends(require_platform_admin())`` enforces both the admin-role and
the platform-admin-org check. The role/platform-org rejection branches
are pinned in `tests/test_permissions.py` (test_require_platform_admin_*),
so this file only covers the post-gate happy and 4xx paths.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from tests.conftest import make_perms


def _make_failed_org(slug: str = "acme", org_id: int = 42) -> MagicMock:
    org = MagicMock()
    org.id = org_id
    org.slug = slug
    org.provisioning_status = "failed_rollback_complete"
    org.deleted_at = "2026-04-21T12:00:00+00:00"
    return org


def _platform_admin_perms() -> object:
    """Caller is admin in the platform org — passes require_platform_admin()."""
    return make_perms(role="admin", org_id=1, org_slug="getklai", is_platform_admin=True)


def _mock_db_returning(*, failed_org, collision_org=None, existing_org=None):
    """Build a mock AsyncSession that returns given rows for the three lookups
    the retry endpoint performs.

    Order of lookups in the endpoint:
    1. Failed row by (slug, state, deleted_at IS NOT NULL) + FOR UPDATE
    2. (only if 1 returned None) Fallback by slug for better error mapping
    3. (only if 1 returned a row) Collision check for active row with same slug
    """
    results_queue: list[MagicMock] = []

    # Lookup 1
    r1 = MagicMock()
    r1.scalar_one_or_none.return_value = failed_org
    results_queue.append(r1)

    if failed_org is None:
        # Lookup 2 — fallback
        r2 = MagicMock()
        r2.scalar_one_or_none.return_value = existing_org
        results_queue.append(r2)
    else:
        # Lookup 3 — collision check
        r3 = MagicMock()
        r3.scalar_one_or_none.return_value = collision_org.id if collision_org else None
        results_queue.append(r3)

    async def fake_execute(stmt, *args, **kwargs):
        return results_queue.pop(0) if results_queue else MagicMock()

    db = AsyncMock()
    db.execute = fake_execute
    db.commit = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_retry_happy_path_returns_202_and_queues_task() -> None:
    from app.api.admin.retry_provisioning import retry_provisioning

    failed_org = _make_failed_org()
    db = _mock_db_returning(failed_org=failed_org)
    background_tasks = MagicMock()

    with (
        patch("app.api.admin.retry_provisioning.log_event", new=AsyncMock()) as mock_log,
    ):
        response = await retry_provisioning(
            slug="acme",
            background_tasks=background_tasks,
            perms=_platform_admin_perms(),
            db=db,
        )

    assert response == {"status": "queued"}
    assert failed_org.deleted_at is None
    assert failed_org.provisioning_status == "queued"
    db.commit.assert_awaited_once()
    background_tasks.add_task.assert_called_once()
    # audit-tenant-isolation-2026-05-05 C-2: platform-admin action is audit-logged.
    mock_log.assert_awaited_once()
    log_kwargs = mock_log.await_args.kwargs
    assert log_kwargs["action"] == "retry_provisioning"
    assert log_kwargs["resource_type"] == "portal_org"
    assert log_kwargs["resource_id"] == str(failed_org.id)
    assert log_kwargs["actor"] == "uid-test"


@pytest.mark.asyncio
async def test_retry_pending_rollback_returns_409_manual_cleanup() -> None:
    from app.api.admin.retry_provisioning import retry_provisioning

    pending = MagicMock()
    pending.provisioning_status = "failed_rollback_pending"
    db = _mock_db_returning(failed_org=None, existing_org=pending)

    with pytest.raises(HTTPException) as excinfo:
        await retry_provisioning(
            slug="acme",
            background_tasks=MagicMock(),
            perms=_platform_admin_perms(),
            db=db,
        )

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail == {
        "error": "manual_cleanup_required",
        "state": "failed_rollback_pending",
    }


@pytest.mark.asyncio
async def test_retry_ready_org_returns_409_not_in_retryable_state() -> None:
    from app.api.admin.retry_provisioning import retry_provisioning

    ready = MagicMock()
    ready.provisioning_status = "ready"
    db = _mock_db_returning(failed_org=None, existing_org=ready)

    with pytest.raises(HTTPException) as excinfo:
        await retry_provisioning(
            slug="acme",
            background_tasks=MagicMock(),
            perms=_platform_admin_perms(),
            db=db,
        )

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail == {
        "error": "not_in_retryable_state",
        "state": "ready",
    }


@pytest.mark.asyncio
async def test_retry_slug_collision_returns_409_slug_in_use() -> None:
    from app.api.admin.retry_provisioning import retry_provisioning

    failed_org = _make_failed_org()
    collision = MagicMock()
    collision.id = 99
    db = _mock_db_returning(failed_org=failed_org, collision_org=collision)

    with pytest.raises(HTTPException) as excinfo:
        await retry_provisioning(
            slug="acme",
            background_tasks=MagicMock(),
            perms=_platform_admin_perms(),
            db=db,
        )

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail == {
        "error": "slug_in_use_by_new_org",
        "state": "failed_rollback_complete",
    }
    # No state mutation
    assert failed_org.deleted_at == "2026-04-21T12:00:00+00:00"
    assert failed_org.provisioning_status == "failed_rollback_complete"
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_unknown_slug_returns_404() -> None:
    from app.api.admin.retry_provisioning import retry_provisioning

    db = _mock_db_returning(failed_org=None, existing_org=None)

    with pytest.raises(HTTPException) as excinfo:
        await retry_provisioning(
            slug="nonexistent",
            background_tasks=MagicMock(),
            perms=_platform_admin_perms(),
            db=db,
        )

    assert excinfo.value.status_code == 404
