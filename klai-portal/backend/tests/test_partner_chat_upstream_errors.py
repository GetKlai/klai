"""Non-streaming partner chat returns a clean 502 on upstream failure.

When LiteLLM is unreachable (e.g. mid-restart during a deploy) or returns an
error status, the non-streaming path used to let httpx exceptions surface as a
bare 500 Internal Server Error. These tests lock in the 502 mapping so callers
can tell an upstream blip apart from a request error.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from app.services.partner_chat import chat_completion_non_streaming


def _settings() -> MagicMock:
    s = MagicMock()
    s.litellm_base_url = "http://litellm:4000"
    s.litellm_master_key = "sk-test"
    return s


def _patched_client(*, post_side_effect=None, response=None):
    client = MagicMock()
    if post_side_effect is not None:
        client.post = AsyncMock(side_effect=post_side_effect)
    else:
        client.post = AsyncMock(return_value=response)

    @asynccontextmanager
    async def fake_client(*_args, **_kwargs):
        yield client

    return fake_client


async def _call() -> None:
    await chat_completion_non_streaming(
        messages=[{"role": "user", "content": "hi"}],
        model="klai-primary",
        temperature=0.7,
        system_prompt="system",
        settings=_settings(),
        org_id=1,
    )


@pytest.mark.asyncio
async def test_non_streaming_connect_error_returns_502():
    fake_client = _patched_client(post_side_effect=httpx.ConnectError("all attempts failed"))
    with patch("app.services.partner_chat.httpx.AsyncClient", fake_client):
        with pytest.raises(HTTPException) as exc:
            await _call()
    assert exc.value.status_code == 502
    assert exc.value.detail["error"]["type"] == "upstream_error"


@pytest.mark.asyncio
async def test_non_streaming_timeout_returns_502():
    fake_client = _patched_client(post_side_effect=httpx.ReadTimeout("slow"))
    with patch("app.services.partner_chat.httpx.AsyncClient", fake_client):
        with pytest.raises(HTTPException) as exc:
            await _call()
    assert exc.value.status_code == 502


@pytest.mark.asyncio
async def test_non_streaming_upstream_status_error_returns_502():
    request = httpx.Request("POST", "http://litellm:4000/v1/chat/completions")
    response = httpx.Response(503, request=request)
    resp = MagicMock()
    resp.raise_for_status = MagicMock(side_effect=httpx.HTTPStatusError("503", request=request, response=response))
    fake_client = _patched_client(response=resp)
    with patch("app.services.partner_chat.httpx.AsyncClient", fake_client):
        with pytest.raises(HTTPException) as exc:
            await _call()
    assert exc.value.status_code == 502
