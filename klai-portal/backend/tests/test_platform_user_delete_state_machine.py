"""RED tests for REQ-4: platform user-delete state machine.

SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-4 (Finding A-2, HIGH):

- Delete sequence must execute in order: Zitadel → External KB → portal_users DELETE
- On any step failure: write deletion_status='failed_partial', failure_reason JSONB,
  last_attempted_step TEXT to portal_users, emit audit event
  platform_admin.user_delete_partial_failure
- Retry endpoint restarts state machine from scratch (idempotent)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_perms


def _platform_perms():
    return make_perms(
        role="admin",
        user_id="platform-admin",
        org_id=1,
        org_slug="getklai",
        is_platform_admin=True,
    )


def _deletion_state(
    *,
    org_id: int = 42,
    zitadel_user_id: str = "target-user",
    delete_global_identity: bool = True,
    kbs: int = 2,
    api_keys: int = 1,
    mcp_tokens: int = 1,
):
    """Build a _UserDeletionState-like object for testing."""
    from app.services.user_deletion_orchestrator import _UserDeletionState

    state = _UserDeletionState(
        org_id=org_id,
        zitadel_user_id=zitadel_user_id,
        actor_user_id="platform-admin",
        delete_global_identity=delete_global_identity,
        kb_dispositions=[MagicMock() for _ in range(kbs)],
        api_keys_count=api_keys,
        mcp_tokens_count=mcp_tokens,
    )
    return state


# ---------------------------------------------------------------------------
# AC4.1 — Step ordering: Zitadel → External KB → portal_users DELETE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_user_steps_execute_in_order() -> None:
    """Steps must execute in the required order: zitadel_remove, external_kb_delete, portal_db_delete."""
    from app.services.user_deletion_orchestrator import _run_user_deletion

    call_order: list[str] = []

    async def fake_zitadel_remove(state):
        call_order.append("zitadel_remove")

    async def fake_kb_delete(state):
        call_order.append("external_kb_delete")

    async def fake_db_delete(state):
        call_order.append("portal_db_delete")

    with (
        patch(
            "app.services.user_deletion_steps.step_zitadel_remove",
            side_effect=fake_zitadel_remove,
        ),
        patch(
            "app.services.user_deletion_steps.step_external_kb_delete",
            side_effect=fake_kb_delete,
        ),
        patch(
            "app.services.user_deletion_steps.step_portal_db_delete",
            side_effect=fake_db_delete,
        ),
    ):
        db = AsyncMock()
        state = _deletion_state()
        await _run_user_deletion(state, db)

    assert call_order == ["zitadel_remove", "external_kb_delete", "portal_db_delete"]


@pytest.mark.asyncio
async def test_external_kb_delete_step_revokes_credentials_after_kb_dispositions() -> None:
    """Credential revoke belongs to the external cleanup step, after Zitadel removal has succeeded."""
    from app.services.user_deletion_steps import step_external_kb_delete

    call_order: list[str] = []

    async def _apply_dispositions(*_args, **_kwargs):
        call_order.append("apply_dispositions")

    async def _revoke_user_credentials(*_args, **_kwargs):
        call_order.append("revoke_user_credentials")
        return (3, 2)

    state = _deletion_state(api_keys=0, mcp_tokens=0)
    state.db_for_steps = AsyncMock()
    state.org = MagicMock()

    with (
        patch("app.services.kb_offboarding.apply_dispositions", new=AsyncMock(side_effect=_apply_dispositions)),
        patch(
            "app.services.kb_offboarding.revoke_user_credentials",
            new=AsyncMock(side_effect=_revoke_user_credentials),
        ),
    ):
        await step_external_kb_delete(state)

    assert call_order == ["apply_dispositions", "revoke_user_credentials"]
    assert state.kbs_deleted_externally == len(state.kb_dispositions)
    assert state.api_keys_count == 3
    assert state.mcp_tokens_count == 2


# ---------------------------------------------------------------------------
# AC4.2 — Zitadel step failure → failed_partial + audit event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zitadel_remove_failure_marks_failed_partial_and_emits_audit() -> None:
    """When zitadel_remove step fails, portal_users gets deletion_status=failed_partial
    and audit event platform_admin.user_delete_partial_failure is emitted."""
    from app.services.user_deletion_orchestrator import _run_user_deletion

    async def failing_zitadel(state):
        raise RuntimeError("Zitadel 502")

    async def ok_kb(state):
        pass

    async def ok_db(state):
        pass

    db = AsyncMock()
    log_event_mock = AsyncMock()
    state = _deletion_state()

    with (
        patch(
            "app.services.user_deletion_steps.step_zitadel_remove",
            side_effect=failing_zitadel,
        ),
        patch(
            "app.services.user_deletion_steps.step_external_kb_delete",
            side_effect=ok_kb,
        ),
        patch(
            "app.services.user_deletion_steps.step_portal_db_delete",
            side_effect=ok_db,
        ),
        patch(
            "app.services.user_deletion_orchestrator.log_event",
            new=log_event_mock,
        ),
        patch(
            "app.services.user_deletion_orchestrator._mark_user_delete_failed",
            new=AsyncMock(),
        ) as mark_failed,
    ):
        await _run_user_deletion(state, db)

    # _mark_user_delete_failed called with correct step name
    mark_failed.assert_awaited_once()
    call_kwargs = mark_failed.await_args
    assert call_kwargs.args[2] == "zitadel_remove" or call_kwargs.kwargs.get("step_name") == "zitadel_remove"

    # audit event emitted
    log_event_mock.assert_awaited_once()
    audit_action = log_event_mock.await_args.kwargs.get("action") or log_event_mock.await_args.args[0]
    assert audit_action == "platform_admin.user_delete_partial_failure"
    details = log_event_mock.await_args.kwargs["details"]
    assert details["step"] == "zitadel_remove"
    assert details["zitadel_identity_deleted"] is False
    assert details["db_user_deleted"] is False


# ---------------------------------------------------------------------------
# AC4.3 — External KB delete step failure → failed_partial + audit event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kb_delete_failure_marks_failed_partial_and_emits_audit() -> None:
    """When external_kb_delete step fails, portal_users gets deletion_status=failed_partial
    and audit event platform_admin.user_delete_partial_failure is emitted."""
    from app.services.user_deletion_orchestrator import _run_user_deletion

    async def ok_zitadel(state):
        state.zitadel_identity_deleted = True

    async def failing_kb(state):
        raise RuntimeError("knowledge-ingest 503")

    async def ok_db(state):
        pass

    db = AsyncMock()
    log_event_mock = AsyncMock()
    state = _deletion_state()

    with (
        patch(
            "app.services.user_deletion_steps.step_zitadel_remove",
            side_effect=ok_zitadel,
        ),
        patch(
            "app.services.user_deletion_steps.step_external_kb_delete",
            side_effect=failing_kb,
        ),
        patch(
            "app.services.user_deletion_steps.step_portal_db_delete",
            side_effect=ok_db,
        ),
        patch(
            "app.services.user_deletion_orchestrator.log_event",
            new=log_event_mock,
        ),
        patch(
            "app.services.user_deletion_orchestrator._mark_user_delete_failed",
            new=AsyncMock(),
        ) as mark_failed,
    ):
        await _run_user_deletion(state, db)

    mark_failed.assert_awaited_once()
    call_kwargs = mark_failed.await_args
    assert call_kwargs.args[2] == "external_kb_delete" or call_kwargs.kwargs.get("step_name") == "external_kb_delete"

    log_event_mock.assert_awaited_once()
    details = log_event_mock.await_args.kwargs["details"]
    assert details["step"] == "external_kb_delete"
    assert details["zitadel_identity_deleted"] is True  # already done
    assert details["db_user_deleted"] is False


# ---------------------------------------------------------------------------
# AC4.4 — portal_db_delete step failure → failed_partial + audit event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_portal_db_delete_failure_marks_failed_partial_and_emits_audit() -> None:
    """When portal_db_delete step fails, portal_users gets deletion_status=failed_partial."""
    from app.services.user_deletion_orchestrator import _run_user_deletion

    async def ok_zitadel(state):
        state.zitadel_identity_deleted = True

    async def ok_kb(state):
        state.kbs_deleted_externally = len(state.kb_dispositions)

    async def failing_db(state):
        raise RuntimeError("DB 500")

    db = AsyncMock()
    log_event_mock = AsyncMock()
    state = _deletion_state()

    with (
        patch(
            "app.services.user_deletion_steps.step_zitadel_remove",
            side_effect=ok_zitadel,
        ),
        patch(
            "app.services.user_deletion_steps.step_external_kb_delete",
            side_effect=ok_kb,
        ),
        patch(
            "app.services.user_deletion_steps.step_portal_db_delete",
            side_effect=failing_db,
        ),
        patch(
            "app.services.user_deletion_orchestrator.log_event",
            new=log_event_mock,
        ),
        patch(
            "app.services.user_deletion_orchestrator._mark_user_delete_failed",
            new=AsyncMock(),
        ) as mark_failed,
    ):
        await _run_user_deletion(state, db)

    mark_failed.assert_awaited_once()
    details = log_event_mock.await_args.kwargs["details"]
    assert details["step"] == "portal_db_delete"
    assert details["zitadel_identity_deleted"] is True
    assert details["db_user_deleted"] is False


# ---------------------------------------------------------------------------
# AC4.5 — Successful run emits platform_admin.user_deleted (not partial_failure)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_delete_emits_user_deleted_audit_event() -> None:
    """On successful completion, platform_admin.user_deleted is emitted (not partial_failure)."""
    from app.services.user_deletion_orchestrator import _run_user_deletion

    async def ok_zitadel(state):
        state.zitadel_identity_deleted = True

    async def ok_kb(state):
        state.kbs_deleted_externally = len(state.kb_dispositions)

    async def ok_db(state):
        state.db_user_deleted = True

    db = AsyncMock()
    log_event_mock = AsyncMock()
    state = _deletion_state()

    with (
        patch(
            "app.services.user_deletion_steps.step_zitadel_remove",
            side_effect=ok_zitadel,
        ),
        patch(
            "app.services.user_deletion_steps.step_external_kb_delete",
            side_effect=ok_kb,
        ),
        patch(
            "app.services.user_deletion_steps.step_portal_db_delete",
            side_effect=ok_db,
        ),
        patch(
            "app.services.user_deletion_orchestrator.log_event",
            new=log_event_mock,
        ),
    ):
        await _run_user_deletion(state, db)

    log_event_mock.assert_awaited_once()
    action = log_event_mock.await_args.kwargs["action"]
    assert action == "platform_admin.user_deleted"
    details = log_event_mock.await_args.kwargs["details"]
    assert details["zitadel_identity_deleted"] is True
    assert details["db_user_deleted"] is True


# ---------------------------------------------------------------------------
# AC4.6 — platform_delete_user endpoint calls orchestrator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_platform_delete_user_calls_orchestrator() -> None:
    """platform_delete_user endpoint must delegate to delete_user_with_state_machine."""
    from app.api.admin.platform_manage import platform_delete_user
    from app.services.user_memberships import UserMembershipSummary

    class AsyncContext:
        def __init__(self, value):
            self.value = value

        async def __aenter__(self):
            return self.value

        async def __aexit__(self, exc_type, exc, tb):
            return False

    db = AsyncMock()
    db.add = MagicMock()

    org = MagicMock()
    org.id = 42

    user = MagicMock()
    user.org_id = 42
    user.zitadel_user_id = "target-user"
    user.status = "active"

    from helpers import FakeResult, setup_db

    setup_db(db, [FakeResult([org]), FakeResult([user])])

    orchestrator_mock = AsyncMock()
    preview = MagicMock(personal_kbs=[], org_kbs_solely_owned=[])

    with (
        patch("app.api.admin.platform_manage.tenant_scoped_session", return_value=AsyncContext(db)),
        patch(
            "app.api.admin.platform_manage.get_user_membership_summary",
            new=AsyncMock(
                return_value=UserMembershipSummary(total_count=1, remaining_count=0, is_platform_admin=False)
            ),
        ),
        patch("app.services.kb_offboarding.compute_offboard_preview", new=AsyncMock(return_value=preview)),
        patch("app.services.kb_offboarding.revoke_user_credentials", new=AsyncMock(return_value=(2, 1))) as revoke,
        patch(
            "app.api.admin.platform_manage.delete_user_with_state_machine",
            new=orchestrator_mock,
        ),
        patch("app.api.admin.platform_manage.fire_role_change_notification"),
    ):
        await platform_delete_user(org_id=42, zitadel_user_id="target-user", perms=_platform_perms())

    orchestrator_mock.assert_awaited_once()
    revoke.assert_not_awaited()


# ---------------------------------------------------------------------------
# AC4.7 — retry-delete endpoint calls orchestrator from scratch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_delete_endpoint_calls_orchestrator() -> None:
    """POST /api/admin/platform/users/{zitadel_user_id}/retry-delete must call
    delete_user_with_state_machine (restart from scratch — idempotent)."""
    from app.api.admin.platform_manage import platform_retry_user_delete
    from app.services.user_memberships import UserMembershipSummary

    class AsyncContext:
        def __init__(self, value):
            self.value = value

        async def __aenter__(self):
            return self.value

        async def __aexit__(self, exc_type, exc, tb):
            return False

    db = AsyncMock()
    db.add = MagicMock()

    org = MagicMock()
    org.id = 42

    user = MagicMock()
    user.org_id = 42
    user.zitadel_user_id = "target-user"
    user.status = "active"
    user.deletion_status = "failed_partial"

    from helpers import FakeResult, setup_db

    setup_db(db, [FakeResult([org]), FakeResult([user])])

    orchestrator_mock = AsyncMock()
    preview = MagicMock(personal_kbs=[], org_kbs_solely_owned=[])

    with (
        patch("app.api.admin.platform_manage.tenant_scoped_session", return_value=AsyncContext(db)),
        patch(
            "app.api.admin.platform_manage.get_user_membership_summary",
            new=AsyncMock(
                return_value=UserMembershipSummary(total_count=1, remaining_count=0, is_platform_admin=False)
            ),
        ),
        patch("app.services.kb_offboarding.compute_offboard_preview", new=AsyncMock(return_value=preview)),
        patch("app.services.kb_offboarding.revoke_user_credentials", new=AsyncMock(return_value=(0, 0))) as revoke,
        patch(
            "app.api.admin.platform_manage.delete_user_with_state_machine",
            new=orchestrator_mock,
        ),
        patch("app.api.admin.platform_manage.fire_role_change_notification"),
    ):
        await platform_retry_user_delete(org_id=42, zitadel_user_id="target-user", perms=_platform_perms())

    orchestrator_mock.assert_awaited_once()
    revoke.assert_not_awaited()


# ---------------------------------------------------------------------------
# AC4.8 — _mark_user_delete_failed writes three columns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_user_delete_failed_writes_correct_columns() -> None:
    """_mark_user_delete_failed must update deletion_status, failure_reason, last_attempted_step."""
    from app.services.user_deletion_orchestrator import _mark_user_delete_failed

    db = AsyncMock()
    state = _deletion_state()

    await _mark_user_delete_failed(state, db, "zitadel_remove", "some error message")

    # Must call db.execute with an UPDATE that touches the three columns
    db.execute.assert_awaited_once()
    call_args = db.execute.await_args
    # The SQL text should reference the three columns
    sql_text = str(call_args.args[0])
    assert "deletion_status" in sql_text
    assert "failure_reason" in sql_text
    assert "last_attempted_step" in sql_text
    db.commit.assert_awaited_once()
