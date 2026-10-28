from __future__ import annotations

import json

import httpx
import pytest

from app.clients.partner import (
    PartnerAuthError,
    PartnerClient,
    PartnerRateLimited,
    PartnerUnavailable,
    Source,
)

BASE = "http://portal-api.test:8010"
KEY = "pk_live_synthetic_key_0001"

COMPLETION = {
    "id": "chatcmpl-123",
    "object": "chat.completion",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "Het verlof is 25 dagen [1].",
                "sources": [
                    {
                        "label": "1",
                        "title": "Verlofregeling",
                        "url": "https://docs.example.com/verlof",
                        "origin": "kb",
                    },
                    {"label": "2", "title": None, "url": "https://docs.example.com/x"},
                ],
            },
            "finish_reason": "stop",
        }
    ],
}


def _client(handler: httpx.MockTransport) -> PartnerClient:
    return PartnerClient(BASE, KEY, transport=handler)


def _capture(status: int = 200, body: object = COMPLETION, headers: dict[str, str] | None = None):
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body, headers=headers)

    return seen, httpx.MockTransport(handle)


async def test_chat_omits_kb_ids_when_scope_is_all_allowed() -> None:
    seen, transport = _capture()
    await _client(transport).chat([{"role": "user", "content": "Hoeveel verlof?"}], None)
    sent = json.loads(seen[0].content)
    assert sent["knowledge"] == {"include_sources": True}
    assert sent["stream"] is False
    assert seen[0].url.path == "/partner/v1/chat/completions"
    assert seen[0].headers["Authorization"] == f"Bearer {KEY}"
    assert seen[0].headers["X-Request-ID"]


async def test_chat_sends_kb_ids_when_scoped() -> None:
    seen, transport = _capture()
    await _client(transport).chat([{"role": "user", "content": "q"}], [2, 5])
    assert json.loads(seen[0].content)["knowledge"]["knowledge_base_ids"] == [2, 5]


async def test_chat_parses_text_sources_and_completion_id() -> None:
    _, transport = _capture()
    result = await _client(transport).chat([{"role": "user", "content": "q"}], None)
    assert result.text == "Het verlof is 25 dagen [1]."
    assert result.completion_id == "chatcmpl-123"
    assert result.sources == [
        Source(title="Verlofregeling", url="https://docs.example.com/verlof", label="1"),
        Source(title="https://docs.example.com/x", url="https://docs.example.com/x", label="2"),
    ]


async def test_429_carries_retry_after() -> None:
    _, transport = _capture(429, {"detail": "rate limited"}, {"Retry-After": "17"})
    with pytest.raises(PartnerRateLimited) as exc:
        await _client(transport).chat([{"role": "user", "content": "q"}], None)
    assert exc.value.retry_after == 17
    assert exc.value.request_id


async def test_403_is_auth_error_with_type() -> None:
    body = {
        "detail": {"error": {"type": "permission_error", "message": "Insufficient permissions"}}
    }
    _, transport = _capture(403, body)
    with pytest.raises(PartnerAuthError) as exc:
        await _client(transport).chat([{"role": "user", "content": "q"}], [9])
    assert exc.value.status == 403
    assert exc.value.error_type == "permission_error"


async def test_5xx_is_unavailable() -> None:
    _, transport = _capture(502, {"detail": "upstream"})
    with pytest.raises(PartnerUnavailable) as exc:
        await _client(transport).list_knowledge_bases()
    assert exc.value.status == 502


async def test_connect_error_is_unavailable() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(PartnerUnavailable):
        await _client(httpx.MockTransport(handle)).list_knowledge_bases()


async def test_list_knowledge_bases() -> None:
    kbs = [{"id": 1, "name": "Handboek", "slug": "handboek", "access_level": "read"}]
    seen, transport = _capture(body=kbs)
    result = await _client(transport).list_knowledge_bases()
    assert seen[0].method == "GET"
    assert [(kb.id, kb.name) for kb in result] == [(1, "Handboek")]


async def test_feedback_body() -> None:
    seen, transport = _capture(201, {})
    await _client(transport).feedback("chatcmpl-123", "slack:T1:C1:1.0", "thumbsDown", "klopt niet")
    assert json.loads(seen[0].content) == {
        "message_id": "chatcmpl-123",
        "conversation_id": "slack:T1:C1:1.0",
        "rating": "thumbsDown",
        "text": "klopt niet",
    }
