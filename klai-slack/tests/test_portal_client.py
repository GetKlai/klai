from __future__ import annotations

import json

import httpx
import pytest

from app.clients.portal import (
    LinkConflict,
    LinkForbidden,
    LinkKeyInvalid,
    LinkResult,
    PortalClient,
    PortalUnavailable,
)

SECRET = "test-portal-internal-secret"
KEY = "pk_live_synthetic_key_0001"


def _client(status: int, body: object = None) -> tuple[list[httpx.Request], PortalClient]:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body)

    return seen, PortalClient(
        "http://portal-api.test:8010", SECRET, transport=httpx.MockTransport(handle)
    )


async def test_link_success() -> None:
    seen, client = _client(
        200,
        {
            "org_id": 7,
            "org_name": "Voorbeeld BV",
            "key_id": "4f2c9a1e-0000-4000-8000-000000000042",
            "kb_ids": [1, 2],
        },
    )
    result = await client.link(KEY, "T1", "Voorbeeld", "U1")
    assert result == LinkResult(
        org_id=7,
        org_name="Voorbeeld BV",
        key_id="4f2c9a1e-0000-4000-8000-000000000042",
        kb_ids=[1, 2],
    )
    assert seen[0].url.path == "/internal/slack/link"
    assert seen[0].headers["Authorization"] == f"Bearer {SECRET}"
    assert json.loads(seen[0].content) == {
        "api_key": KEY,
        "team_id": "T1",
        "team_name": "Voorbeeld",
        "slack_user_id": "U1",
    }


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (404, {"detail": "not found"}, LinkKeyInvalid),
        (403, {"detail": "locked"}, LinkForbidden),
        (500, {"detail": "boom"}, PortalUnavailable),
    ],
)
async def test_link_error_mapping(status: int, body: object, error: type[Exception]) -> None:
    _, client = _client(status, body)
    with pytest.raises(error):
        await client.link(KEY, "T1", "Voorbeeld", "U1")


@pytest.mark.parametrize(
    "body",
    [{"detail": {"code": "org_already_linked"}}, {"code": "org_already_linked"}],
)
async def test_link_conflict_code(body: object) -> None:
    _, client = _client(409, body)
    with pytest.raises(LinkConflict) as exc:
        await client.link(KEY, "T1", "Voorbeeld", "U1")
    assert exc.value.code == "org_already_linked"


async def test_unlink() -> None:
    seen, client = _client(204)
    await client.unlink("T1")
    assert (seen[0].method, seen[0].url.path) == ("DELETE", "/internal/slack/link/T1")


async def test_unlink_failure_is_loud() -> None:
    _, client = _client(503, {"detail": "down"})
    with pytest.raises(PortalUnavailable):
        await client.unlink("T1")
