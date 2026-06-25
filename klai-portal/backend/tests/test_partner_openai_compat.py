"""OpenAI-compatible Partner API passthrough tests."""

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException
from redis.exceptions import RedisError
from starlette.responses import StreamingResponse

from app.api.partner_dependencies import PartnerAuthContext


@pytest.fixture(autouse=True)
def allow_openai_usage_limits(monkeypatch):
    import app.api.partner as partner

    monkeypatch.setattr(partner, "get_redis_pool", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(partner, "check_rate_limit", AsyncMock(return_value=(True, 0)))
    monkeypatch.setattr(partner, "check_weighted_rate_limit", AsyncMock(return_value=(True, 0)))
    monkeypatch.setattr(partner, "openai_chat_completion_non_streaming", AsyncMock(return_value={"choices": []}))
    monkeypatch.setattr(partner, "openai_chat_completion_streaming", AsyncMock(return_value=MagicMock()))


def _auth(permissions: dict | None = None) -> PartnerAuthContext:
    return PartnerAuthContext(
        key_id="key-1",
        org_id=1,
        zitadel_org_id="org-1",
        permissions=permissions or {"chat": True, "general_chat": True},
        kb_access={1: "read"},
        rate_limit_rpm=60,
    )


def _request(body: object, *, headers: dict[str, str] | None = None, raw: bytes | None = None) -> MagicMock:
    payload = raw if raw is not None else json.dumps(body).encode()

    async def stream():
        yield payload

    request = MagicMock()
    request.headers = {"content-length": str(len(payload)), **(headers or {})}
    request.stream = stream
    return request


def test_openai_passthrough_metadata_strips_user_metadata():
    from app.services.partner_chat import _with_openai_passthrough_metadata

    body = {"metadata": {"customer": "acme"}, "messages": [{"role": "user", "content": "hi"}]}

    forwarded = _with_openai_passthrough_metadata(body)

    assert forwarded["metadata"] == {"_klai_openai_passthrough": True}
    assert body["metadata"] == {"customer": "acme"}


@pytest.mark.asyncio
async def test_openai_compatible_chat_requires_general_chat_permission():
    from app.api.partner import openai_compatible_chat_completions

    with pytest.raises(HTTPException) as exc:
        await openai_compatible_chat_completions(
            http_request=_request({"messages": [{"role": "user", "content": "hi"}]}),
            auth=_auth({"chat": True}),
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_openai_compatible_chat_forwards_raw_openai_fields(monkeypatch):
    import app.api.partner as partner

    forwarded = AsyncMock(return_value={"choices": [{"message": {"content": "ok"}}]})
    monkeypatch.setattr(partner, "openai_chat_completion_non_streaming", forwarded)

    body = {
        "model": "klai-large",
        "messages": [{"role": "user", "content": "Call the tool"}],
        "tools": [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}],
        "tool_choice": "auto",
        "response_format": {"type": "json_object"},
        "user": "external-user-1",
        "max_tokens": 512,
    }

    result = await partner.openai_compatible_chat_completions(http_request=_request(body), auth=_auth())

    assert result == {"choices": [{"message": {"content": "ok"}}]}
    sent = forwarded.await_args.args[0]
    assert sent["model"] == "klai-large"
    assert sent["tools"] == body["tools"]
    assert sent["tool_choice"] == "auto"
    assert sent["response_format"] == {"type": "json_object"}
    assert sent["user"] == "external-user-1"


@pytest.mark.asyncio
async def test_openai_compatible_chat_strips_litellm_control_fields(monkeypatch):
    import app.api.partner as partner

    forwarded = AsyncMock(return_value={"choices": [{"message": {"content": "ok"}}]})
    monkeypatch.setattr(partner, "openai_chat_completion_non_streaming", forwarded)

    await partner.openai_compatible_chat_completions(
        http_request=_request(
            {
                "messages": [{"role": "user", "content": "hi"}],
                "mock_response": "free tokens",
                "guardrails": [],
                "api_base": "https://attacker.test",
                "api_key": "sk-attacker",
                "metadata": {"customer": "acme"},
            }
        ),
        auth=_auth(),
    )

    sent = forwarded.await_args.args[0]
    assert "mock_response" not in sent
    assert "guardrails" not in sent
    assert "api_base" not in sent
    assert "api_key" not in sent
    assert "metadata" not in sent


@pytest.mark.asyncio
async def test_openai_compatible_chat_defaults_output_cap(monkeypatch):
    import app.api.partner as partner

    forwarded = AsyncMock(return_value={"choices": [{"message": {"content": "ok"}}]})
    monkeypatch.setattr(partner, "openai_chat_completion_non_streaming", forwarded)

    await partner.openai_compatible_chat_completions(
        http_request=_request({"messages": [{"role": "user", "content": "hi"}]}),
        auth=_auth(),
    )

    assert forwarded.await_args.args[0]["max_tokens"] == 2048


@pytest.mark.asyncio
async def test_openai_compatible_chat_rejects_invalid_json():
    from app.api.partner import openai_compatible_chat_completions

    with pytest.raises(HTTPException) as exc:
        await openai_compatible_chat_completions(http_request=_request({}, raw=b"{not-json"), auth=_auth())

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_openai_compatible_chat_rejects_non_object_json():
    from app.api.partner import openai_compatible_chat_completions

    with pytest.raises(HTTPException) as exc:
        await openai_compatible_chat_completions(http_request=_request(["not", "object"]), auth=_auth())

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_openai_compatible_chat_rejects_large_content_length():
    from app.api.partner import openai_compatible_chat_completions

    with pytest.raises(HTTPException) as exc:
        await openai_compatible_chat_completions(
            http_request=_request(
                {"messages": [{"role": "user", "content": "hi"}]},
                headers={"content-length": "131073"},
            ),
            auth=_auth(),
        )

    assert exc.value.status_code == 413


@pytest.mark.asyncio
async def test_openai_compatible_chat_rejects_large_output_cap():
    from app.api.partner import openai_compatible_chat_completions

    with pytest.raises(HTTPException) as exc:
        await openai_compatible_chat_completions(
            http_request=_request(
                {
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 4097,
                }
            ),
            auth=_auth(),
        )

    assert exc.value.status_code == 400
    assert "may not exceed" in exc.value.detail["error"]["message"]


@pytest.mark.asyncio
async def test_openai_compatible_chat_rejects_large_completion_output_cap():
    from app.api.partner import openai_compatible_chat_completions

    with pytest.raises(HTTPException) as exc:
        await openai_compatible_chat_completions(
            http_request=_request(
                {
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_completion_tokens": 4097,
                }
            ),
            auth=_auth(),
        )

    assert exc.value.status_code == 400
    assert "may not exceed" in exc.value.detail["error"]["message"]


@pytest.mark.asyncio
async def test_openai_compatible_chat_rejects_multiple_choices():
    from app.api.partner import openai_compatible_chat_completions

    with pytest.raises(HTTPException) as exc:
        await openai_compatible_chat_completions(
            http_request=_request({"messages": [{"role": "user", "content": "hi"}], "n": 2}),
            auth=_auth(),
        )

    assert exc.value.status_code == 400
    assert exc.value.detail["error"]["message"] == "n must be 1"


@pytest.mark.asyncio
async def test_openai_compatible_chat_rejects_large_estimated_input(monkeypatch):
    import app.api.partner as partner

    monkeypatch.setattr(partner.settings, "partner_openai_max_input_tokens", 10)

    with pytest.raises(HTTPException) as exc:
        await partner.openai_compatible_chat_completions(
            http_request=_request({"messages": [{"role": "user", "content": "x" * 200}]}),
            auth=_auth(),
        )

    assert exc.value.status_code == 400
    assert "estimated input tokens" in exc.value.detail["error"]["message"]


@pytest.mark.asyncio
async def test_openai_compatible_chat_enforces_route_rpm(monkeypatch):
    import app.api.partner as partner

    monkeypatch.setattr(partner, "check_rate_limit", AsyncMock(return_value=(False, 17)))

    with pytest.raises(HTTPException) as exc:
        await partner.openai_compatible_chat_completions(
            http_request=_request({"messages": [{"role": "user", "content": "hi"}]}),
            auth=_auth(),
        )

    assert exc.value.status_code == 429
    assert exc.value.headers["Retry-After"] == "17"
    partner.check_rate_limit.assert_awaited_once()
    assert partner.check_rate_limit.await_args.args[1] == "openai:key-1"


@pytest.mark.asyncio
async def test_openai_compatible_chat_fails_closed_when_redis_pool_missing(monkeypatch):
    import app.api.partner as partner

    monkeypatch.setattr(partner, "get_redis_pool", AsyncMock(return_value=None))

    with pytest.raises(HTTPException) as exc:
        await partner.openai_compatible_chat_completions(
            http_request=_request({"messages": [{"role": "user", "content": "hi"}]}),
            auth=_auth(),
        )

    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_openai_compatible_chat_fails_closed_when_redis_errors(monkeypatch):
    import app.api.partner as partner

    monkeypatch.setattr(partner, "check_rate_limit", AsyncMock(side_effect=RedisError("connection refused")))

    with pytest.raises(HTTPException) as exc:
        await partner.openai_compatible_chat_completions(
            http_request=_request({"messages": [{"role": "user", "content": "hi"}]}),
            auth=_auth(),
        )

    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_openai_compatible_chat_enforces_route_tpm(monkeypatch):
    import app.api.partner as partner

    monkeypatch.setattr(partner, "check_weighted_rate_limit", AsyncMock(return_value=(False, 23)))

    with pytest.raises(HTTPException) as exc:
        await partner.openai_compatible_chat_completions(
            http_request=_request({"messages": [{"role": "user", "content": "hi"}]}),
            auth=_auth(),
        )

    assert exc.value.status_code == 429
    assert exc.value.headers["Retry-After"] == "23"
    partner.check_weighted_rate_limit.assert_awaited_once()
    assert partner.check_weighted_rate_limit.await_args.args[1] == "openai_tpm:key-1"


@pytest.mark.asyncio
async def test_openai_compatible_chat_tpm_cost_includes_default_output(monkeypatch):
    import app.api.partner as partner

    await partner.openai_compatible_chat_completions(
        http_request=_request({"messages": [{"role": "user", "content": "hi"}]}),
        auth=_auth(),
    )

    sent_body = partner.openai_chat_completion_non_streaming.await_args.args[0]
    expected_cost = partner._estimate_openai_input_tokens(sent_body) + 2048
    assert partner.check_weighted_rate_limit.await_args.args[2] == expected_cost


@pytest.mark.asyncio
async def test_openai_compatible_chat_tpm_cost_uses_largest_output_field(monkeypatch):
    import app.api.partner as partner

    await partner.openai_compatible_chat_completions(
        http_request=_request(
            {
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 4096,
                "max_completion_tokens": 1,
            }
        ),
        auth=_auth(),
    )

    sent_body = partner.openai_chat_completion_non_streaming.await_args.args[0]
    expected_cost = partner._estimate_openai_input_tokens(sent_body) + 4096
    assert partner.check_weighted_rate_limit.await_args.args[2] == expected_cost


@pytest.mark.asyncio
async def test_openai_non_streaming_preserves_upstream_4xx(monkeypatch):
    from app.core.config import Settings
    from app.services.partner_chat import openai_chat_completion_non_streaming

    upstream_body = {"error": {"type": "invalid_request_error", "message": "context length exceeded"}}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, *args, **kwargs):
            request = httpx.Request("POST", "http://litellm.test/v1/chat/completions")
            return httpx.Response(400, json=upstream_body, request=request)

    monkeypatch.setattr("app.services.partner_chat.httpx.AsyncClient", FakeClient)

    result = await openai_chat_completion_non_streaming(
        {"messages": [{"role": "user", "content": "hi"}]},
        Settings(litellm_base_url="http://litellm.test", litellm_general_chat_key="sk-general"),
    )

    assert result.status_code == 400
    assert json.loads(result.body) == upstream_body


@pytest.mark.asyncio
async def test_openai_non_streaming_uses_general_chat_key(monkeypatch):
    from app.core.config import Settings
    from app.services.partner_chat import openai_chat_completion_non_streaming

    seen_headers: dict[str, str] = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, *args, **kwargs):
            seen_headers.update(kwargs["headers"])
            request = httpx.Request("POST", "http://litellm.test/v1/chat/completions")
            return httpx.Response(200, json={"choices": []}, request=request)

    monkeypatch.setattr("app.services.partner_chat.httpx.AsyncClient", FakeClient)

    await openai_chat_completion_non_streaming(
        {"messages": [{"role": "user", "content": "hi"}]},
        Settings(
            litellm_base_url="http://litellm.test",
            litellm_master_key="sk-master",
            litellm_general_chat_key="sk-general",
        ),
    )

    assert seen_headers["Authorization"] == "Bearer sk-general"


@pytest.mark.asyncio
async def test_openai_non_streaming_fails_closed_without_general_chat_key():
    from app.core.config import Settings
    from app.services.partner_chat import openai_chat_completion_non_streaming

    with pytest.raises(HTTPException) as exc:
        await openai_chat_completion_non_streaming(
            {"messages": [{"role": "user", "content": "hi"}]},
            Settings(litellm_base_url="http://litellm.test", litellm_master_key="sk-master"),
        )

    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_openai_non_streaming_fails_closed_when_general_chat_key_matches_master():
    from app.core.config import Settings
    from app.services.partner_chat import openai_chat_completion_non_streaming

    with pytest.raises(HTTPException) as exc:
        await openai_chat_completion_non_streaming(
            {"messages": [{"role": "user", "content": "hi"}]},
            Settings(
                litellm_base_url="http://litellm.test",
                litellm_master_key="sk-same",
                litellm_general_chat_key="sk-same",
            ),
        )

    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_openai_streaming_preserves_upstream_4xx_before_stream_start(monkeypatch):
    from app.core.config import Settings
    from app.services.partner_chat import openai_chat_completion_streaming

    upstream_body = {"error": {"type": "invalid_request_error", "message": "context length exceeded"}}

    class FakeStream:
        async def __aenter__(self):
            request = httpx.Request("POST", "http://litellm.test/v1/chat/completions")
            return httpx.Response(400, json=upstream_body, request=request)

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.closed = False

        def stream(self, *args, **kwargs):
            return FakeStream()

        async def aclose(self):
            self.closed = True

    monkeypatch.setattr("app.services.partner_chat.httpx.AsyncClient", FakeClient)

    result = await openai_chat_completion_streaming(
        {"messages": [{"role": "user", "content": "hi"}]},
        Settings(litellm_base_url="http://litellm.test", litellm_general_chat_key="sk-general"),
    )

    assert result.status_code == 400
    assert json.loads(result.body) == upstream_body


@pytest.mark.asyncio
async def test_openai_compatible_models_requires_general_chat_permission():
    from app.api.partner import openai_compatible_models

    with pytest.raises(HTTPException) as exc:
        await openai_compatible_models(auth=_auth({"chat": True}))

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_openai_compatible_models_lists_general_models():
    from app.api.partner import openai_compatible_models

    result = await openai_compatible_models(auth=_auth())

    model_ids = [entry["id"] for entry in result["data"]]
    assert model_ids == [
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4o",
        "gpt-4o-mini",
        "klai-fast",
        "klai-large",
        "klai-primary",
    ]


@pytest.mark.asyncio
async def test_openai_compatible_responses_requires_general_chat_permission():
    from app.api.partner import openai_compatible_responses

    with pytest.raises(HTTPException) as exc:
        await openai_compatible_responses(
            http_request=_request({"model": "gpt-4o-mini", "input": "hi"}),
            auth=_auth({"chat": True}),
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_openai_compatible_responses_maps_text_request_to_chat(monkeypatch):
    import app.api.partner as partner

    forwarded = AsyncMock(
        return_value={
            "id": "chatcmpl-1",
            "model": "klai-fast",
            "choices": [{"message": {"content": '{"ok":true}'}}],
            "usage": {"total_tokens": 7},
        }
    )
    monkeypatch.setattr(partner, "openai_chat_completion_non_streaming", forwarded)

    result = await partner.openai_compatible_responses(
        http_request=_request(
            {
                "model": "gpt-4o-mini",
                "instructions": "Answer as JSON.",
                "input": [{"role": "user", "content": [{"type": "input_text", "text": "Ping"}]}],
                "max_output_tokens": 123,
                "text": {"format": {"type": "json_object"}},
                "stream": False,
            }
        ),
        auth=_auth(),
    )

    assert result["object"] == "response"
    assert result["id"] == "chatcmpl-1"
    assert result["model"] == "klai-fast"
    assert result["output_text"] == '{"ok":true}'
    assert result["output"][0]["content"][0]["type"] == "output_text"
    assert result["usage"] == {"total_tokens": 7}

    sent = forwarded.await_args.args[0]
    assert sent["model"] == "klai-fast"
    assert sent["messages"] == [
        {"role": "system", "content": "Answer as JSON."},
        {"role": "user", "content": "Ping"},
    ]
    assert sent["max_tokens"] == 123
    assert sent["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_openai_compatible_responses_maps_function_tools(monkeypatch):
    import app.api.partner as partner

    forwarded = AsyncMock(
        return_value={
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
            ]
        }
    )
    monkeypatch.setattr(partner, "openai_chat_completion_non_streaming", forwarded)

    result = await partner.openai_compatible_responses(
        http_request=_request(
            {
                "model": "klai-primary",
                "input": "Find this customer",
                "tools": [
                    {
                        "type": "function",
                        "name": "lookup",
                        "description": "Look up a customer",
                        "parameters": {"type": "object", "properties": {"id": {"type": "integer"}}},
                    }
                ],
                "tool_choice": {"type": "function", "name": "lookup"},
            }
        ),
        auth=_auth(),
    )

    sent = forwarded.await_args.args[0]
    assert sent["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "lookup",
                "description": "Look up a customer",
                "parameters": {"type": "object", "properties": {"id": {"type": "integer"}}},
            },
        }
    ]
    assert sent["tool_choice"] == {"type": "function", "function": {"name": "lookup"}}
    assert result["output"][1] == {
        "type": "function_call",
        "id": "call_123",
        "call_id": "call_123",
        "name": "lookup",
        "arguments": '{"id":1}',
        "status": "completed",
    }


@pytest.mark.asyncio
async def test_openai_compatible_responses_rejects_hosted_tools():
    from app.api.partner import openai_compatible_responses

    with pytest.raises(HTTPException) as exc:
        await openai_compatible_responses(
            http_request=_request(
                {
                    "model": "gpt-4o-mini",
                    "input": "Search the web",
                    "tools": [{"type": "web_search_preview"}],
                }
            ),
            auth=_auth(),
        )

    assert exc.value.status_code == 400
    assert "not supported" in exc.value.detail["error"]["message"]


@pytest.mark.asyncio
async def test_openai_compatible_responses_rejects_knowledge_extensions():
    from app.api.partner import openai_compatible_responses

    with pytest.raises(HTTPException) as exc:
        await openai_compatible_responses(
            http_request=_request(
                {
                    "model": "gpt-4o-mini",
                    "input": "Use KB",
                    "knowledge": {"enabled": True},
                }
            ),
            auth=_auth(),
        )

    assert exc.value.status_code == 400
    assert "/chat/completions" in exc.value.detail["error"]["message"]


@pytest.mark.asyncio
async def test_openai_compatible_responses_streams_text_events(monkeypatch):
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

    response = await partner.openai_compatible_responses(
        http_request=_request({"model": "gpt-4o-mini", "input": "hi", "stream": True}),
        auth=_auth(),
    )

    assert isinstance(response, StreamingResponse)
    chunks: list[bytes] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else str(chunk).encode())
    body = b"".join(chunks).decode()
    assert "event: response.output_text.delta" in body
    assert '"delta": "hel"' in body
    assert '"delta": "lo"' in body
    assert "event: response.completed" in body
    assert '"output_text": "hello"' in body
    assert "data: [DONE]" in body


def test_openai_compatible_routes_are_canonical_only():
    import app.api.partner as partner

    route_paths = {route.path for route in partner.router.routes}

    assert "/partner/v1/models" in route_paths
    assert "/partner/v1/chat/completions" in route_paths
    assert "/partner/v1/responses" in route_paths
    assert "/partner/v1/openai/models" not in route_paths
    assert "/partner/v1/openai/chat/completions" not in route_paths


@pytest.mark.asyncio
async def test_canonical_chat_without_knowledge_uses_general_passthrough(monkeypatch):
    import app.api.partner as partner

    forwarded = AsyncMock(return_value={"choices": [{"message": {"content": "ok"}}]})
    monkeypatch.setattr(partner, "openai_chat_completion_non_streaming", forwarded)

    result = await partner.canonical_chat_completions(
        http_request=_request(
            {
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "Return JSON"}],
                "response_format": {"type": "json_object"},
                "stream": False,
            }
        ),
        auth=_auth({"chat": True, "general_chat": True}),
        db=AsyncMock(),
    )

    assert result == {"choices": [{"message": {"content": "ok"}}]}
    sent = forwarded.await_args.args[0]
    assert sent["model"] == "klai-fast"
    assert sent["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_canonical_chat_with_knowledge_uses_rag_flow(monkeypatch):
    import app.api.partner as partner

    knowledge_flow = AsyncMock(return_value={"choices": [{"message": {"content": "rag"}}]})
    monkeypatch.setattr(partner, "chat_completions", knowledge_flow)

    result = await partner.canonical_chat_completions(
        http_request=_request(
            {
                "model": "klai-primary",
                "messages": [{"role": "user", "content": "Answer from KB"}],
                "stream": False,
                "knowledge": {
                    "enabled": True,
                    "knowledge_base_ids": [1],
                    "include_sources": True,
                },
            }
        ),
        auth=_auth({"chat": True, "general_chat": True}),
        db=AsyncMock(),
    )

    assert result == {"choices": [{"message": {"content": "rag"}}]}
    knowledge_flow.assert_awaited_once()
    sent_request = knowledge_flow.await_args.kwargs["request"]
    assert sent_request.knowledge is not None
    assert sent_request.knowledge.enabled is True
    assert sent_request.knowledge.knowledge_base_ids == [1]


@pytest.mark.asyncio
async def test_canonical_chat_with_knowledge_disabled_uses_general_passthrough(monkeypatch):
    import app.api.partner as partner

    forwarded = AsyncMock(return_value={"choices": [{"message": {"content": "general"}}]})
    monkeypatch.setattr(partner, "openai_chat_completion_non_streaming", forwarded)

    result = await partner.canonical_chat_completions(
        http_request=_request(
            {
                "model": "klai-primary",
                "messages": [{"role": "user", "content": "general question"}],
                "stream": False,
                "knowledge": {"enabled": False},
            }
        ),
        auth=_auth({"chat": True, "general_chat": True}),
        db=AsyncMock(),
    )

    assert result == {"choices": [{"message": {"content": "general"}}]}
    sent = forwarded.await_args.args[0]
    assert sent["model"] == "klai-primary"
    assert "knowledge" not in sent


@pytest.mark.asyncio
async def test_knowledge_chat_still_rejects_klai_large():
    from app.api.partner import ChatCompletionsRequest, chat_completions

    req = ChatCompletionsRequest(
        messages=[{"role": "user", "content": "hi"}],
        model="klai-large",
        stream=False,
    )

    with pytest.raises(HTTPException) as exc:
        await chat_completions(
            request=req,
            http_request=MagicMock(headers={}, client=MagicMock(host="127.0.0.1")),
            auth=_auth({"chat": True, "general_chat": True}),
            db=AsyncMock(),
        )

    assert exc.value.status_code == 400
