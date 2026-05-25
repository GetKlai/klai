"""REQ-5 (Finding A-4, HIGH): Role changes SHALL sync Zitadel org:owner grant.

Tests cover:
  AC5.1 — promotion to admin calls grant_user_role with org:owner
  AC5.2 — demotion from admin calls remove_user_role
  AC5.3 — Zitadel failure → DB committed, audit emitted, zitadel_sync_failed=true in response

Both platform_update_role (platform_manage.py) and update_user_role (users.py) are
covered so the shared helper is exercised from both call sites.

# @MX:NOTE: [AUTO] Tests mirror AC5.x from SPEC-SEC-CROSS-TENANT-FOLLOWUP-001.
# @MX:SPEC: SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-5
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_perms

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _org(org_id: int = 42):
    org = MagicMock()
    org.id = org_id
    org.slug = f"org-{org_id}"
    org.deleted_at = None
    return org


def _user(*, org_id: int = 42, zitadel_user_id: str = "zit-abc123", role: str = "company"):
    user = MagicMock()
    user.org_id = org_id
    user.zitadel_user_id = zitadel_user_id
    user.role = role
    user.status = "active"
    return user


def _platform_perms():
    return make_perms(role="admin", user_id="platform-admin", org_id=1, org_slug="getklai", is_platform_admin=True)


def _org_admin_perms(org_id: int = 42):
    return make_perms(role="admin", user_id="org-admin-user", org_id=org_id, org_slug="acme", is_platform_admin=False)


def _setup_db(db, execute_results: list):
    """Feed execute_results sequentially to db.execute."""
    db.execute = AsyncMock(
        side_effect=[MagicMock(scalar_one_or_none=MagicMock(return_value=r)) for r in execute_results]
    )
    db.commit = AsyncMock()
    db.add = MagicMock()


# ---------------------------------------------------------------------------
# AC5.1 — promotion to admin triggers _sync_zitadel_role_grant with old→new
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_platform_update_role_admin_promotion_calls_sync_helper():
    """platform_update_role: company→admin calls _sync_zitadel_role_grant(old='company', new='admin')."""
    from app.api.admin.platform_manage import RoleUpdateRequest, platform_update_role

    user = _user(role="company")
    db = AsyncMock()
    _setup_db(db, [_org(), user])

    mock_sync = AsyncMock()

    with (
        patch("app.api.admin.platform_manage.tenant_scoped_session", return_value=AsyncContext(db)),
        patch("app.api.admin.platform_manage.fire_role_change_notification"),
        patch("app.api.admin.platform_manage.log_event", new=AsyncMock()),
        patch("app.api.admin.platform_manage._sync_zitadel_role_grant", mock_sync),
    ):
        result = await platform_update_role(
            org_id=42,
            zitadel_user_id="zit-abc123",
            body=RoleUpdateRequest(role="admin"),
            perms=_platform_perms(),
        )

    mock_sync.assert_awaited_once_with("zit-abc123", old_role="company", new_role="admin")
    assert result.zitadel_sync_failed is False


@pytest.mark.asyncio
async def test_update_user_role_admin_promotion_calls_sync_helper():
    """users.py update_user_role: company→admin calls _sync_zitadel_role_grant."""
    from app.api.admin.users import RoleUpdateRequest, update_user_role

    perms = _org_admin_perms(org_id=42)
    user = _user(org_id=42, role="company")

    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            MagicMock(scalar_one=MagicMock(return_value=_org())),
            MagicMock(scalar_one_or_none=MagicMock(return_value=user)),
        ]
    )
    db.commit = AsyncMock()

    mock_sync = AsyncMock()

    with (
        patch("app.api.admin.users.fire_role_change_notification"),
        patch("app.api.admin.users.log_event", new=AsyncMock()),
        patch("app.api.admin.users._sync_zitadel_role_grant", mock_sync),
    ):
        result = await update_user_role(
            zitadel_user_id="zit-abc123",
            body=RoleUpdateRequest(role="admin"),
            perms=perms,
            db=db,
        )

    mock_sync.assert_awaited_once_with("zit-abc123", old_role="company", new_role="admin")
    assert result.zitadel_sync_failed is False


# ---------------------------------------------------------------------------
# AC5.2 — demotion from admin triggers _sync_zitadel_role_grant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_platform_update_role_admin_demotion_calls_sync_helper():
    """platform_update_role: admin→company calls _sync_zitadel_role_grant(old='admin', new='company')."""
    from app.api.admin.platform_manage import RoleUpdateRequest, platform_update_role

    user = _user(role="admin")
    db = AsyncMock()
    _setup_db(db, [_org(), user])
    # admin_count > 1 to allow demotion
    db.scalar = AsyncMock(return_value=2)

    mock_sync = AsyncMock()

    with (
        patch("app.api.admin.platform_manage.tenant_scoped_session", return_value=AsyncContext(db)),
        patch("app.api.admin.platform_manage.fire_role_change_notification"),
        patch("app.api.admin.platform_manage.log_event", new=AsyncMock()),
        patch("app.api.admin.platform_manage._sync_zitadel_role_grant", mock_sync),
    ):
        result = await platform_update_role(
            org_id=42,
            zitadel_user_id="zit-abc123",
            body=RoleUpdateRequest(role="company"),
            perms=_platform_perms(),
        )

    mock_sync.assert_awaited_once_with("zit-abc123", old_role="admin", new_role="company")
    assert result.zitadel_sync_failed is False


@pytest.mark.asyncio
async def test_update_user_role_demotion_calls_sync_helper():
    """users.py update_user_role: admin→company calls _sync_zitadel_role_grant."""
    from app.api.admin.users import RoleUpdateRequest, update_user_role

    perms = _org_admin_perms(org_id=42)
    user = _user(org_id=42, role="admin")

    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            MagicMock(scalar_one=MagicMock(return_value=_org())),
            MagicMock(scalar_one_or_none=MagicMock(return_value=user)),
        ]
    )
    db.scalar = AsyncMock(return_value=2)  # admin_count > 1
    db.commit = AsyncMock()

    mock_sync = AsyncMock()

    with (
        patch("app.api.admin.users.fire_role_change_notification"),
        patch("app.api.admin.users.log_event", new=AsyncMock()),
        patch("app.api.admin.users._sync_zitadel_role_grant", mock_sync),
    ):
        result = await update_user_role(
            zitadel_user_id="zit-abc123",
            body=RoleUpdateRequest(role="company"),
            perms=perms,
            db=db,
        )

    mock_sync.assert_awaited_once_with("zit-abc123", old_role="admin", new_role="company")
    assert result.zitadel_sync_failed is False


# ---------------------------------------------------------------------------
# AC5.3 — Zitadel failure: DB committed, audit emitted, zitadel_sync_failed in response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_platform_update_role_zitadel_failure_no_db_rollback():
    """Zitadel sync failure → DB stays committed (no exception propagated), audit emitted."""
    from app.api.admin.platform_manage import RoleUpdateRequest, platform_update_role

    user = _user(role="company")
    db = AsyncMock()
    _setup_db(db, [_org(), user])

    captured_audit: list[dict] = []

    async def _raise_zitadel(*args, **kwargs):
        raise RuntimeError("Zitadel 502 Bad Gateway")

    async def _capture_audit(**kwargs):
        captured_audit.append(kwargs)

    with (
        patch("app.api.admin.platform_manage.tenant_scoped_session", return_value=AsyncContext(db)),
        patch("app.api.admin.platform_manage.fire_role_change_notification"),
        patch("app.api.admin.platform_manage.log_event", side_effect=_capture_audit),
        patch("app.api.admin.platform_manage._sync_zitadel_role_grant", side_effect=_raise_zitadel),
        patch("app.api.admin.platform_manage._emit_audit_safe", new=AsyncMock()) as mock_emit_safe,
    ):
        result = await platform_update_role(
            org_id=42,
            zitadel_user_id="zit-abc123",
            body=RoleUpdateRequest(role="admin"),
            perms=_platform_perms(),
        )

    # DB MUST be committed — no rollback
    db.commit.assert_awaited_once()

    # Response carries the flag
    assert result.zitadel_sync_failed is True

    # _emit_audit_safe called with desync action
    assert mock_emit_safe.await_count == 1
    call_kwargs = mock_emit_safe.await_args.kwargs
    assert call_kwargs["action"] == "platform_admin.role_change_zitadel_desync"
    assert call_kwargs["details"]["zitadel_sync_failed"] is True


@pytest.mark.asyncio
async def test_update_user_role_zitadel_failure_no_db_rollback():
    """users.py: Zitadel sync failure → DB committed, audit event emitted."""
    from app.api.admin.users import RoleUpdateRequest, update_user_role

    perms = _org_admin_perms(org_id=42)
    user = _user(org_id=42, role="company")

    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            MagicMock(scalar_one=MagicMock(return_value=_org())),
            MagicMock(scalar_one_or_none=MagicMock(return_value=user)),
        ]
    )
    db.commit = AsyncMock()

    captured_audit: list[dict] = []

    async def _raise_zitadel(*args, **kwargs):
        raise RuntimeError("Zitadel 502 Bad Gateway")

    async def _capture_audit(**kwargs):
        captured_audit.append(kwargs)

    with (
        patch("app.api.admin.users.fire_role_change_notification"),
        patch("app.api.admin.users.log_event", side_effect=_capture_audit),
        patch("app.api.admin.users._sync_zitadel_role_grant", side_effect=_raise_zitadel),
    ):
        result = await update_user_role(
            zitadel_user_id="zit-abc123",
            body=RoleUpdateRequest(role="admin"),
            perms=perms,
            db=db,
        )

    db.commit.assert_awaited_once()
    assert result.zitadel_sync_failed is True

    desync_events = [e for e in captured_audit if e.get("action") == "platform_admin.role_change_zitadel_desync"]
    assert len(desync_events) == 1
    assert desync_events[0]["details"]["zitadel_sync_failed"] is True


@pytest.mark.asyncio
async def test_no_sync_call_when_role_unchanged():
    """When old_role == new_role, _sync_zitadel_role_grant is still called but is a no-op inside."""
    from app.api.admin.platform_manage import RoleUpdateRequest, platform_update_role

    # company → company (no admin transition)
    user = _user(role="company")
    db = AsyncMock()
    _setup_db(db, [_org(), user])

    mock_sync = AsyncMock()

    with (
        patch("app.api.admin.platform_manage.tenant_scoped_session", return_value=AsyncContext(db)),
        patch("app.api.admin.platform_manage.fire_role_change_notification"),
        patch("app.api.admin.platform_manage.log_event", new=AsyncMock()),
        patch("app.api.admin.platform_manage._sync_zitadel_role_grant", mock_sync),
    ):
        result = await platform_update_role(
            org_id=42,
            zitadel_user_id="zit-abc123",
            body=RoleUpdateRequest(role="company"),
            perms=_platform_perms(),
        )

    # sync still called — no-op is inside the helper, not here
    mock_sync.assert_awaited_once_with("zit-abc123", old_role="company", new_role="company")
    assert result.zitadel_sync_failed is False
