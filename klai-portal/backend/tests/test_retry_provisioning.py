"""SPEC-PROV-001 M4 — admin retry endpoint unit tests.

Authorization tests cover both the admin-role gate (`_require_admin`) and
the platform-admin-org gate (`_require_platform_admin`) added in
audit-tenant-isolation-2026-05-05 finding C-2.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.core.config import settings


def _make_failed_org(slug: str = "acme", org_id: int = 42) -> MagicMock:
    org = MagicMock()
    org.id = org_id
    org.slug = slug
    org.provisioning_status = "failed_rollback_complete"
    org.deleted_at = "2026-04-21T12:00:00+00:00"
    return org


def _make_platform_caller_org() -> MagicMock:
    """Caller's own org with slug==platform_org_slug so the platform-admin gate passes."""
    caller_org = MagicMock()
    caller_org.id = 1
    caller_org.slug = settings.platform_org_slug
    return caller_org


def _make_tenant_caller_org(slug: str = "voys") -> MagicMock:
    """Caller's own org with a non-platform slug — must trigger 403."""
    caller_org = MagicMock()
    caller_org.id = 17
    caller_org.slug = slug
    return caller_org


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
async def test_retry_non_admin_returns_403() -> None:
    from app.api.admin.retry_provisioning import retry_provisioning

    caller_user = MagicMock()
    caller_user.role = "member"

    async def _fake_caller_resolver(*args, **kwargs):
        return ("zit-user", _make_platform_caller_org(), caller_user)

    with (
        patch("app.api.admin.retry_provisioning._get_caller_org", new=_fake_caller_resolver),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await retry_provisioning(
                slug="acme",
                background_tasks=MagicMock(),
                credentials=MagicMock(),
                db=AsyncMock(),
            )

    assert excinfo.value.status_code == 403


@pytest.mark.asyncio
async def test_retry_non_platform_admin_org_returns_403() -> None:
    """audit-tenant-isolation-2026-05-05 finding C-2 regression test.

    A regular tenant-admin (admin role, but non-platform-admin org) MUST NOT
    be able to retry-provision another tenant's org. Without this gate, any
    admin in any tenant could revive any other tenant's failed-rollback row.
    """
    from app.api.admin.retry_provisioning import retry_provisioning

    admin = MagicMock()
    admin.role = "admin"
    admin.zitadel_user_id = "zit-tenant-admin"
    tenant_caller_org = _make_tenant_caller_org(slug="voys")

    async def _fake_caller_resolver(*args, **kwargs):
        return ("zit-tenant-admin", tenant_caller_org, admin)

    with patch("app.api.admin.retry_provisioning._get_caller_org", new=_fake_caller_resolver):
        with pytest.raises(HTTPException) as excinfo:
            await retry_provisioning(
                slug="some-other-tenant",
                background_tasks=MagicMock(),
                credentials=MagicMock(),
                db=AsyncMock(),
            )

    assert excinfo.value.status_code == 403
    assert "platform admin" in str(excinfo.value.detail).lower()


@pytest.mark.asyncio
async def test_retry_happy_path_returns_202_and_queues_task() -> None:
    from app.api.admin.retry_provisioning import retry_provisioning

    admin = MagicMock()
    admin.role = "admin"
    admin.zitadel_user_id = "zit-admin"
    failed_org = _make_failed_org()
    db = _mock_db_returning(failed_org=failed_org)
    background_tasks = MagicMock()

    async def _fake_caller_resolver(*args, **kwargs):
        return ("zit-admin", _make_platform_caller_org(), admin)

    with (
        patch("app.api.admin.retry_provisioning._get_caller_org", new=_fake_caller_resolver),
        patch("app.api.admin.retry_provisioning.log_event", new=AsyncMock()) as mock_log,
    ):
        response = await retry_provisioning(
            slug="acme",
            background_tasks=background_tasks,
            credentials=MagicMock(),
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
    assert log_kwargs["actor"] == "zit-admin"


@pytest.mark.asyncio
async def test_retry_pending_rollback_returns_409_manual_cleanup() -> None:
    from app.api.admin.retry_provisioning import retry_provisioning

    admin = MagicMock()
    admin.role = "admin"
    pending = MagicMock()
    pending.provisioning_status = "failed_rollback_pending"
    db = _mock_db_returning(failed_org=None, existing_org=pending)

    async def _fake_caller_resolver(*args, **kwargs):
        return ("zit-admin", _make_platform_caller_org(), admin)

    with patch("app.api.admin.retry_provisioning._get_caller_org", new=_fake_caller_resolver):
        with pytest.raises(HTTPException) as excinfo:
            await retry_provisioning(
                slug="acme",
                background_tasks=MagicMock(),
                credentials=MagicMock(),
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

    admin = MagicMock()
    admin.role = "admin"
    ready = MagicMock()
    ready.provisioning_status = "ready"
    db = _mock_db_returning(failed_org=None, existing_org=ready)

    async def _fake_caller_resolver(*args, **kwargs):
        return ("zit-admin", _make_platform_caller_org(), admin)

    with patch("app.api.admin.retry_provisioning._get_caller_org", new=_fake_caller_resolver):
        with pytest.raises(HTTPException) as excinfo:
            await retry_provisioning(
                slug="acme",
                background_tasks=MagicMock(),
                credentials=MagicMock(),
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

    admin = MagicMock()
    admin.role = "admin"
    failed_org = _make_failed_org()
    collision = MagicMock()
    collision.id = 99
    db = _mock_db_returning(failed_org=failed_org, collision_org=collision)

    async def _fake_caller_resolver(*args, **kwargs):
        return ("zit-admin", _make_platform_caller_org(), admin)

    with patch("app.api.admin.retry_provisioning._get_caller_org", new=_fake_caller_resolver):
        with pytest.raises(HTTPException) as excinfo:
            await retry_provisioning(
                slug="acme",
                background_tasks=MagicMock(),
                credentials=MagicMock(),
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

    admin = MagicMock()
    admin.role = "admin"
    db = _mock_db_returning(failed_org=None, existing_org=None)

    async def _fake_caller_resolver(*args, **kwargs):
        return ("zit-admin", _make_platform_caller_org(), admin)

    with patch("app.api.admin.retry_provisioning._get_caller_org", new=_fake_caller_resolver):
        with pytest.raises(HTTPException) as excinfo:
            await retry_provisioning(
                slug="nonexistent",
                background_tasks=MagicMock(),
                credentials=MagicMock(),
                db=db,
            )

    assert excinfo.value.status_code == 404
