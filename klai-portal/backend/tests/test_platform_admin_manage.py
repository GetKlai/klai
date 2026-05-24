"""Regression tests for platform-admin cross-tenant write hardening."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from helpers import FakeResult, setup_db

from tests.conftest import make_perms


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


def _user(*, org_id: int = 42, user_id: str = "target-user", role: str = "company"):
    user = MagicMock()
    user.org_id = org_id
    user.zitadel_user_id = user_id
    user.role = role
    user.status = "active"
    return user


def _platform_perms():
    return make_perms(
        role="admin",
        user_id="platform-admin",
        org_id=1,
        org_slug="getklai",
        is_platform_admin=True,
    )


@pytest.mark.asyncio
async def test_platform_role_change_notifies_active_sessions() -> None:
    from app.api.admin.platform_manage import RoleUpdateRequest, platform_update_role

    db = AsyncMock()
    setup_db(db, [FakeResult([_org()]), FakeResult([_user(role="company")])])

    with (
        patch("app.api.admin.platform_manage.tenant_scoped_session", return_value=AsyncContext(db)),
        patch("app.api.admin.platform_manage.fire_role_change_notification") as notify,
        patch("app.api.admin.platform_manage.log_event", new=AsyncMock()),
    ):
        await platform_update_role(
            org_id=42,
            zitadel_user_id="target-user",
            body=RoleUpdateRequest(role="admin"),
            perms=_platform_perms(),
        )

    notify.assert_called_once_with("target-user")
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_platform_delete_keeps_global_identity_when_other_memberships_exist() -> None:
    from app.api.admin.platform_manage import platform_delete_user
    from app.services.user_memberships import UserMembershipSummary

    db = AsyncMock()
    db.delete = AsyncMock()
    setup_db(db, [FakeResult([_org()]), FakeResult([_user()])])
    preview = MagicMock(personal_kbs=[], org_kbs_solely_owned=[])

    with (
        patch("app.api.admin.platform_manage.tenant_scoped_session", return_value=AsyncContext(db)),
        patch(
            "app.api.admin.platform_manage.get_user_membership_summary",
            new=AsyncMock(
                return_value=UserMembershipSummary(total_count=2, remaining_count=1, is_platform_admin=False)
            ),
        ),
        patch("app.services.kb_offboarding.compute_offboard_preview", new=AsyncMock(return_value=preview)),
        patch("app.services.kb_offboarding.revoke_user_credentials", new=AsyncMock(return_value=(0, 0))),
        patch("app.api.admin.platform_manage.zitadel") as zitadel,
        patch("app.api.admin.platform_manage.fire_role_change_notification"),
        patch("app.api.admin.platform_manage.log_event", new=AsyncMock()) as log_event,
    ):
        zitadel.remove_user = AsyncMock()
        response = await platform_delete_user(org_id=42, zitadel_user_id="target-user", perms=_platform_perms())

    zitadel.remove_user.assert_not_awaited()
    db.delete.assert_awaited_once()
    assert response.message == "Gebruiker uit tenant verwijderd."
    details = log_event.await_args.kwargs["details"]
    assert details["global_identity_deleted"] is False
    assert details["remaining_membership_count"] == 1


@pytest.mark.asyncio
async def test_platform_delete_blocks_platform_admin_identity() -> None:
    from app.api.admin.platform_manage import platform_delete_user
    from app.services.user_memberships import UserMembershipSummary

    db = AsyncMock()
    setup_db(db, [FakeResult([_org()]), FakeResult([_user(role="admin")])])

    with (
        patch("app.api.admin.platform_manage.tenant_scoped_session", return_value=AsyncContext(db)),
        patch(
            "app.api.admin.platform_manage.get_user_membership_summary",
            new=AsyncMock(return_value=UserMembershipSummary(total_count=2, remaining_count=1, is_platform_admin=True)),
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await platform_delete_user(org_id=42, zitadel_user_id="target-user", perms=_platform_perms())

    assert exc_info.value.status_code == 409
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_platform_invite_rolls_back_zitadel_user_when_grant_fails() -> None:
    from app.api.admin.platform_manage import PlatformInviteRequest, platform_invite

    org = _org()
    with (
        patch("app.api.admin.platform_manage._load_org_or_404", new=AsyncMock(return_value=org)),
        patch("app.api.admin.platform_manage.zitadel") as zitadel,
    ):
        zitadel.invite_user = AsyncMock(return_value={"userId": "new-user"})
        zitadel.grant_user_role = AsyncMock(side_effect=RuntimeError("grant failed"))
        zitadel.remove_user = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await platform_invite(
                org_id=42,
                body=PlatformInviteRequest(
                    email="new@example.com",
                    first_name="New",
                    last_name="User",
                    role="admin",
                ),
                perms=_platform_perms(),
            )

    assert exc_info.value.status_code == 502
    zitadel.remove_user.assert_awaited_once()


@pytest.mark.asyncio
async def test_platform_invite_mail_failure_keeps_committed_portal_user() -> None:
    from app.api.admin.platform_manage import PlatformInviteRequest, platform_invite

    db = AsyncMock()
    db.add = MagicMock()
    org = _org()
    with (
        patch("app.api.admin.platform_manage._load_org_or_404", new=AsyncMock(return_value=org)),
        patch("app.api.admin.platform_manage.tenant_scoped_session", return_value=AsyncContext(db)),
        patch("app.api.admin.platform_manage.create_default_personal_kb", new=AsyncMock()),
        patch("app.api.admin.platform_manage.log_event", new=AsyncMock()),
        patch("app.api.admin.platform_manage.zitadel") as zitadel,
    ):
        zitadel.invite_user = AsyncMock(return_value={"userId": "new-user"})
        zitadel.grant_user_role = AsyncMock()
        zitadel.send_invite_code = AsyncMock(side_effect=RuntimeError("mail failed"))
        zitadel.remove_user = AsyncMock()

        response = await platform_invite(
            org_id=42,
            body=PlatformInviteRequest(
                email="new@example.com",
                first_name="New",
                last_name="User",
                role="personal",
            ),
            perms=_platform_perms(),
        )

    db.commit.assert_awaited_once()
    zitadel.remove_user.assert_not_awaited()
    assert "invite-mail kon niet worden verstuurd" in response.message


@pytest.mark.asyncio
async def test_platform_create_tenant_owner_setup_failure_rolls_back_org_and_user() -> None:
    from app.api.admin.platform_manage import CreateTenantRequest, platform_create_tenant

    db = AsyncMock()
    background_tasks = MagicMock()

    with patch("app.api.admin.platform_manage.zitadel") as zitadel:
        zitadel.create_org = AsyncMock(return_value={"id": "zitadel-org-new"})
        zitadel.invite_user = AsyncMock(return_value={"userId": "owner-user"})
        zitadel.grant_user_role = AsyncMock(side_effect=RuntimeError("grant failed"))
        zitadel.remove_user = AsyncMock()
        zitadel.delete_org = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await platform_create_tenant(
                body=CreateTenantRequest(
                    company_name="Acme BV",
                    owner_email="owner@example.com",
                    owner_first_name="Owner",
                    owner_last_name="User",
                ),
                background_tasks=background_tasks,
                perms=_platform_perms(),
                db=db,
            )

    assert exc_info.value.status_code == 502
    zitadel.remove_user.assert_awaited_once()
    zitadel.delete_org.assert_awaited_once_with("zitadel-org-new")


# ---------------------------------------------------------------------------
# REQ-6 (Finding A-7): partial-failure paths emit audit events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_platform_invite_zitadel_invite_failure_emits_audit_event() -> None:
    """AC6.1 — invite Zitadel-failure emits platform_admin.invite_zitadel_invite_failed."""
    from app.api.admin.platform_manage import PlatformInviteRequest, platform_invite

    org = _org()
    log_calls: list[dict] = []

    async def _capture_log_event(**kwargs: object) -> None:
        log_calls.append(kwargs)

    with (
        patch("app.api.admin.platform_manage._load_org_or_404", new=AsyncMock(return_value=org)),
        patch("app.api.admin.platform_manage.log_event", side_effect=_capture_log_event),
        patch("app.api.admin.platform_manage.zitadel") as mock_zitadel,
    ):
        mock_zitadel.invite_user = AsyncMock(side_effect=RuntimeError("zitadel 502"))
        mock_zitadel.remove_user = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await platform_invite(
                org_id=42,
                body=PlatformInviteRequest(
                    email="probe@attacker.example",
                    first_name="Probe",
                    last_name="Attacker",
                    role="personal",
                ),
                perms=_platform_perms(),
            )

    assert exc_info.value.status_code == 502
    assert len(log_calls) == 1, "Exactly one audit event should be emitted on Zitadel-invite failure"
    evt = log_calls[0]
    assert evt["action"] == "platform_admin.invite_zitadel_invite_failed"
    assert evt["details"]["target_email"] == "probe@attacker.example"
    assert evt["details"]["target_org_id"] == 42
    assert "zitadel 502" in evt["details"]["error"]
    assert len(evt["details"]["error"]) <= 200


@pytest.mark.asyncio
async def test_platform_create_tenant_grant_role_failure_emits_audit_event() -> None:
    """AC6.2 — create-tenant grant_role failure emits platform_admin.create_tenant_grant_role_failed."""
    from app.api.admin.platform_manage import CreateTenantRequest, platform_create_tenant

    db = AsyncMock()
    background_tasks = MagicMock()
    log_calls: list[dict] = []

    async def _capture_log_event(**kwargs: object) -> None:
        log_calls.append(kwargs)

    with (
        patch("app.api.admin.platform_manage.log_event", side_effect=_capture_log_event),
        patch("app.api.admin.platform_manage.zitadel") as mock_zitadel,
    ):
        mock_zitadel.create_org = AsyncMock(return_value={"id": "zitadel-org-new"})
        mock_zitadel.invite_user = AsyncMock(return_value={"userId": "owner-user"})
        mock_zitadel.grant_user_role = AsyncMock(side_effect=RuntimeError("grant 502"))
        mock_zitadel.remove_user = AsyncMock()
        mock_zitadel.delete_org = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await platform_create_tenant(
                body=CreateTenantRequest(
                    company_name="Acme BV",
                    owner_email="owner@acme.example",
                    owner_first_name="Owner",
                    owner_last_name="User",
                ),
                background_tasks=background_tasks,
                perms=_platform_perms(),
                db=db,
            )

    assert exc_info.value.status_code == 502
    assert len(log_calls) == 1
    evt = log_calls[0]
    assert evt["action"] == "platform_admin.create_tenant_grant_role_failed"
    assert evt["details"]["target_email"] == "owner@acme.example"
    assert "grant 502" in evt["details"]["error"]
    assert len(evt["details"]["error"]) <= 200


@pytest.mark.asyncio
async def test_audit_emit_failure_falls_back_to_structlog() -> None:
    """AC6.3 — when log_event itself raises, structlog platform_admin_audit_emit_failed is logged."""
    import structlog.testing

    from app.api.admin.platform_manage import PlatformInviteRequest, platform_invite

    org = _org()

    with (
        patch("app.api.admin.platform_manage._load_org_or_404", new=AsyncMock(return_value=org)),
        patch("app.api.admin.platform_manage.log_event", side_effect=Exception("DB aborted")),
        patch("app.api.admin.platform_manage.zitadel") as mock_zitadel,
    ):
        mock_zitadel.invite_user = AsyncMock(side_effect=RuntimeError("zitadel 502"))
        mock_zitadel.remove_user = AsyncMock()

        with structlog.testing.capture_logs() as log_output:
            with pytest.raises(HTTPException):
                await platform_invite(
                    org_id=42,
                    body=PlatformInviteRequest(
                        email="probe@attacker.example",
                        first_name="Probe",
                        last_name="Attacker",
                        role="personal",
                    ),
                    perms=_platform_perms(),
                )

    # Must have logged platform_admin_audit_emit_failed with structlog
    audit_fail_logs = [e for e in log_output if e.get("event") == "platform_admin_audit_emit_failed"]
    assert len(audit_fail_logs) == 1, f"Expected exactly 1 audit-emit-failed log, got: {log_output}"
