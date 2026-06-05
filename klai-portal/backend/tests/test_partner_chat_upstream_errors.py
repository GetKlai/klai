"""Partner chat surfaces upstream failures cleanly.

When LiteLLM is unreachable (e.g. mid-restart during a deploy) or returns an
error status:
- non-streaming maps httpx exceptions to a clean 502 (was a bare 500);
- marker-mode streaming (which buffers before emitting) yields an OpenAI-style
  SSE error frame + [DONE] instead of a broken/truncated stream.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from app.services.partner_chat import chat_completion_non_streaming, chat_completion_streaming


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


def _stream_client_raising(exc: Exception):
    class _Stream:
        async def __aenter__(self):
            raise exc

        async def __aexit__(self, *_):
            return False

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        def stream(self, *_, **__):
            return _Stream()

    return lambda timeout: _Client()


@pytest.mark.asyncio
async def test_streaming_markers_upstream_connect_error_emits_error_frame():
    with patch(
        "app.services.partner_chat.httpx.AsyncClient",
        _stream_client_raising(httpx.ConnectError("all attempts failed")),
    ):
        chunks = [
            chunk
            async for chunk in chat_completion_streaming(
                messages=[{"role": "user", "content": "hi"}],
                model="klai-primary",
                temperature=0.7,
                system_prompt="system",
                settings=_settings(),
                org_id=1,
                citation_output="markers",
            )
        ]
    body = b"".join(chunks).decode()
    assert '"error"' in body
    assert "upstream_error" in body
    assert "data: [DONE]" in body


@pytest.mark.asyncio
async def test_streaming_markers_upstream_status_error_emits_error_frame():
    request = httpx.Request("POST", "http://litellm:4000/v1/chat/completions")
    response = httpx.Response(503, request=request)
    err = httpx.HTTPStatusError("503", request=request, response=response)
    with patch("app.services.partner_chat.httpx.AsyncClient", _stream_client_raising(err)):
        chunks = [
            chunk
            async for chunk in chat_completion_streaming(
                messages=[{"role": "user", "content": "hi"}],
                model="klai-primary",
                temperature=0.7,
                system_prompt="system",
                settings=_settings(),
                org_id=1,
                citation_output="markers",
            )
        ]
    body = b"".join(chunks).decode()
    assert "upstream_error" in body
    assert "data: [DONE]" in body
