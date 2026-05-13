"""Coverage for set_password_with_code's dual-path (invite + reset) behaviour.

Zitadel has two flows that both arrive on Klai's /password/set page:
  - Invite:  POST /v2/users/{id}/invite_code/verify  + POST /v2/users/{id}/password
  - Reset:   POST /v2/users/{id}/password (with verificationCode in the body)

set_password_with_code MUST try invite first, then fall back to reset on 4xx.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest


def _client_with_mocked_http():
    from app.services.zitadel import ZitadelClient

    client = ZitadelClient.__new__(ZitadelClient)
    client._http = MagicMock()
    return client


def _resp(status: int, json_body: dict | None = None) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.is_success = 200 <= status < 300
    r.json = MagicMock(return_value=json_body or {})

    def _raise() -> None:
        if status >= 400:
            req = httpx.Request("POST", f"https://zitadel.example/v2/x?status={status}")
            raw = httpx.Response(status, request=req)
            raise httpx.HTTPStatusError(f"HTTP {status}", request=req, response=raw)

    r.raise_for_status = MagicMock(side_effect=_raise)
    return r


@pytest.mark.asyncio
async def test_invite_flow_happy_path() -> None:
    """invite_code/verify 200 → password (no code) 200 → return."""
    client = _client_with_mocked_http()
    client._http.post = AsyncMock(
        side_effect=[
            _resp(200, {"details": {"sequence": "11"}}),  # invite_code/verify
            _resp(200, {"details": {"sequence": "12"}}),  # password (no code)
        ]
    )

    flow = await client.set_password_with_code("uid-1", "INVITE", "NewSecret123!")
    assert flow == "invite"

    assert client._http.post.await_count == 2
    # First call: verify
    first = client._http.post.await_args_list[0]
    assert first.args[0] == "/v2/users/uid-1/invite_code/verify"
    assert first.kwargs["json"] == {"verificationCode": "INVITE"}
    # Second call: password without verificationCode
    second = client._http.post.await_args_list[1]
    assert second.args[0] == "/v2/users/uid-1/password"
    assert "verificationCode" not in second.kwargs["json"]
    assert second.kwargs["json"]["newPassword"]["password"] == "NewSecret123!"


@pytest.mark.asyncio
async def test_invite_verify_4xx_falls_back_to_reset_flow() -> None:
    """invite_code/verify 400 → reset-flow attempt → success."""
    client = _client_with_mocked_http()
    client._http.post = AsyncMock(
        side_effect=[
            _resp(400, {"error": "code is not an invite"}),  # invite_code/verify
            _resp(200, {"details": {"sequence": "8"}}),  # reset-flow password (with code)
        ]
    )

    flow = await client.set_password_with_code("uid-1", "RESETCODE", "NewSecret123!")
    assert flow == "reset"

    assert client._http.post.await_count == 2
    second = client._http.post.await_args_list[1]
    assert second.args[0] == "/v2/users/uid-1/password"
    # Reset flow includes verificationCode
    assert second.kwargs["json"]["verificationCode"] == "RESETCODE"


@pytest.mark.asyncio
async def test_invite_verify_5xx_propagates() -> None:
    """invite_code/verify 502 → propagate HTTPStatusError, no fallback attempt."""
    client = _client_with_mocked_http()
    client._http.post = AsyncMock(side_effect=[_resp(502, {"error": "bad gw"})])

    with pytest.raises(httpx.HTTPStatusError) as exc:
        await client.set_password_with_code("uid-1", "X", "NewSecret123!")
    assert exc.value.response.status_code == 502
    # No fallback attempted on 5xx.
    assert client._http.post.await_count == 1


@pytest.mark.asyncio
async def test_reset_flow_4xx_invalid_code() -> None:
    """invite_code/verify 400 → reset-flow 400 → both 4xx, raise as reset 4xx."""
    client = _client_with_mocked_http()
    client._http.post = AsyncMock(
        side_effect=[
            _resp(400, {"error": "not an invite"}),  # invite_code/verify
            _resp(400, {"error": "bad code"}),  # reset password
        ]
    )

    with pytest.raises(httpx.HTTPStatusError) as exc:
        await client.set_password_with_code("uid-1", "BOGUS", "NewSecret123!")
    assert exc.value.response.status_code == 400
    assert client._http.post.await_count == 2


@pytest.mark.asyncio
async def test_invite_flow_password_step_5xx() -> None:
    """invite_code/verify 200 → password 502 → propagate 502."""
    client = _client_with_mocked_http()
    client._http.post = AsyncMock(
        side_effect=[
            _resp(200, {"details": {"sequence": "11"}}),
            _resp(502, {"error": "bad gw"}),
        ]
    )

    with pytest.raises(httpx.HTTPStatusError) as exc:
        await client.set_password_with_code("uid-1", "INVITE", "NewSecret123!")
    assert exc.value.response.status_code == 502
