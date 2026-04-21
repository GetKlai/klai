"""SPEC-PROV-001 M4 — admin retry endpoint unit tests."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _make_failed_org(slug: str = "acme", org_id: int = 42) -> MagicMock:
    org = MagicMock()
    org.id = org_id
    org.slug = slug
    org.provisioning_status = "failed_rollback_complete"
    org.deleted_at = "2026-04-21T12:00:00+00:00"
    return org


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
        return ("zit-user", MagicMock(), caller_user)

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
async def test_retry_happy_path_returns_202_and_queues_task() -> None:
    from app.api.admin.retry_provisioning import retry_provisioning

    admin = MagicMock()
    admin.role = "admin"
    admin.zitadel_user_id = "zit-admin"
    failed_org = _make_failed_org()
    db = _mock_db_returning(failed_org=failed_org)
    background_tasks = MagicMock()

    async def _fake_caller_resolver(*args, **kwargs):
        return ("zit-admin", MagicMock(), admin)

    with patch("app.api.admin.retry_provisioning._get_caller_org", new=_fake_caller_resolver):
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


@pytest.mark.asyncio
async def test_retry_pending_rollback_returns_409_manual_cleanup() -> None:
    from app.api.admin.retry_provisioning import retry_provisioning

    admin = MagicMock()
    admin.role = "admin"
    pending = MagicMock()
    pending.provisioning_status = "failed_rollback_pending"
    db = _mock_db_returning(failed_org=None, existing_org=pending)

    async def _fake_caller_resolver(*args, **kwargs):
        return ("zit-admin", MagicMock(), admin)

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
        return ("zit-admin", MagicMock(), admin)

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
        return ("zit-admin", MagicMock(), admin)

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
        return ("zit-admin", MagicMock(), admin)

    with patch("app.api.admin.retry_provisioning._get_caller_org", new=_fake_caller_resolver):
        with pytest.raises(HTTPException) as excinfo:
            await retry_provisioning(
                slug="nonexistent",
                background_tasks=MagicMock(),
                credentials=MagicMock(),
                db=db,
            )

    assert excinfo.value.status_code == 404
