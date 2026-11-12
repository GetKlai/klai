"""An organisation's chat application serves only its active members.

Provisioning gives each organisation its own OIDC client for the chat
application (``portal_orgs.zitadel_librechat_client_id``). Completing a
sign-in for that client requires an active membership of that organisation,
and the chat's calls to the portal resolve only active members.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException, status

USER_ID = "user-1"
CHAT_CLIENT = "chat-client-of-org-1"
AUTH_REQUEST = "auth-req-1"


class FakeLogin:
    """The Zitadel login API: an auth request for one client, a session for one user."""

    def __init__(self, client_id: str) -> None:
        self.client_id = client_id
        self.finalized = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == f"/v2/oidc/auth_requests/{AUTH_REQUEST}":
            return httpx.Response(200, json={"authRequest": {"id": AUTH_REQUEST, "clientId": self.client_id}})
        if request.method == "GET" and path == "/v2/sessions/sid-1":
            return httpx.Response(200, json={"session": {"factors": {"user": {"id": USER_ID}}}})
        if request.method == "POST" and path == f"/v2/oidc/auth_requests/{AUTH_REQUEST}":
            self.finalized = True
            return httpx.Response(200, json={"callbackUrl": "https://chat.example.com/oauth/openid/callback"})
        return httpx.Response(404, json={"message": f"unexpected {request.method} {path}"})


class _Result:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object:
        return self._value


class _Session:
    """Answers the chat-client lookup, then the membership-status lookup, across sessions."""

    def __init__(self, answers: list[object]) -> None:
        self._answers = answers

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def execute(self, _stmt: object) -> _Result:
        return _Result(self._answers.pop(0))


async def _finalize(login: FakeLogin, org_id: int | None, membership_status: str | None) -> str:
    from app.services.zitadel import zitadel

    client = httpx.AsyncClient(base_url="https://idp.example.com", transport=httpx.MockTransport(login.handle))
    answers: list[object] = [org_id, membership_status]
    with (
        patch.object(zitadel, "_http", client),
        patch("app.core.database.AsyncSessionLocal", side_effect=lambda: _Session(answers)),
    ):
        return await zitadel.finalize_auth_request(
            auth_request_id=AUTH_REQUEST, session_id="sid-1", session_token="stk-1"
        )


@pytest.mark.asyncio
async def test_chat_signin_completes_for_active_member() -> None:
    login = FakeLogin(CHAT_CLIENT)

    callback = await _finalize(login, org_id=1, membership_status="active")

    assert login.finalized
    assert callback.startswith("https://chat.example.com/")


@pytest.mark.asyncio
@pytest.mark.parametrize("membership_status", ["suspended", "offboarded", None])
async def test_chat_signin_is_refused_without_active_membership(membership_status: str | None) -> None:
    login = FakeLogin(CHAT_CLIENT)

    with pytest.raises(HTTPException) as exc:
        await _finalize(login, org_id=1, membership_status=membership_status)

    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
    assert not login.finalized


@pytest.mark.asyncio
async def test_signin_for_a_client_that_is_no_organisation_chat_completes() -> None:
    login = FakeLogin("portal-client")

    await _finalize(login, org_id=None, membership_status=None)

    assert login.finalized


@pytest.mark.asyncio
@pytest.mark.parametrize("cached", [True, False])
async def test_chat_user_resolution_refuses_member_who_is_not_active(cached: bool) -> None:
    from app.services.internal_chat_identity import LibreChatIdentityError, resolve_librechat_user

    member = SimpleNamespace(zitadel_user_id=USER_ID, status="offboarded", librechat_user_id="0" * 24)
    db = AsyncMock()
    db.execute.side_effect = [_Result(member)] if cached else [_Result(None), _Result(member)]
    mongo = MagicMock()
    mongo.__getitem__.return_value.__getitem__.return_value.find_one = AsyncMock(return_value={"openidId": USER_ID})
    org = SimpleNamespace(id=1, librechat_container="chat-org-1", zitadel_org_id="z-org-1")

    with (
        patch("app.services.internal_chat_identity.set_tenant", new=AsyncMock()),
        patch("app.services.internal_chat_identity.AsyncIOMotorClient", return_value=mongo),
        patch("app.services.internal_chat_identity.settings") as settings,
    ):
        settings.librechat_mongo_root_uri = "mongodb://mongo.example.com"
        with pytest.raises(LibreChatIdentityError):
            await resolve_librechat_user(db, org, "0" * 24)  # type: ignore[arg-type]
