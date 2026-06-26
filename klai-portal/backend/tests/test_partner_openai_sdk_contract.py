"""Contract tests: drive the REAL OpenAI SDK against our /partner/v1 payloads.

Unit tests that assert our own dict keys cannot prove SDK compatibility — the
OpenAI Python SDK parses leniently, so a missing required field surfaces as a
silent ``None`` (broken ``usage``/``tools``) rather than a loud error, and a
streaming event without a ``type`` field is dropped entirely. These tests feed
the bytes our handlers actually emit through ``openai`` so green == an OpenAI
SDK client gets correct, typed objects.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

openai = pytest.importorskip("openai")
import httpx  # noqa: E402
from openai import OpenAI  # noqa: E402

from app.api.partner_dependencies import PartnerAuthContext  # noqa: E402


@pytest.fixture(autouse=True)
def _bypass_usage_limits(monkeypatch):
    """Rate/spend limiting is covered elsewhere; let it pass so we reach upstream."""
    import app.api.partner as partner

    monkeypatch.setattr(partner, "get_redis_pool", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(partner, "check_rate_limit", AsyncMock(return_value=(True, 0)))
    monkeypatch.setattr(partner, "check_weighted_rate_limit", AsyncMock(return_value=(True, 0)))


def _auth() -> PartnerAuthContext:
    return PartnerAuthContext(
        key_id="key-1",
        org_id=1,
        zitadel_org_id="org-1",
        permissions={"general_chat": True},
        kb_access={},
        rate_limit_rpm=60,
    )


def _request(body: dict) -> MagicMock:
    payload = json.dumps(body).encode()

    async def stream():
        yield payload

    request = MagicMock()
    request.headers = {"content-length": str(len(payload))}
    request.stream = stream
    return request


def _sdk_client(handler) -> OpenAI:
    return OpenAI(
        api_key="pk_live_test",
        base_url="https://api.getklai.com/partner/v1",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


@pytest.mark.asyncio
async def test_models_list_and_retrieve_are_sdk_parseable():
    """client.models.list()/retrieve() should work with canonical ids and aliases."""
    import app.api.partner as partner

    models_payload = await partner.openai_compatible_models(auth=_auth())
    alias_payload = await partner.openai_compatible_model(model="gpt-4o-mini", auth=_auth())

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/partner/v1/models/gpt-4o-mini":
            return httpx.Response(200, json=alias_payload)
        if request.url.path == "/partner/v1/models":
            return httpx.Response(200, json=models_payload)
        return httpx.Response(404, json={"error": {"message": "not found"}})

    client = _sdk_client(handler)

    listed = client.models.list()
    assert [model.id for model in listed.data] == ["klai-fast", "klai-large", "klai-primary"]

    alias = client.models.retrieve("gpt-4o-mini")
    assert alias.id == "gpt-4o-mini"
    assert alias.object == "model"
    assert alias.owned_by == "klai-alias"


@pytest.mark.asyncio
async def test_responses_non_streaming_is_sdk_parseable(monkeypatch):
    """client.responses.create() must yield a fully-typed Response (usage + tools)."""
    import app.api.partner as partner

    chat_result = {
        "id": "chatcmpl-1",
        "model": "klai-fast",
        "choices": [{"message": {"content": "hello"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    monkeypatch.setattr(partner, "openai_chat_completion_non_streaming", AsyncMock(return_value=chat_result))

    emitted = await partner.openai_compatible_responses(
        http_request=_request({"model": "klai-fast", "input": "hi", "stream": False}),
        auth=_auth(),
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=emitted)

    client = _sdk_client(handler)
    resp = client.responses.create(model="klai-fast", input="hi")

    assert resp.output_text == "hello"
    # usage must be the Responses shape (input_tokens/output_tokens), not chat's
    # prompt_tokens/completion_tokens which the SDK drops to None.
    assert resp.usage is not None
    assert resp.usage.input_tokens == 10
    assert resp.usage.output_tokens == 5
    assert resp.usage.total_tokens == 15
    # Required Response fields must round-trip, not parse to None.
    assert resp.tools == []
    assert resp.tool_choice == "auto"
    assert resp.parallel_tool_calls is True


@pytest.mark.asyncio
async def test_responses_non_streaming_function_call_is_sdk_parseable(monkeypatch):
    """A tool-call response must expose a typed function_call output item."""
    import app.api.partner as partner

    chat_result = {
        "id": "chatcmpl-2",
        "model": "klai-primary",
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_123",
                            "type": "function",
                            "function": {"name": "lookup", "arguments": '{"id":1}'},
                        }
                    ],
                }
            }
        ],
        "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
    }
    monkeypatch.setattr(partner, "openai_chat_completion_non_streaming", AsyncMock(return_value=chat_result))

    emitted = await partner.openai_compatible_responses(
        http_request=_request(
            {
                "model": "klai-primary",
                "input": "Find customer 1",
                "tools": [{"type": "function", "name": "lookup", "parameters": {"type": "object"}}],
            }
        ),
        auth=_auth(),
    )

    client = _sdk_client(lambda _r: httpx.Response(200, json=emitted))
    resp = client.responses.create(model="klai-primary", input="x")

    fn_calls = [item for item in resp.output if item.type == "function_call"]
    assert len(fn_calls) == 1
    assert fn_calls[0].name == "lookup"
    assert fn_calls[0].arguments == '{"id":1}'
    assert fn_calls[0].call_id == "call_123"


@pytest.mark.asyncio
async def test_responses_streaming_is_sdk_parseable(monkeypatch):
    """client.responses.stream() must accumulate text and reach a final response."""
    from starlette.responses import StreamingResponse

    import app.api.partner as partner

    async def chat_events():
        yield b'data: {"choices":[{"delta":{"content":"hel"}}]}\n\n'
        yield b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
        yield b"data: [DONE]\n\n"

    monkeypatch.setattr(
        partner,
        "openai_chat_completion_streaming",
        AsyncMock(return_value=StreamingResponse(chat_events(), media_type="text/event-stream")),
    )

    streaming = await partner.openai_compatible_responses(
        http_request=_request({"model": "klai-fast", "input": "hi", "stream": True}),
        auth=_auth(),
    )
    assert isinstance(streaming, StreamingResponse)
    chunks: list[bytes] = []
    async for chunk in streaming.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else str(chunk).encode())
    sse_body = b"".join(chunks)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse_body)

    client = _sdk_client(handler)

    # Low-level: every streamed event must carry a real discriminated type.
    event_types = []
    delta_text = []
    for event in client.responses.create(model="klai-fast", input="hi", stream=True):
        event_types.append(event.type)
        if event.type == "response.output_text.delta":
            delta_text.append(event.delta)
    assert None not in event_types
    assert "".join(delta_text) == "hello"

    # High-level helper must not raise and must reconstruct the text.
    with client.responses.stream(model="klai-fast", input="hi") as stream:
        final = stream.get_final_response()
    assert final.output_text == "hello"
