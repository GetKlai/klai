"""The sign-in identity follows the person's memberships.

One Zitadel identity serves every organisation a person belongs to. Suspend,
reactivate and offboard change one ``portal_users`` row; afterwards the
identity can sign in exactly when at least one membership is still active:

- suspending or offboarding the last active membership deactivates it
  (``POST /management/v1/users/{id}/_deactivate``);
- a person with another active membership keeps a working identity;
- reactivating a membership of a deactivated identity reactivates it
  (``POST /management/v1/users/{id}/_reactivate``);
- bringing an offboarded member back reactivates the identity only when it is
  inactive, so a person still active elsewhere can be invited back;
- removing a membership or leaving an organisation runs the same rule;
- repeating suspend, reactivate or offboard runs the rule again, so a provider
  failure after the commit can be repaired by repeating the action;
- an identity the provider no longer has leaves nothing to bring in line;
- a membership that is no longer active is refused by the portal's auth gate.

The fake identity provider answers like Zitadel's user commands
(internal/command/user.go): ``_deactivate`` on an inactive user and
``_reactivate`` on a user that is not inactive fail with a precondition error.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials

from tests.conftest import make_perms
from tests.test_user_lifecycle import _mock_org, _mock_user, _offboard_db_mock

USER_ID = "user-1"
ORG_ID = 1
OTHER_ORG_ID = 2


class FakeZitadel:
    """Holds one identity's state and records the user-state calls made on it."""

    def __init__(self, state: str) -> None:
        self.state = state
        self.calls: list[str] = []
        self.down = False
        self.missing = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            return httpx.Response(503, json={"message": "unavailable"})
        path = request.url.path
        if self.missing:
            return httpx.Response(404, json={"message": "Errors.User.NotFound"})
        if request.method == "POST" and path == f"/v2/users/{USER_ID}/invite_code":
            self.calls.append("invite_code")
            return httpx.Response(200, json={})
        if request.method == "GET" and path == f"/management/v1/users/{USER_ID}":
            return httpx.Response(200, json={"user": {"id": USER_ID, "state": self.state}})
        if request.method == "POST" and path == f"/management/v1/users/{USER_ID}/_deactivate":
            self.calls.append("_deactivate")
            if self.state == "USER_STATE_INITIAL":
                return httpx.Response(404, json={"message": "Errors.User.CantDeactivateInitial"})
            if self.state == "USER_STATE_INACTIVE":
                return httpx.Response(400, json={"message": "Errors.User.AlreadyInactive"})
            self.state = "USER_STATE_INACTIVE"
            return httpx.Response(200, json={})
        if request.method == "POST" and path == f"/management/v1/users/{USER_ID}/_reactivate":
            self.calls.append("_reactivate")
            if self.state != "USER_STATE_INACTIVE":
                return httpx.Response(400, json={"message": "Errors.User.NotInactive"})
            self.state = "USER_STATE_ACTIVE"
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"message": f"unexpected {request.method} {path}"})


class _Rows:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self._rows = rows

    def all(self) -> list[SimpleNamespace]:
        return self._rows


class _MembershipSession:
    """Stands in for the fresh session the membership helpers open."""

    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self._rows = rows

    async def __aenter__(self) -> _MembershipSession:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def execute(self, _stmt: object) -> _Rows:
        return _Rows(self._rows)


def _membership(org_id: int, status_: str) -> SimpleNamespace:
    return SimpleNamespace(org_id=org_id, role="company", status=status_, slug=f"org-{org_id}")


@contextmanager
def _world(fake: FakeZitadel, memberships_after: list[SimpleNamespace]) -> Iterator[AsyncMock]:
    """Patch the identity provider and the committed memberships the routes read back; yield the audit log."""
    from app.services.zitadel import zitadel

    client = httpx.AsyncClient(base_url="https://idp.example.com", transport=httpx.MockTransport(fake.handle))
    with (
        patch.object(zitadel, "_http", client),
        patch(
            "app.services.user_memberships.AsyncSessionLocal",
            side_effect=lambda: _MembershipSession(memberships_after),
        ),
        patch("app.api.admin.users.log_event", new=AsyncMock()) as audit,
    ):
        yield audit


def _single_user_db(user: MagicMock) -> AsyncMock:
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    db.execute.return_value = result
    return db


def _admin():
    return make_perms(role="admin", user_id="admin-1", org_id=ORG_ID)


# ---------------------------------------------------------------------------
# Suspend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_suspend_of_only_membership_deactivates_identity() -> None:
    from app.api.admin.users import suspend_user

    fake = FakeZitadel("USER_STATE_ACTIVE")
    user = _mock_user(status="active")
    with _world(fake, [_membership(ORG_ID, "suspended")]):
        await suspend_user(zitadel_user_id=USER_ID, perms=_admin(), db=_single_user_db(user))

    assert user.status == "suspended"
    assert fake.calls == ["_deactivate"]
    assert fake.state == "USER_STATE_INACTIVE"


@pytest.mark.asyncio
async def test_admin_suspend_keeps_identity_for_other_active_membership() -> None:
    from app.api.admin.users import suspend_user

    fake = FakeZitadel("USER_STATE_ACTIVE")
    user = _mock_user(status="active")
    with _world(fake, [_membership(ORG_ID, "suspended"), _membership(OTHER_ORG_ID, "active")]):
        await suspend_user(zitadel_user_id=USER_ID, perms=_admin(), db=_single_user_db(user))

    assert user.status == "suspended"
    assert fake.calls == []
    assert fake.state == "USER_STATE_ACTIVE"


@pytest.mark.asyncio
async def test_admin_suspend_of_never_signed_in_identity_succeeds() -> None:
    """A legacy initial identity cannot sign in and cannot be deactivated; the suspend still answers 200."""
    from app.api.admin.users import suspend_user

    fake = FakeZitadel("USER_STATE_INITIAL")
    user = _mock_user(status="active")
    with _world(fake, [_membership(ORG_ID, "suspended")]):
        result = await suspend_user(zitadel_user_id=USER_ID, perms=_admin(), db=_single_user_db(user))

    assert "suspended" in result.message
    assert fake.calls == []
    assert fake.state == "USER_STATE_INITIAL"


@pytest.mark.asyncio
async def test_admin_suspend_reports_identity_provider_failure_after_commit() -> None:
    """The membership stays suspended; the admin gets a 502 and the audit log a trace."""
    from app.api.admin.users import suspend_user

    fake = FakeZitadel("USER_STATE_ACTIVE")
    fake.down = True
    user = _mock_user(status="active")
    db = _single_user_db(user)
    with _world(fake, [_membership(ORG_ID, "suspended")]) as audit:
        with pytest.raises(HTTPException) as exc:
            await suspend_user(zitadel_user_id=USER_ID, perms=_admin(), db=db)

    assert exc.value.status_code == status.HTTP_502_BAD_GATEWAY
    db.commit.assert_awaited_once()
    assert user.status == "suspended"
    actions = [call.kwargs["action"] for call in audit.await_args_list]
    assert "user.identity_sync_failed" in actions


# ---------------------------------------------------------------------------
# Reactivate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_reactivate_restores_deactivated_identity() -> None:
    from app.api.admin.users import reactivate_user

    fake = FakeZitadel("USER_STATE_INACTIVE")
    user = _mock_user(status="suspended")
    with _world(fake, [_membership(ORG_ID, "active")]):
        await reactivate_user(zitadel_user_id=USER_ID, perms=_admin(), db=_single_user_db(user))

    assert user.status == "active"
    assert fake.calls == ["_reactivate"]
    assert fake.state == "USER_STATE_ACTIVE"


@pytest.mark.asyncio
async def test_admin_reactivate_leaves_active_identity_untouched() -> None:
    """Suspended here while active elsewhere: the identity was never deactivated."""
    from app.api.admin.users import reactivate_user

    fake = FakeZitadel("USER_STATE_ACTIVE")
    user = _mock_user(status="suspended")
    with _world(fake, [_membership(ORG_ID, "active"), _membership(OTHER_ORG_ID, "active")]):
        await reactivate_user(zitadel_user_id=USER_ID, perms=_admin(), db=_single_user_db(user))

    assert user.status == "active"
    assert fake.calls == []


# ---------------------------------------------------------------------------
# Offboard
# ---------------------------------------------------------------------------


async def _offboard(fake: FakeZitadel, user: MagicMock, memberships_after: list[SimpleNamespace]):
    from app.api.admin.users import offboard_user

    org = _mock_org(org_id=ORG_ID)
    org.slug = "org-1"
    org.zitadel_org_id = "zitadel-org-1"
    with _world(fake, memberships_after):
        return await offboard_user(zitadel_user_id=USER_ID, body=None, perms=_admin(), db=_offboard_db_mock(user, org))


@pytest.mark.asyncio
async def test_offboard_keeps_identity_for_other_active_membership() -> None:
    fake = FakeZitadel("USER_STATE_ACTIVE")
    user = _mock_user(status="active")

    await _offboard(fake, user, [_membership(ORG_ID, "offboarded"), _membership(OTHER_ORG_ID, "active")])

    assert user.status == "offboarded"
    assert fake.calls == []
    assert fake.state == "USER_STATE_ACTIVE"


@pytest.mark.asyncio
async def test_offboard_of_last_membership_deactivates_identity() -> None:
    fake = FakeZitadel("USER_STATE_ACTIVE")
    user = _mock_user(status="active")

    await _offboard(fake, user, [_membership(ORG_ID, "offboarded")])

    assert fake.calls == ["_deactivate"]
    assert fake.state == "USER_STATE_INACTIVE"


@pytest.mark.asyncio
async def test_offboard_of_suspended_user_with_deactivated_identity_succeeds() -> None:
    """Suspend already deactivated the identity; offboarding leaves it so and answers 200."""
    fake = FakeZitadel("USER_STATE_INACTIVE")
    user = _mock_user(status="suspended")

    result = await _offboard(fake, user, [_membership(ORG_ID, "offboarded")])

    assert user.status == "offboarded"
    assert "offboarded" in result.message
    assert fake.calls == []
    assert fake.state == "USER_STATE_INACTIVE"


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_offboarded_membership_is_refused_by_the_auth_gate() -> None:
    """The identity stays usable for the other organisation, not for this one."""
    from app.core import permissions as perms_module

    with (
        patch.object(perms_module.zitadel, "get_userinfo", new=AsyncMock(return_value={"sub": USER_ID})),
        patch.object(
            perms_module, "resolve_user_permissions", new=AsyncMock(return_value=make_perms(status="offboarded"))
        ),
        patch.object(perms_module, "set_tenant", new=AsyncMock()),
    ):
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")
        with pytest.raises(HTTPException) as exc:
            await perms_module._resolve_caller_with_options(creds, AsyncMock(), allow_during_deprovisioning=False)

    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
    assert isinstance(exc.value.detail, dict)
    assert exc.value.detail.get("error_code") == "user_offboarded"


# ---------------------------------------------------------------------------
# Repeating an action brings the identity in line again
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repeated_suspend_brings_identity_in_line_and_answers_409() -> None:
    from app.api.admin.users import suspend_user

    fake = FakeZitadel("USER_STATE_ACTIVE")
    user = _mock_user(status="suspended")
    with _world(fake, [_membership(ORG_ID, "suspended")]):
        with pytest.raises(HTTPException) as exc:
            await suspend_user(zitadel_user_id=USER_ID, perms=_admin(), db=_single_user_db(user))

    assert exc.value.status_code == status.HTTP_409_CONFLICT
    assert fake.calls == ["_deactivate"]


@pytest.mark.asyncio
async def test_repeated_reactivate_brings_identity_in_line_and_answers_409() -> None:
    from app.api.admin.users import reactivate_user

    fake = FakeZitadel("USER_STATE_INACTIVE")
    user = _mock_user(status="active")
    with _world(fake, [_membership(ORG_ID, "active")]):
        with pytest.raises(HTTPException) as exc:
            await reactivate_user(zitadel_user_id=USER_ID, perms=_admin(), db=_single_user_db(user))

    assert exc.value.status_code == status.HTTP_409_CONFLICT
    assert fake.calls == ["_reactivate"]


@pytest.mark.asyncio
async def test_repeated_offboard_brings_identity_in_line_and_answers_409() -> None:
    from app.api.admin.users import offboard_user

    fake = FakeZitadel("USER_STATE_ACTIVE")
    user = _mock_user(status="offboarded")
    with _world(fake, [_membership(ORG_ID, "offboarded")]):
        with pytest.raises(HTTPException) as exc:
            await offboard_user(zitadel_user_id=USER_ID, body=None, perms=_admin(), db=_single_user_db(user))

    assert exc.value.status_code == status.HTTP_409_CONFLICT
    assert fake.calls == ["_deactivate"]


@pytest.mark.asyncio
async def test_suspend_of_identity_unknown_to_provider_succeeds() -> None:
    from app.api.admin.users import suspend_user

    fake = FakeZitadel("USER_STATE_ACTIVE")
    fake.missing = True
    user = _mock_user(status="active")
    with _world(fake, [_membership(ORG_ID, "suspended")]):
        result = await suspend_user(zitadel_user_id=USER_ID, perms=_admin(), db=_single_user_db(user))

    assert "suspended" in result.message
    assert fake.calls == []


# ---------------------------------------------------------------------------
# Bringing an offboarded member back
# ---------------------------------------------------------------------------


@contextmanager
def _invite_world(fake: FakeZitadel) -> Iterator[None]:
    with (
        _world(fake, []),
        patch("app.services.default_knowledge_bases.create_default_personal_kb", new=AsyncMock()),
    ):
        yield


@pytest.mark.asyncio
async def test_resend_invite_brings_back_offboarded_member_still_active_elsewhere() -> None:
    from app.api.admin.users import resend_invite

    fake = FakeZitadel("USER_STATE_ACTIVE")
    user = _mock_user(status="offboarded")
    db = _single_user_db(user)
    with _invite_world(fake):
        result = await resend_invite(zitadel_user_id=USER_ID, perms=_admin(), db=db)

    assert result.message == "Invitation resent."
    assert user.status == "active"
    assert fake.calls == ["invite_code"]
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_resend_invite_reactivates_deactivated_identity_of_offboarded_member() -> None:
    from app.api.admin.users import resend_invite

    fake = FakeZitadel("USER_STATE_INACTIVE")
    user = _mock_user(status="offboarded")
    with _invite_world(fake):
        await resend_invite(zitadel_user_id=USER_ID, perms=_admin(), db=_single_user_db(user))

    assert fake.calls == ["_reactivate", "invite_code"]
    assert fake.state == "USER_STATE_ACTIVE"


@pytest.mark.asyncio
async def test_failed_invite_back_keeps_identity_another_organisation_needs() -> None:
    """The invite mail fails after the member is restored; the rollback leaves the identity active."""
    from app.api.admin.users import _persist_invited_user_and_send_code

    fake = FakeZitadel("USER_STATE_ACTIVE")
    membership = _mock_user(status="active")
    db = AsyncMock()
    db.commit = AsyncMock(side_effect=RuntimeError("commit failed"))
    body = SimpleNamespace(email="person@example.com", first_name="Ada", last_name="Example")
    with _invite_world(fake):
        with pytest.raises(HTTPException) as exc:
            await _persist_invited_user_and_send_code(
                db=db,
                org=_mock_org(org_id=ORG_ID),
                body=body,  # type: ignore[arg-type]
                zitadel_user_id=USER_ID,
                user_row=membership,
                reactivated_membership=membership,
                reactivated_existing_zitadel_user=False,
                invite_url_template="https://my.example.com/password/set",
                cleanup_zitadel_user=AsyncMock(),
            )

    assert exc.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert "_deactivate" not in fake.calls
    assert fake.state == "USER_STATE_ACTIVE"


# ---------------------------------------------------------------------------
# Removing a membership and leaving an organisation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_removing_last_active_membership_deactivates_kept_identity() -> None:
    """Active here, suspended elsewhere: the identity stays for the other row but can no longer sign in."""
    from app.api.admin.users import remove_user

    fake = FakeZitadel("USER_STATE_ACTIVE")
    user = _mock_user(status="active")
    with (
        _world(fake, [_membership(OTHER_ORG_ID, "suspended")]),
        patch("app.api.admin.users.forget_internal_cases", new=AsyncMock()),
        patch("app.api.admin.users.fire_role_change_notification"),
    ):
        result = await remove_user(zitadel_user_id=USER_ID, perms=_admin(), db=_single_user_db(user))

    assert result.message == "User removed from organization."
    assert fake.calls == ["_deactivate"]


@pytest.mark.asyncio
async def test_leaving_last_active_membership_deactivates_kept_identity() -> None:
    from app.api.admin.users import leave_workspace

    fake = FakeZitadel("USER_STATE_ACTIVE")
    db = _single_user_db(_mock_user(status="active"))
    with (
        _world(fake, [_membership(OTHER_ORG_ID, "suspended")]),
        patch("app.api.admin.users.forget_internal_cases", new=AsyncMock()),
        patch("app.api.admin.users.emit_event"),
    ):
        await leave_workspace(perms=make_perms(role="company", user_id=USER_ID, org_id=ORG_ID), db=db)

    db.delete.assert_awaited_once()
    assert fake.calls == ["_deactivate"]
