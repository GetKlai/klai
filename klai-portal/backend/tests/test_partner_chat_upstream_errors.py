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
from helpers import held_reply_body, streamed_reply_frames

from app.services.chat_profile import ChatProfile


def _settings() -> MagicMock:
    s = MagicMock()
    s.litellm_base_url = "http://litellm:4000"
    s.litellm_master_key = "sk-test"
    s.chat_writer_max_tokens = 2000
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
    await held_reply_body(
        messages=[{"role": "user", "content": "hi"}],
        model="klai-primary",
        temperature=0.7,
        system_prompt="system",
        settings=_settings(),
        org_id=1,
        profile=ChatProfile(surface="internal", kb_mode="open"),
    )


@pytest.mark.asyncio
async def test_non_streaming_connect_error_returns_502():
    fake_client = _patched_client(post_side_effect=httpx.ConnectError("all attempts failed"))
    with patch("app.services.widget_write.httpx.AsyncClient", fake_client):
        with pytest.raises(HTTPException) as exc:
            await _call()
    assert exc.value.status_code == 502
    assert exc.value.detail["error"]["type"] == "upstream_error"


@pytest.mark.asyncio
async def test_non_streaming_timeout_returns_502():
    fake_client = _patched_client(post_side_effect=httpx.ReadTimeout("slow"))
    with patch("app.services.widget_write.httpx.AsyncClient", fake_client):
        with pytest.raises(HTTPException) as exc:
            await _call()
    assert exc.value.status_code == 502


@pytest.mark.asyncio
async def test_non_streaming_read_error_returns_502():
    fake_client = _patched_client(post_side_effect=httpx.ReadError("connection dropped while reading"))
    with patch("app.services.widget_write.httpx.AsyncClient", fake_client):
        with pytest.raises(HTTPException) as exc:
            await _call()
    assert exc.value.status_code == 502
    assert exc.value.detail["error"]["type"] == "upstream_error"


@pytest.mark.asyncio
async def test_non_streaming_upstream_status_error_returns_502():
    request = httpx.Request("POST", "http://litellm:4000/v1/chat/completions")
    response = httpx.Response(503, request=request)
    resp = MagicMock()
    resp.raise_for_status = MagicMock(side_effect=httpx.HTTPStatusError("503", request=request, response=response))
    fake_client = _patched_client(response=resp)
    with patch("app.services.widget_write.httpx.AsyncClient", fake_client):
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


def _stream_client_read_error():
    class _Stream:
        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            yield 'data: {"choices":[{"delta":{"content":"partial"}}]}'
            raise httpx.ReadError("connection dropped while reading")

        async def __aenter__(self):
            return self

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
        "app.services.widget_write.httpx.AsyncClient",
        _stream_client_raising(httpx.ConnectError("all attempts failed")),
    ):
        chunks = await streamed_reply_frames(
            profile=ChatProfile(surface="internal", kb_mode="open"),
            messages=[{"role": "user", "content": "hi"}],
            model="klai-primary",
            temperature=0.7,
            system_prompt="system",
            settings=_settings(),
            org_id=1,
        )
    body = b"".join(chunks).decode()
    assert '"error"' in body
    assert "upstream_error" in body
    assert "data: [DONE]" in body


@pytest.mark.asyncio
async def test_streaming_markers_upstream_read_error_emits_error_frame():
    with patch("app.services.widget_write.httpx.AsyncClient", _stream_client_read_error()):
        chunks = await streamed_reply_frames(
            profile=ChatProfile(surface="internal", kb_mode="open"),
            messages=[{"role": "user", "content": "hi"}],
            model="klai-primary",
            temperature=0.7,
            system_prompt="system",
            settings=_settings(),
            org_id=1,
        )
    body = b"".join(chunks).decode()
    assert "upstream_error" in body
    assert "data: [DONE]" in body


@pytest.mark.asyncio
async def test_streaming_markers_upstream_status_error_emits_error_frame():
    request = httpx.Request("POST", "http://litellm:4000/v1/chat/completions")
    response = httpx.Response(503, request=request)
    err = httpx.HTTPStatusError("503", request=request, response=response)
    with patch("app.services.widget_write.httpx.AsyncClient", _stream_client_raising(err)):
        chunks = await streamed_reply_frames(
            profile=ChatProfile(surface="internal", kb_mode="open"),
            messages=[{"role": "user", "content": "hi"}],
            model="klai-primary",
            temperature=0.7,
            system_prompt="system",
            settings=_settings(),
            org_id=1,
        )
    body = b"".join(chunks).decode()
    assert "upstream_error" in body
    assert "data: [DONE]" in body


def _answer(text: str = "Dat regel je onder Instellingen.") -> MagicMock:
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(
        return_value={"id": "c1", "choices": [{"message": {"content": text}, "finish_reason": "stop"}]}
    )
    return response


def _failing(status: int, text: str = "") -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://litellm.example.com/v1/chat/completions")
    return httpx.HTTPStatusError("failed", request=request, response=httpx.Response(status, request=request, text=text))


# What the gateway answers when an alias has spent its daily budget.
BUDGET_SPENT = (429, "No deployments available - crossed budget")


async def _write_on_medium(side_effect: list, signals: dict) -> tuple[dict, MagicMock]:
    client = MagicMock()
    client.post = AsyncMock(side_effect=side_effect)

    @asynccontextmanager
    async def fake_client(*_args, **_kwargs):
        yield client

    with patch("app.services.widget_write.httpx.AsyncClient", fake_client):
        body = await held_reply_body(
            messages=[{"role": "user", "content": "hi"}],
            model="klai-medium",
            fallback_model="klai-primary",
            temperature=0.7,
            system_prompt="system",
            settings=_settings(),
            org_id=1,
            answer_signals=signals,
            profile=ChatProfile(surface="internal", kb_mode="open"),
        )
    return body, client


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [BUDGET_SPENT, (402, ""), (500, ""), (503, "")], ids=["budget", "402", "500", "503"]
)
async def test_a_medium_alias_that_is_out_of_budget_is_retried_once_on_the_requested_alias_and_the_record_says_so(
    failure,
):
    signals: dict = {}

    body, client = await _write_on_medium([_failing(*failure), _answer()], signals)

    assert [call.kwargs["json"]["model"] for call in client.post.call_args_list] == ["klai-medium", "klai-primary"]
    assert body["choices"][0]["message"]["content"]
    assert (signals["model"], signals["writer"]["model"], signals["writer"]["fallback"]) == (
        "klai-primary",
        "klai-primary",
        True,
    )


@pytest.mark.asyncio
async def test_a_reply_written_by_the_medium_alias_is_recorded_as_such():
    signals: dict = {}

    await _write_on_medium([_answer()], signals)

    assert (signals["model"], signals["writer"]["fallback"]) == ("klai-medium", False)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [(400, ""), (429, "Rate limit exceeded")], ids=["bad_request", "rate_limit"])
async def test_a_failure_the_requested_alias_would_repeat_is_not_retried(failure):
    with pytest.raises(HTTPException) as exc:
        await _write_on_medium([_failing(*failure), _answer()], {})

    assert exc.value.status_code == 502
