"""Portal-driven OAuth link: /slack/connect and Bolt's OAuth callback. All values are synthetic."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
import pytest
from fastapi import FastAPI, Request, Response
from slack_bolt.oauth.async_oauth_flow import AsyncOAuthFlow
from slack_bolt.oauth.async_oauth_settings import AsyncOAuthSettings
from slack_bolt.request.async_request import AsyncBoltRequest
from slack_sdk.oauth.installation_store.models.installation import Installation
from structlog.testing import capture_logs

from app import db, portal_link, repo
from app.clients.portal import PortalClient
from app.config import get_settings
from app.crypto import decrypt
from app.services import allowed_kbs
from app.slack_app import CONNECT_PATH, build_oauth_settings, build_slack_app

# The synthetic test secret is shorter than the 32 bytes PyJWT recommends for HS256.
pytestmark = pytest.mark.filterwarnings("ignore::jwt.warnings.InsecureKeyLengthWarning")

SECRET = "test-portal-internal-secret"
STATE = "state-synthetic-1"
KEY = "pk_live_synthetic_key_0001"
RETURN_URL = "https://portal.example.com/admin/slack"
INSTALLATION = Installation(
    app_id="A1",
    team_id="T1",
    team_name="Voorbeeld",
    bot_token="xoxb-synthetic-0001",
    bot_id="B1",
    bot_user_id="UB1",
    bot_scopes="chat:write",
    user_id="U1",
)


def _token(secret: str = SECRET, typ: str = "slack_link", exp_in: int = 600) -> str:
    claims = {"typ": typ, "exp": int(time.time()) + exp_in, "return_url": RETURN_URL}
    return jwt.encode(claims, secret, algorithm="HS256")


class RecordingSession:
    def __init__(self) -> None:
        self.statements: list[Any] = []
        self.fail = False

    async def execute(self, stmt: Any) -> None:
        if self.fail:
            raise RuntimeError("db down")
        self.statements.append(stmt)


@dataclass
class Portal:
    status: int = 200
    body: Any = field(
        default_factory=lambda: {
            "org_id": 7,
            "org_name": "Voorbeeld BV",
            "key_id": "4f2c9a1e-0000-4000-8000-000000000042",
            "kb_ids": [1, 2],
            "api_key": KEY,
            "return_url": RETURN_URL,
        }
    )
    requests: list[httpx.Request] = field(default_factory=list)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/internal/slack/link-oauth":
            return httpx.Response(self.status, json=self.body)
        return httpx.Response(204)

    def paths(self) -> list[str]:
        return [f"{r.method} {r.url.path}" for r in self.requests]


class StateStore:
    async def async_issue(self, *args: Any, **kwargs: Any) -> str:
        return STATE

    async def async_consume(self, state: str) -> bool:
        return state == STATE


class InstallationStore:
    async def async_save(self, installation: Installation) -> None:
        return None


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> RecordingSession:
    recording = RecordingSession()

    @asynccontextmanager
    async def _session() -> AsyncIterator[Any]:
        yield recording

    monkeypatch.setattr(db, "session", _session)
    return recording


@pytest.fixture
def pending(monkeypatch: pytest.MonkeyPatch, session: RecordingSession) -> dict[str, str]:
    rows: dict[str, str] = {}

    async def add(_s: object, state: str, token: str) -> None:
        rows[state] = token

    async def pop(_s: object, state: str) -> str | None:
        return rows.pop(state, None)

    monkeypatch.setattr(repo, "add_pending_link", add)
    monkeypatch.setattr(repo, "pop_pending_link", pop)
    return rows


@pytest.fixture
def portal() -> Portal:
    return Portal()


@pytest.fixture
def oauth(portal: Portal) -> tuple[AsyncOAuthSettings, AsyncOAuthFlow]:
    client = PortalClient(
        "http://portal-api.test:8010", SECRET, transport=httpx.MockTransport(portal.handle)
    )
    settings = build_oauth_settings(get_settings(), InstallationStore(), StateStore(), client)  # pyright: ignore[reportArgumentType]
    flow = build_slack_app(get_settings(), settings, client).oauth_flow
    assert flow is not None

    async def run_installation(code: str) -> Installation:
        return INSTALLATION

    flow.run_installation = run_installation  # pyright: ignore[reportAttributeAccessIssue]
    return settings, flow


def _callback(query: str) -> AsyncBoltRequest:
    return AsyncBoltRequest(
        body="", query=query, headers={"cookie": f"slack-app-oauth-state={STATE}"}
    )


def _location(response: Any) -> str:
    return response.headers["location"][0]


async def _connect(oauth: AsyncOAuthSettings, method: str, **kwargs: Any) -> httpx.Response:
    """Drive ``/slack/connect`` over HTTP, the way ``app.main`` routes it."""
    app = FastAPI()

    @app.api_route(CONNECT_PATH, methods=["GET", "POST"])
    async def route(request: Request) -> Response:
        return await portal_link.connect(request, oauth, SECRET)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://slack.example.com"
    ) as client:
        return await client.request(method, CONNECT_PATH, **kwargs)


def _assert_not_cached(response: httpx.Response) -> None:
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "claims",
    [{"secret": "another-secret"}, {"exp_in": -60}, {"typ": "invite"}, None],
    ids=["bad_signature", "expired", "wrong_typ", "missing"],
)
async def test_connect_rejects_an_invalid_token(
    oauth: tuple[AsyncOAuthSettings, AsyncOAuthFlow],
    pending: dict[str, str],
    claims: dict[str, Any] | None,
) -> None:
    form = {} if claims is None else {"token": _token(**claims)}
    response = await _connect(oauth[0], "POST", data=form)
    assert response.status_code == 400
    assert portal_link.INVALID_LINK in response.text
    _assert_not_cached(response)
    assert pending == {}


async def test_connect_refuses_a_body_above_the_limit(
    oauth: tuple[AsyncOAuthSettings, AsyncOAuthFlow],
    pending: dict[str, str],
) -> None:
    response = await _connect(
        oauth[0], "POST", data={"token": "a" * (portal_link.MAX_BODY_BYTES + 1)}
    )
    assert response.status_code == 413
    assert portal_link.INVALID_LINK in response.text
    _assert_not_cached(response)
    assert pending == {}


@pytest.mark.parametrize(
    ("method", "carrier", "status"),
    [("GET", "params", 405), ("POST", "json", 400)],
    ids=["get_with_query_token", "post_with_json_body"],
)
async def test_connect_reads_a_valid_token_only_from_a_form_post(
    oauth: tuple[AsyncOAuthSettings, AsyncOAuthFlow],
    pending: dict[str, str],
    method: str,
    carrier: str,
    status: int,
) -> None:
    response = await _connect(oauth[0], method, **{carrier: {"token": _token()}})
    assert response.status_code == status
    assert portal_link.INVALID_LINK in response.text
    _assert_not_cached(response)
    assert pending == {}


async def test_connect_redirects_to_slack_with_the_issued_state(
    oauth: tuple[AsyncOAuthSettings, AsyncOAuthFlow], pending: dict[str, str]
) -> None:
    token = _token()
    response = await _connect(oauth[0], "POST", data={"token": token})
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith(
        f"https://slack.com/oauth/v2/authorize?state={STATE}&client_id=1111.2222&scope="
    )
    assert "redirect_uri=https://slack.example.com/slack/oauth_redirect" in location
    # Bolt's callback refuses a state that this cookie does not carry.
    assert f"slack-app-oauth-state={STATE};" in response.headers["set-cookie"]
    _assert_not_cached(response)
    assert pending == {STATE: token}


async def test_callback_with_pending_link_stores_the_encrypted_key_and_returns_to_portal(
    oauth: tuple[AsyncOAuthSettings, AsyncOAuthFlow],
    pending: dict[str, str],
    session: RecordingSession,
    portal: Portal,
) -> None:
    token = _token()
    await portal_link.start(token, oauth[0], SECRET)
    allowed_kbs._cache["T1"] = object()  # pyright: ignore[reportArgumentType]

    response = await oauth[1].handle_callback(_callback(f"state={STATE}&code=c1"))

    assert response.status == 302
    assert _location(response) == f"{RETURN_URL}?slack=connected"
    (request,) = portal.requests
    assert request.headers["Authorization"] == f"Bearer {SECRET}"
    assert json.loads(request.content) == {
        "token": token,
        "team_id": "T1",
        "team_name": "Voorbeeld",
        "slack_user_id": "U1",
    }
    (insert,) = session.statements
    params = insert.compile().params
    assert params["team_id"] == "T1" and params["key_id"] == portal.body["key_id"]
    assert params["klai_api_key_enc"] != KEY
    assert decrypt(params["klai_api_key_enc"]) == KEY
    assert pending == {}
    assert "T1" not in allowed_kbs._cache


@pytest.mark.parametrize(
    ("status", "body", "outcome", "reason"),
    [
        (409, {"code": "team_already_linked"}, "already_linked", "team_already_linked"),
        (403, {"code": "not_admin"}, "denied", "not_admin"),
        (
            403,
            {"detail": {"error_code": "feature_not_unlocked", "feature": "slack"}},
            "denied",
            "feature_not_unlocked",
        ),
        (401, {"detail": "invalid token"}, "failed", "LinkKeyInvalid"),
        (503, {"detail": "down"}, "failed", "PortalUnavailable"),
    ],
)
async def test_portal_refusal_returns_to_portal_without_a_link(
    oauth: tuple[AsyncOAuthSettings, AsyncOAuthFlow],
    pending: dict[str, str],
    session: RecordingSession,
    portal: Portal,
    status: int,
    body: Any,
    outcome: str,
    reason: str,
) -> None:
    portal.status, portal.body = status, body
    await portal_link.start(_token(), oauth[0], SECRET)

    with capture_logs() as logs:
        response = await oauth[1].handle_callback(_callback(f"state={STATE}&code=c1"))

    assert _location(response) == f"{RETURN_URL}?slack={outcome}"
    (failed,) = [log for log in logs if log["event"] == "slack_portal_link_failed"]
    assert (failed["outcome"], failed["reason"]) == (outcome, reason)
    assert session.statements == []
    assert pending == {}


async def test_store_failure_releases_the_portal_link(
    oauth: tuple[AsyncOAuthSettings, AsyncOAuthFlow],
    pending: dict[str, str],
    session: RecordingSession,
    portal: Portal,
) -> None:
    await portal_link.start(_token(), oauth[0], SECRET)
    session.fail = True

    response = await oauth[1].handle_callback(_callback(f"state={STATE}&code=c1"))

    assert _location(response) == f"{RETURN_URL}?slack=failed"
    assert portal.paths() == ["POST /internal/slack/link-oauth", "DELETE /internal/slack/link/T1"]


async def test_cancel_in_slack_returns_denied(
    oauth: tuple[AsyncOAuthSettings, AsyncOAuthFlow], pending: dict[str, str], portal: Portal
) -> None:
    await portal_link.start(_token(), oauth[0], SECRET)

    response = await oauth[1].handle_callback(_callback(f"error=access_denied&state={STATE}"))

    assert _location(response) == f"{RETURN_URL}?slack=denied"
    assert portal.requests == []
    assert pending == {}


async def test_install_without_pending_link_keeps_bolts_page(
    oauth: tuple[AsyncOAuthSettings, AsyncOAuthFlow], pending: dict[str, str], portal: Portal
) -> None:
    response = await oauth[1].handle_callback(_callback(f"state={STATE}&code=c1"))

    assert response.status == 200
    assert "location" not in response.headers
    assert "text/html" in response.headers["content-type"][0]
    assert portal.requests == []
