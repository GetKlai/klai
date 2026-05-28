"""Regression tests for platform-admin cross-tenant write hardening."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
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


def _http_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://auth.example.test/v2/users/human")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("Zitadel error", request=request, response=response)


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
    """Endpoint delegates to delete_user_with_state_machine with delete_global_identity=False
    when remaining_count > 0. The orchestrator handles the actual deletion."""
    from app.api.admin.platform_manage import platform_delete_user
    from app.services.user_memberships import UserMembershipSummary

    db = AsyncMock()
    db.add = MagicMock()
    setup_db(db, [FakeResult([_org()]), FakeResult([_user()])])
    preview = MagicMock(personal_kbs=[], org_kbs_solely_owned=[])

    orchestrator_mock = AsyncMock(return_value=True)  # True = success

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
        patch(
            "app.api.admin.platform_manage.delete_user_with_state_machine",
            new=orchestrator_mock,
        ),
        patch("app.api.admin.platform_manage.fire_role_change_notification"),
    ):
        response = await platform_delete_user(org_id=42, zitadel_user_id="target-user", perms=_platform_perms())

    orchestrator_mock.assert_awaited_once()
    call_kwargs = orchestrator_mock.await_args.kwargs
    # Multi-tenant user: delete_global_identity must be False
    assert call_kwargs["delete_global_identity"] is False
    assert response.message == "Gebruiker uit tenant verwijderd."


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
# REQ-10 (Finding A-3): platform_create_tenant owner-user INSERT MUST use
# tenant_scoped_session(org_row.id), NOT set_tenant on the request session.
# Conforms to standards.md § 3 — request-scoped session is never mutated.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_platform_create_tenant_user_insert_uses_tenant_scoped_session() -> None:
    """AC10.1 + AC10.2 — owner-user INSERT runs inside
    tenant_scoped_session(org_row.id); set_tenant is NOT called on the
    request-scoped session passed via Depends(get_db).
    """
    from app.api.admin.platform_manage import CreateTenantRequest, platform_create_tenant

    db = AsyncMock()
    db.add = MagicMock()
    background_tasks = MagicMock()

    # Simulate autoincrement: any PortalOrg added to `db` gets id=12345 after flush/commit.
    new_org_id = 12345

    async def _fake_commit() -> None:
        for call in db.add.call_args_list:
            (obj,) = call.args
            if obj.__class__.__name__ == "PortalOrg" and getattr(obj, "id", None) is None:
                obj.id = new_org_id

    db.commit.side_effect = _fake_commit
    db.flush = AsyncMock(side_effect=_fake_commit)

    tdb_session = AsyncMock()
    tdb_session.add = MagicMock()

    tenant_scoped_mock = MagicMock(return_value=AsyncContext(tdb_session))
    cross_org_mock = MagicMock(return_value=AsyncContext(AsyncMock()))

    with (
        patch("app.api.admin.platform_manage.tenant_scoped_session", new=tenant_scoped_mock),
        patch("app.api.admin.platform_manage.cross_org_session", new=cross_org_mock),
        patch("app.api.auth.invalidate_tenant_slug_cache"),
        patch("app.api.admin.platform_manage.provision_tenant", new=AsyncMock()),
        patch("app.api.admin.platform_manage.log_event", new=AsyncMock()),
        patch("app.api.admin.platform_manage.zitadel") as mock_zitadel,
    ):
        mock_zitadel.create_org = AsyncMock(return_value={"id": "z-org-new"})
        mock_zitadel.invite_user = AsyncMock(return_value={"userId": "owner-user"})
        mock_zitadel.grant_user_role = AsyncMock()
        mock_zitadel.send_invite_code = AsyncMock()

        response = await platform_create_tenant(
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

    # AC10.1 — user INSERT issued via tenant_scoped_session
    tenant_scoped_mock.assert_called_once_with(new_org_id)
    tdb_session.add.assert_called_once()
    added_user = tdb_session.add.call_args.args[0]
    assert added_user.__class__.__name__ == "PortalUser"
    assert added_user.org_id == new_org_id
    tdb_session.commit.assert_awaited_once()

    # AC10.2 — request-scoped session is NOT mutated by set_tenant. We assert
    # this structurally: set_tenant is no longer imported in platform_manage.
    import app.api.admin.platform_manage as platform_manage_module

    assert not hasattr(platform_manage_module, "set_tenant"), (
        "set_tenant must not be imported in platform_manage.py — REQ-10 "
        "eliminated all set_tenant call-sites on the request session."
    )

    assert response.org_id == new_org_id
    assert response.owner_user_id == "owner-user"


@pytest.mark.asyncio
async def test_platform_create_tenant_reuses_existing_owner_identity() -> None:
    from app.api.admin.platform_manage import CreateTenantRequest, platform_create_tenant

    db = AsyncMock()
    db.add = MagicMock()
    background_tasks = MagicMock()
    new_org_id = 12346

    async def _fake_commit() -> None:
        for call in db.add.call_args_list:
            (obj,) = call.args
            if obj.__class__.__name__ == "PortalOrg" and getattr(obj, "id", None) is None:
                obj.id = new_org_id

    db.commit.side_effect = _fake_commit
    db.flush = AsyncMock(side_effect=_fake_commit)

    tdb_session = AsyncMock()
    tdb_session.add = MagicMock()

    with (
        patch("app.api.admin.platform_manage.tenant_scoped_session", return_value=AsyncContext(tdb_session)),
        patch("app.api.auth.invalidate_tenant_slug_cache"),
        patch("app.api.admin.platform_manage.provision_tenant", new=AsyncMock()),
        patch("app.api.admin.platform_manage.log_event", new=AsyncMock()),
        patch("app.api.admin.platform_manage.zitadel") as mock_zitadel,
    ):
        mock_zitadel.create_org = AsyncMock(return_value={"id": "z-org-new"})
        mock_zitadel.invite_user = AsyncMock(side_effect=_http_error(409))
        mock_zitadel.find_user_id_by_email = AsyncMock(return_value="existing-owner")
        mock_zitadel.grant_user_role = AsyncMock()
        mock_zitadel.send_invite_code = AsyncMock()
        mock_zitadel.remove_user = AsyncMock()

        response = await platform_create_tenant(
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

    mock_zitadel.find_user_id_by_email.assert_awaited_once_with("owner@acme.example")
    mock_zitadel.grant_user_role.assert_awaited_once()
    mock_zitadel.remove_user.assert_not_awaited()
    assert response.org_id == new_org_id
    assert response.owner_user_id == "existing-owner"


@pytest.mark.asyncio
async def test_platform_create_tenant_rolls_back_org_when_owner_insert_fails() -> None:
    """REQ-10 follow-up — if the tenant-scoped owner INSERT fails AFTER the
    org row was committed, the orphan org must be cleaned up via a cross-org
    session DELETE plus the Zitadel rollbacks must fire.
    """
    from app.api.admin.platform_manage import CreateTenantRequest, platform_create_tenant

    db = AsyncMock()
    db.add = MagicMock()
    background_tasks = MagicMock()
    new_org_id = 9999

    async def _fake_commit() -> None:
        for call in db.add.call_args_list:
            (obj,) = call.args
            if obj.__class__.__name__ == "PortalOrg" and getattr(obj, "id", None) is None:
                obj.id = new_org_id

    db.commit.side_effect = _fake_commit
    db.flush = AsyncMock(side_effect=_fake_commit)

    # The tenant-scoped session itself raises on commit (simulate FK/RLS failure).
    tdb_session = AsyncMock()
    tdb_session.add = MagicMock()
    tdb_session.commit = AsyncMock(side_effect=RuntimeError("user insert failed"))

    cleanup_db = AsyncMock()
    tenant_scoped_mock = MagicMock(return_value=AsyncContext(tdb_session))
    cross_org_mock = MagicMock(return_value=AsyncContext(cleanup_db))

    with (
        patch("app.api.admin.platform_manage.tenant_scoped_session", new=tenant_scoped_mock),
        patch("app.api.admin.platform_manage.cross_org_session", new=cross_org_mock),
        patch("app.api.auth.invalidate_tenant_slug_cache"),
        patch("app.api.admin.platform_manage.provision_tenant", new=AsyncMock()),
        patch("app.api.admin.platform_manage.log_event", new=AsyncMock()),
        patch("app.api.admin.platform_manage.zitadel") as mock_zitadel,
    ):
        mock_zitadel.create_org = AsyncMock(return_value={"id": "z-org-new"})
        mock_zitadel.invite_user = AsyncMock(return_value={"userId": "owner-user"})
        mock_zitadel.grant_user_role = AsyncMock()
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
    cleanup_db.execute.assert_awaited()  # cross_org_session ran the DELETE
    cleanup_db.commit.assert_awaited_once()
    mock_zitadel.remove_user.assert_awaited_once()
    assert (
        mock_zitadel.remove_user.await_args.kwargs.get("zitadel_user_id") == "owner-user"
        or "owner-user" in mock_zitadel.remove_user.await_args.args
    )
    mock_zitadel.delete_org.assert_awaited_once_with("z-org-new")


@pytest.mark.asyncio
async def test_platform_create_tenant_reconciles_existing_owner_grant_when_owner_insert_fails() -> None:
    """Existing Zitadel identities are not deleted on rollback, but their
    global admin grant is reconciled with persisted DB memberships.
    """
    from app.api.admin.platform_manage import CreateTenantRequest, platform_create_tenant

    db = AsyncMock()
    db.add = MagicMock()
    background_tasks = MagicMock()
    new_org_id = 12347

    async def _fake_commit() -> None:
        for call in db.add.call_args_list:
            (obj,) = call.args
            if obj.__class__.__name__ == "PortalOrg" and getattr(obj, "id", None) is None:
                obj.id = new_org_id

    db.commit.side_effect = _fake_commit
    db.flush = AsyncMock(side_effect=_fake_commit)

    tdb_session = AsyncMock()
    tdb_session.add = MagicMock()
    tdb_session.commit = AsyncMock(side_effect=RuntimeError("owner insert failed"))

    cleanup_db = AsyncMock()
    sync_mock = AsyncMock()

    with (
        patch("app.api.admin.platform_manage.tenant_scoped_session", return_value=AsyncContext(tdb_session)),
        patch("app.api.admin.platform_manage.cross_org_session", return_value=AsyncContext(cleanup_db)),
        patch("app.api.admin.platform_manage._sync_zitadel_role_grant", new=sync_mock),
        patch("app.api.auth.invalidate_tenant_slug_cache"),
        patch("app.api.admin.platform_manage.provision_tenant", new=AsyncMock()),
        patch("app.api.admin.platform_manage.log_event", new=AsyncMock()),
        patch("app.api.admin.platform_manage.zitadel") as mock_zitadel,
    ):
        mock_zitadel.create_org = AsyncMock(return_value={"id": "z-org-new"})
        mock_zitadel.invite_user = AsyncMock(side_effect=_http_error(409))
        mock_zitadel.find_user_id_by_email = AsyncMock(return_value="existing-owner")
        mock_zitadel.grant_user_role = AsyncMock()
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
    mock_zitadel.remove_user.assert_not_awaited()
    sync_mock.assert_awaited_once_with(
        zitadel_user_id="existing-owner",
        old_role="company",
        new_role="company",
    )
    mock_zitadel.delete_org.assert_awaited_once_with("z-org-new")


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
