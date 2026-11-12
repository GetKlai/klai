"""The sign-in identity follows the person's memberships.

One Zitadel identity serves every organisation a person belongs to. Suspend,
reactivate and offboard change one ``portal_users`` row; afterwards the
identity can sign in exactly when at least one membership is still active:

- suspending or offboarding the last active membership deactivates it
  (``POST /management/v1/users/{id}/_deactivate``);
- a person with another active membership keeps a working identity;
- reactivating a membership of a deactivated identity reactivates it
  (``POST /management/v1/users/{id}/_reactivate``);
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

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            return httpx.Response(503, json={"message": "unavailable"})
        path = request.url.path
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
