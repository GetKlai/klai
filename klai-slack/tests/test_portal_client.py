from __future__ import annotations

import json

import httpx
import pytest

from app.clients.portal import (
    LinkConflict,
    LinkForbidden,
    LinkKeyInvalid,
    OAuthLinkResult,
    PortalClient,
    PortalUnavailable,
)

SECRET = "test-portal-internal-secret"
KEY = "pk_live_synthetic_key_0001"
TOKEN = "synthetic.link.token"
RETURN_URL = "https://portal.example.com/admin/slack"


def _client(status: int, body: object = None) -> tuple[list[httpx.Request], PortalClient]:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body)

    return seen, PortalClient(
        "http://portal-api.test:8010", SECRET, transport=httpx.MockTransport(handle)
    )


async def test_link_oauth_success() -> None:
    seen, client = _client(
        200,
        {
            "org_id": 7,
            "org_name": "Voorbeeld BV",
            "key_id": "4f2c9a1e-0000-4000-8000-000000000042",
            "kb_ids": [1, 2],
            "api_key": KEY,
            "return_url": RETURN_URL,
        },
    )
    result = await client.link_oauth(TOKEN, "T1", "Voorbeeld", "U1")
    assert result == OAuthLinkResult(
        org_id=7,
        org_name="Voorbeeld BV",
        key_id="4f2c9a1e-0000-4000-8000-000000000042",
        kb_ids=[1, 2],
        api_key=KEY,
        return_url=RETURN_URL,
    )
    assert seen[0].url.path == "/internal/slack/link-oauth"
    assert seen[0].headers["Authorization"] == f"Bearer {SECRET}"
    assert json.loads(seen[0].content) == {
        "token": TOKEN,
        "team_id": "T1",
        "team_name": "Voorbeeld",
        "slack_user_id": "U1",
    }


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (401, {"detail": "Invalid link token"}, LinkKeyInvalid),
        (403, {"code": "not_admin"}, LinkForbidden),
        (500, {"detail": "boom"}, PortalUnavailable),
    ],
)
async def test_link_oauth_error_mapping(status: int, body: object, error: type[Exception]) -> None:
    _, client = _client(status, body)
    with pytest.raises(error):
        await client.link_oauth(TOKEN, "T1", "Voorbeeld", "U1")


@pytest.mark.parametrize(
    ("status", "body", "error", "code"),
    [
        (409, {"code": "org_already_linked"}, LinkConflict, "org_already_linked"),
        (409, {"detail": {"code": "token_used"}}, LinkConflict, "token_used"),
        (403, {"code": "not_admin"}, LinkForbidden, "not_admin"),
        (
            403,
            {"detail": {"error_code": "feature_not_unlocked", "feature": "slack"}},
            LinkForbidden,
            "feature_not_unlocked",
        ),
    ],
)
async def test_link_oauth_error_code(
    status: int, body: object, error: type[LinkConflict | LinkForbidden], code: str
) -> None:
    _, client = _client(status, body)
    with pytest.raises(error) as exc:
        await client.link_oauth(TOKEN, "T1", "Voorbeeld", "U1")
    assert exc.value.code == code


async def test_unlink() -> None:
    seen, client = _client(204)
    await client.unlink("T1")
    assert (seen[0].method, seen[0].url.path) == ("DELETE", "/internal/slack/link/T1")


async def test_unlink_failure_is_loud() -> None:
    _, client = _client(503, {"detail": "down"})
    with pytest.raises(PortalUnavailable):
        await client.unlink("T1")
