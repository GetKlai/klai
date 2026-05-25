"""Tests for POST /partner/v1/chat/completions.

SPEC-API-001 TASK-008 + TASK-009:
- Model validation (only klai-primary, klai-fast allowed)
- Messages validation (non-empty, at least one user message)
- KB out-of-scope -> 403
- Retrieval timeout -> 502
- Happy path non-streaming returns OpenAI-shaped JSON
- Retrieval log scheduled as async task
- kb_id -> kb_slug translation
- Streaming returns text/event-stream with SSE chunks
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException
from helpers import FakeKB, FakeResult, make_partner_auth


def _http_request_stub() -> MagicMock:
    """Minimal Request stub for chat_completions; widget-only audit
    helpers read headers + client.host, so set both."""
    req = MagicMock()
    req.headers = {}
    req.client = MagicMock(host="127.0.0.1")
    return req


@pytest.fixture(autouse=True)
def _mock_retrieval_log(monkeypatch):
    """Prevent orphaned coroutines when asyncio.create_task is mocked.

    Tests in this module mock the entire asyncio module.  If write_retrieval_log
    were a real coroutine function, GC at interpreter shutdown would emit
    'coroutine was never awaited' — after all hooks have already been cleaned up.
    Replacing it with a plain MagicMock prevents coroutine creation entirely.
    The assertion tests (test_retrieval_log_scheduled) still verify that
    asyncio.create_task was called; only the argument type changes.
    """
    monkeypatch.setattr("app.api.partner.write_retrieval_log", MagicMock())


# ---------------------------------------------------------------------------
# TASK-008: Non-streaming chat completions
# ---------------------------------------------------------------------------


def test_llm_messages_strip_widget_metadata():
    """Widget-only metadata must never be sent back to the LLM provider."""
    from app.services.partner_chat import _augment_messages_with_system_prompt

    messages = [
        {"role": "user", "content": "What is Klai?"},
        {
            "role": "assistant",
            "content": "Klai is an AI workspace. (1)",
            "sources": [{"label": "1", "title": "Klai", "url": "https://getklai.com/"}],
        },
        {"role": "user", "content": "And ownership?"},
    ]

    augmented = _augment_messages_with_system_prompt(messages, "system prompt")

    assert augmented == [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "What is Klai?"},
        {"role": "assistant", "content": "Klai is an AI workspace. (1)"},
        {"role": "user", "content": "And ownership?"},
    ]
    assert all(set(message) == {"role", "content"} for message in augmented)


@pytest.mark.asyncio
async def test_invalid_model_returns_400():
    """Model must be klai-primary or klai-fast; anything else -> 400."""
    from app.api.partner import ChatCompletionsRequest, chat_completions

    req = ChatCompletionsRequest(
        messages=[{"role": "user", "content": "Hello"}],
        model="gpt-4",
        stream=False,
    )

    with pytest.raises(HTTPException) as exc_info:
        await chat_completions(request=req, http_request=_http_request_stub(), auth=make_partner_auth(), db=AsyncMock())
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_empty_messages_returns_400():
    """Empty messages list -> rejected by Pydantic (min_length=1)."""
    from pydantic import ValidationError

    from app.api.partner import ChatCompletionsRequest

    with pytest.raises(ValidationError):
        ChatCompletionsRequest(
            messages=[],
            model="klai-primary",
            stream=False,
        )


@pytest.mark.asyncio
async def test_messages_only_system_role_returns_400():
    """Messages with only system role and no user message -> 400."""
    from app.api.partner import ChatCompletionsRequest, chat_completions

    req = ChatCompletionsRequest(
        messages=[{"role": "system", "content": "You are helpful"}],
        model="klai-primary",
        stream=False,
    )

    with pytest.raises(HTTPException) as exc_info:
        await chat_completions(request=req, http_request=_http_request_stub(), auth=make_partner_auth(), db=AsyncMock())
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_kb_out_of_scope_returns_403():
    """Requesting a KB not in key scope -> 403."""
    from app.api.partner import ChatCompletionsRequest, chat_completions

    req = ChatCompletionsRequest(
        messages=[{"role": "user", "content": "Hello"}],
        model="klai-primary",
        stream=False,
        knowledge_base_ids=[99],  # not in scope
    )

    with pytest.raises(HTTPException) as exc_info:
        await chat_completions(
            request=req,
            http_request=_http_request_stub(),
            auth=make_partner_auth(kb_access={10: "read"}),
            db=AsyncMock(),
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_retrieval_timeout_returns_502():
    """Retrieval-api timeout -> 502 Bad Gateway."""
    from app.api.partner import ChatCompletionsRequest, chat_completions

    fake_kbs = [FakeKB(id=10, name="KB Alpha", slug="kb-alpha", org_id=42)]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeResult(rows=fake_kbs))

    req = ChatCompletionsRequest(
        messages=[{"role": "user", "content": "Hello"}],
        model="klai-primary",
        stream=False,
    )

    with (
        patch("app.api.partner.retrieve_context", side_effect=httpx.ReadTimeout("timeout")),
        pytest.raises(HTTPException) as exc_info,
    ):
        await chat_completions(
            request=req, http_request=_http_request_stub(), auth=make_partner_auth(kb_access={10: "read"}), db=db
        )
    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_happy_path_non_streaming():
    """Non-streaming: returns OpenAI-shaped JSON with choices."""
    from app.api.partner import ChatCompletionsRequest, chat_completions

    fake_kbs = [FakeKB(id=10, name="KB Alpha", slug="kb-alpha", org_id=42)]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeResult(rows=fake_kbs))

    req = ChatCompletionsRequest(
        messages=[{"role": "user", "content": "Hello"}],
        model="klai-primary",
        stream=False,
    )

    litellm_response = {
        "id": "chatcmpl-123",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Hi there!"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }

    with (
        patch("app.api.partner.retrieve_context", return_value=([{"chunk_id": "c1", "text": "ctx"}], "prompt", [])),
        patch("app.api.partner.chat_completion_non_streaming", return_value=litellm_response),
        patch("app.api.partner.asyncio"),
    ):
        result = await chat_completions(
            request=req, http_request=_http_request_stub(), auth=make_partner_auth(kb_access={10: "read"}), db=db
        )

    assert result["id"] == "chatcmpl-123"
    assert result["choices"][0]["message"]["content"] == "Hi there!"


@pytest.mark.asyncio
async def test_retrieval_log_scheduled():
    """Retrieval log is scheduled as fire-and-forget asyncio.create_task."""
    from app.api.partner import ChatCompletionsRequest, chat_completions

    fake_kbs = [FakeKB(id=10, name="KB Alpha", slug="kb-alpha", org_id=42)]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeResult(rows=fake_kbs))

    req = ChatCompletionsRequest(
        messages=[{"role": "user", "content": "Hello"}],
        model="klai-primary",
        stream=False,
    )

    litellm_response = {
        "id": "chatcmpl-123",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hi"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }

    with (
        patch(
            "app.api.partner.retrieve_context",
            return_value=([{"chunk_id": "c1", "text": "ctx", "reranker_score": 0.9}], "prompt", []),
        ),
        patch("app.api.partner.chat_completion_non_streaming", return_value=litellm_response),
        patch("app.api.partner.asyncio") as mock_asyncio,
    ):
        await chat_completions(
            request=req, http_request=_http_request_stub(), auth=make_partner_auth(kb_access={10: "read"}), db=db
        )

    mock_asyncio.create_task.assert_called_once()


@pytest.mark.asyncio
async def test_kb_id_to_slug_translation():
    """kb_ids are translated to kb_slugs via DB lookup before retrieval."""
    from app.api.partner import ChatCompletionsRequest, chat_completions

    fake_kbs = [
        FakeKB(id=10, name="KB Alpha", slug="kb-alpha", org_id=42),
        FakeKB(id=20, name="KB Beta", slug="kb-beta", org_id=42),
    ]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeResult(rows=fake_kbs))

    req = ChatCompletionsRequest(
        messages=[{"role": "user", "content": "Hello"}],
        model="klai-primary",
        stream=False,
        knowledge_base_ids=[10, 20],
    )

    litellm_response = {
        "id": "chatcmpl-123",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hi"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }

    with (
        patch("app.api.partner.retrieve_context", return_value=([], "prompt", [])) as mock_retrieve,
        patch("app.api.partner.chat_completion_non_streaming", return_value=litellm_response),
        patch("app.api.partner.asyncio"),
    ):
        await chat_completions(request=req, http_request=_http_request_stub(), auth=make_partner_auth(), db=db)

    call_kwargs = mock_retrieve.call_args
    kb_slugs_arg = call_kwargs[1].get("kb_slugs") or call_kwargs[0][2]
    assert set(kb_slugs_arg) == {"kb-alpha", "kb-beta"}


@pytest.mark.asyncio
async def test_widget_system_prompt_loaded_for_widget_auth():
    """Widget JWT calls load private behaviour instructions from widget_config."""
    from app.api.partner import _widget_system_prompt
    from app.api.partner_dependencies import PartnerAuthContext

    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=FakeResult(
            rows=[{"system_prompt": "Be brief and friendly."}],
        )
    )
    auth = PartnerAuthContext(
        key_id="wgt_abc123",
        org_id=42,
        zitadel_org_id="zit-org-42",
        permissions={"chat": True},
        kb_access={10: "read"},
        rate_limit_rpm=60,
    )

    assert await _widget_system_prompt(auth, db) == "Be brief and friendly."


@pytest.mark.asyncio
async def test_widget_system_prompt_ignored_for_partner_keys():
    """Partner API keys do not read widget_config behaviour instructions."""
    from app.api.partner import _widget_system_prompt

    db = AsyncMock()

    assert await _widget_system_prompt(make_partner_auth(), db) is None
    db.execute.assert_not_called()


# ---------------------------------------------------------------------------
# TASK-009: Streaming chat completions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_returns_event_stream_content_type():
    """Streaming response has content-type text/event-stream."""
    from app.api.partner import ChatCompletionsRequest, chat_completions

    fake_kbs = [FakeKB(id=10, name="KB Alpha", slug="kb-alpha", org_id=42)]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeResult(rows=fake_kbs))

    req = ChatCompletionsRequest(
        messages=[{"role": "user", "content": "Hello"}],
        model="klai-primary",
        stream=True,
    )

    async def mock_streaming_gen():
        yield b"data: {}\n\n"
        yield b"data: [DONE]\n\n"

    with (
        patch("app.api.partner.retrieve_context", return_value=([{"chunk_id": "c1", "text": "ctx"}], "prompt", [])),
        patch("app.api.partner.chat_completion_streaming", return_value=mock_streaming_gen()),
        patch("app.api.partner.asyncio"),
    ):
        from starlette.responses import StreamingResponse

        result = await chat_completions(
            request=req, http_request=_http_request_stub(), auth=make_partner_auth(kb_access={10: "read"}), db=db
        )
        assert isinstance(result, StreamingResponse)
        assert result.media_type == "text/event-stream"


@pytest.mark.asyncio
async def test_widget_streaming_uses_structured_citation_mode():
    """Widget calls render sources from backend metadata, not model-authored URLs."""
    from app.api.partner import ChatCompletionsRequest, chat_completions

    fake_kbs = [FakeKB(id=10, name="KB Alpha", slug="kb-alpha", org_id=42)]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeResult(rows=fake_kbs))
    auth = make_partner_auth(kb_access={10: "read"})
    auth.key_id = "wgt_901"

    req = ChatCompletionsRequest(
        messages=[{"role": "user", "content": "Welke gegevens?"}],
        model="klai-primary",
        stream=True,
    )

    async def mock_streaming_gen():
        yield b"data: [DONE]\n\n"

    retrieved_chunks = [
        {
            "chunk_id": "c1",
            "title": "Privacy policy",
            "source_url": "https://www.getklai.com/docs/legal/privacy",
            "text": "Privacy context",
        }
    ]

    with (
        patch(
            "app.api.partner.retrieve_context",
            return_value=(
                retrieved_chunks,
                "prompt",
                [
                    {
                        "title": "Privacy policy",
                        "url": "https://www.getklai.com/docs/legal/privacy",
                    }
                ],
            ),
        ) as mock_retrieve,
        patch("app.api.partner.chat_completion_streaming", return_value=mock_streaming_gen()) as chat_stream,
        patch("app.api.partner.asyncio"),
    ):
        await chat_completions(request=req, http_request=_http_request_stub(), auth=auth, db=db)

    assert mock_retrieve.call_args.kwargs["backend_managed_citations"] is True
    assert chat_stream.call_args.kwargs["citation_output"] == "markers"
    assert chat_stream.call_args.kwargs["citation_chunks"] == retrieved_chunks
    assert chat_stream.call_args.kwargs["citation_source_urls"] == {}
    assert chat_stream.call_args.kwargs["citation_source_metadata"] == {}


@pytest.mark.asyncio
async def test_partner_streaming_uses_backend_managed_citations():
    """Partner API calls use the same deterministic source selector as widgets."""
    from app.api.partner import ChatCompletionsRequest, chat_completions

    fake_kbs = [FakeKB(id=10, name="KB Alpha", slug="kb-alpha", org_id=42)]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeResult(rows=fake_kbs))
    auth = make_partner_auth(kb_access={10: "read"})

    req = ChatCompletionsRequest(
        messages=[{"role": "user", "content": "Hoe voeg ik een gebruiker toe?"}],
        model="klai-primary",
        stream=True,
    )

    async def mock_streaming_gen():
        yield b"data: [DONE]\n\n"

    retrieved_chunks = [
        {
            "chunk_id": "c1",
            "title": "Invite and remove people",
            "source_url": "https://www.getklai.com/docs/klai-help/invite-and-remove-people",
            "text": "Invite a colleague from Admin > Users.",
        }
    ]

    with (
        patch(
            "app.api.partner.retrieve_context",
            return_value=(
                retrieved_chunks,
                "prompt",
                [
                    {
                        "title": "Invite and remove people",
                        "url": "https://www.getklai.com/docs/klai-help/invite-and-remove-people",
                    }
                ],
            ),
        ) as mock_retrieve,
        patch("app.api.partner.chat_completion_streaming", return_value=mock_streaming_gen()) as chat_stream,
        patch("app.api.partner.asyncio"),
    ):
        await chat_completions(request=req, http_request=_http_request_stub(), auth=auth, db=db)

    assert mock_retrieve.call_args.kwargs["backend_managed_citations"] is True
    assert chat_stream.call_args.kwargs["citation_output"] == "markers"
    assert chat_stream.call_args.kwargs["citation_source_urls"] == {}
    assert chat_stream.call_args.kwargs["citation_source_metadata"] == {}


@pytest.mark.asyncio
async def test_streaming_chunks_forwarded():
    """Mock LiteLLM streaming chunks are forwarded byte-for-byte."""
    from app.api.partner import ChatCompletionsRequest, chat_completions

    fake_kbs = [FakeKB(id=10, name="KB Alpha", slug="kb-alpha", org_id=42)]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeResult(rows=fake_kbs))

    req = ChatCompletionsRequest(
        messages=[{"role": "user", "content": "Hello"}],
        model="klai-primary",
        stream=True,
    )

    expected_bytes = [
        b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n',
        b"data: [DONE]\n\n",
    ]

    async def mock_streaming_gen():
        for chunk in expected_bytes:
            yield chunk

    with (
        patch("app.api.partner.retrieve_context", return_value=([{"chunk_id": "c1", "text": "ctx"}], "prompt", [])),
        patch("app.api.partner.chat_completion_streaming", return_value=mock_streaming_gen()),
        patch("app.api.partner.asyncio"),
    ):
        result = await chat_completions(
            request=req, http_request=_http_request_stub(), auth=make_partner_auth(kb_access={10: "read"}), db=db
        )

        received = []
        async for chunk in result.body_iterator:
            received.append(chunk)

        assert len(received) == 2
        assert b"[DONE]" in received[-1]


@pytest.mark.asyncio
async def test_streaming_done_terminator():
    """[DONE] terminator is present in streaming output."""
    from app.api.partner import ChatCompletionsRequest, chat_completions

    fake_kbs = [FakeKB(id=10, name="KB Alpha", slug="kb-alpha", org_id=42)]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeResult(rows=fake_kbs))

    req = ChatCompletionsRequest(
        messages=[{"role": "user", "content": "Hello"}],
        model="klai-primary",
        stream=True,
    )

    async def mock_streaming_gen():
        yield b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n'
        yield b"data: [DONE]\n\n"

    with (
        patch("app.api.partner.retrieve_context", return_value=([], "prompt", [])),
        patch("app.api.partner.chat_completion_streaming", return_value=mock_streaming_gen()),
        patch("app.api.partner.asyncio"),
    ):
        result = await chat_completions(
            request=req, http_request=_http_request_stub(), auth=make_partner_auth(kb_access={10: "read"}), db=db
        )

        all_bytes = b""
        async for chunk in result.body_iterator:
            all_bytes += chunk

        assert b"[DONE]" in all_bytes


@pytest.mark.asyncio
async def test_streaming_retrieval_log_fires():
    """Retrieval log fires even on streaming path."""
    from app.api.partner import ChatCompletionsRequest, chat_completions

    fake_kbs = [FakeKB(id=10, name="KB Alpha", slug="kb-alpha", org_id=42)]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeResult(rows=fake_kbs))

    req = ChatCompletionsRequest(
        messages=[{"role": "user", "content": "Hello"}],
        model="klai-primary",
        stream=True,
    )

    async def mock_streaming_gen():
        yield b"data: [DONE]\n\n"

    with (
        patch("app.api.partner.retrieve_context", return_value=([{"chunk_id": "c1"}], "prompt", [])),
        patch("app.api.partner.chat_completion_streaming", return_value=mock_streaming_gen()),
        patch("app.api.partner.asyncio") as mock_asyncio,
    ):
        await chat_completions(
            request=req, http_request=_http_request_stub(), auth=make_partner_auth(kb_access={10: "read"}), db=db
        )

    mock_asyncio.create_task.assert_called_once()


@pytest.mark.asyncio
async def test_non_streaming_strips_unretrieved_links(monkeypatch):
    """Non-streaming partner/widget completions strip invented Markdown URLs."""
    from app.services.partner_chat import chat_completion_non_streaming

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {"message": {"content": ("Bron: [goed](https://getklai.com/) [fout](https://getklai.com/404)")}}
                ]
            }

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, *_, **__):
            return _Resp()

    monkeypatch.setattr("app.services.partner_chat.httpx.AsyncClient", lambda timeout: _Client())

    settings = MagicMock()
    settings.litellm_base_url = "http://litellm"
    settings.litellm_master_key = "secret"

    body = await chat_completion_non_streaming(
        messages=[{"role": "user", "content": "Is Klai open source?"}],
        model="klai-primary",
        temperature=0.7,
        system_prompt="prompt",
        settings=settings,
        allowed_source_urls={"https://getklai.com/"},
    )

    content = body["choices"][0]["message"]["content"]
    assert "[goed](https://getklai.com/)" in content
    assert "https://getklai.com/404" not in content
    assert "fout" in content


@pytest.mark.asyncio
async def test_streaming_strips_unretrieved_links(monkeypatch):
    """Streaming partner/widget completions sanitize split Markdown links before emitting."""
    from app.services.partner_chat import chat_completion_streaming

    events = [
        {"choices": [{"delta": {"content": "Bron: [fout](https://getklai.com/4"}}]},
        {"choices": [{"delta": {"content": "04) en [goed](https://getklai.com/)"}}]},
        {"choices": [{"delta": {"content": "."}}]},
    ]

    class _StreamResp:
        def raise_for_status(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def aiter_lines(self):
            for event in events:
                yield "data: " + json.dumps(event)
            yield "data: [DONE]"

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        def stream(self, *_, **__):
            return _StreamResp()

    monkeypatch.setattr("app.services.partner_chat.httpx.AsyncClient", lambda timeout: _Client())

    settings = MagicMock()
    settings.litellm_base_url = "http://litellm"
    settings.litellm_master_key = "secret"

    chunks = []
    async for chunk in chat_completion_streaming(
        messages=[{"role": "user", "content": "Is Klai open source?"}],
        model="klai-primary",
        temperature=0.7,
        system_prompt="prompt",
        settings=settings,
        allowed_source_urls={"https://getklai.com/"},
    ):
        chunks.append(chunk)

    body = b"".join(chunks).decode()
    assert "https://getklai.com/404" not in body
    assert "fout" in body
    assert "https://getklai.com/" in body
    assert "[DONE]" in body


# ---------------------------------------------------------------------------
# 2026-05-05 regression-guard: SPEC-SEC-IDENTITY-ASSERT-001 caller-service
# ---------------------------------------------------------------------------


def test_build_system_prompt_includes_source_urls_for_widget_citations():
    """Widget/partner prompts must expose literal source_url values.

    Regression guard for widget answers inventing docs pages for [n] links
    when the retrieved Qdrant chunks only had canonical source URLs in
    payload metadata.
    """
    from app.services.partner_chat import _build_system_prompt

    prompt = _build_system_prompt(
        [
            {
                "title": "AI infrastructure that stays yours",
                "source_url": "https://www.getklai.com/",
                "source_label": "web_crawler",
                "text": "Everything Klai runs is open source.",
            },
            {
                "metadata": {"title": "Our mission"},
                "source_url": "https://www.getklai.com/docs/company/mission",
                "text": "Klai is steward-owned.",
            },
        ]
    )

    assert "source_url: https://getklai.com/" in prompt
    assert "source_url: https://getklai.com/docs/company/mission" in prompt
    assert "Use only literal source_url values" in prompt
    assert "Never turn a title, heading, or documentation phrase into a URL" in prompt


def test_build_system_prompt_reads_nested_chunk_source_urls():
    """Partner chat should use chunk metadata URLs, never literal sentinel values."""
    from app.services.partner_chat import _build_system_prompt, _citation_source_urls_from_chunks

    chunks = [
        {
            "title": "Privacy",
            "source_url": "undefined",
            "metadata": {"source_url": "https://www.getklai.com/privacy"},
            "text": "Klai collects account data and query data.",
        },
        {
            "source": {"url": "https://www.getklai.com/dpa"},
            "text": "Klai uses subprocessors for billing.",
        },
    ]

    prompt = _build_system_prompt(chunks)

    assert "source_url: https://getklai.com/privacy" in prompt
    assert "source_url: https://getklai.com/dpa" in prompt
    assert "source_url: undefined" not in prompt
    assert _citation_source_urls_from_chunks(chunks) == {
        1: "https://getklai.com/privacy",
        2: "https://getklai.com/dpa",
    }


def test_placeholder_source_paths_are_not_valid_urls():
    """Source guards must reject browser-resolved placeholder paths like /undefined."""
    from app.services.partner_chat import _normalise_guard_url

    assert _normalise_guard_url("undefined") == ""
    assert _normalise_guard_url("https://getklai.com/undefined") == ""
    assert _normalise_guard_url("https://getklai.com/null") == ""
    assert _normalise_guard_url("https://getklai.com/none?x=1") == ""


def test_build_system_prompt_includes_widget_system_prompt():
    """Widget admin behaviour instructions are added without replacing KB grounding."""
    from app.services.partner_chat import _build_system_prompt

    prompt = _build_system_prompt(
        [{"title": "Policy", "source_url": "https://docs.example.com/policy", "text": "Use policy text."}],
        widget_system_prompt="Use a calm, support-oriented tone.",
    )

    assert "Widget behaviour instructions" in prompt
    assert "Use a calm, support-oriented tone." in prompt
    assert "source_url: https://docs.example.com/policy" in prompt
    assert "URL rules for citations and source links" in prompt


def test_build_system_prompt_can_leave_citations_to_backend():
    """Widget prompts should not invite the model to write source markers."""
    from app.services.partner_chat import _build_system_prompt

    prompt = _build_system_prompt(
        [{"title": "Policy", "source_url": "https://docs.example.com/policy", "text": "Use policy text."}],
        backend_managed_citations=True,
    )

    assert "source_url:" not in prompt
    assert "the application adds citations after generation" in prompt
    assert "Do not write URLs" in prompt


def test_build_system_prompt_renders_structured_evidence_context():
    from app.services.partner_chat import _build_system_prompt

    prompt = _build_system_prompt(
        [
            {
                "title": "Invite and remove people",
                "source_url": "https://docs.example.com/invite",
                "heading_path": "Admin > Mensen",
                "text": "Admin > Mensen\n\n4. Voer het werk-emailadres in.\n5. Selecteer een rol.",
                "chunk_type": "procedural",
            }
        ],
        backend_managed_citations=True,
    )

    assert "Evidence E1" in prompt
    assert "Source title: Invite and remove people" in prompt
    assert "Section path: Admin > Mensen" in prompt
    assert "List note: this excerpt starts mid ordered-list" in prompt
    assert "Admin > Mensen\n\n4." not in prompt
    assert "source_url:" not in prompt


def test_citation_composer_adds_sources_when_model_has_no_citations():
    """Widget sources come from retrieved chunks, not from model-authored markers."""
    from app.services.citations import compose_citations

    composed = compose_citations(
        "Klai is steward-owned and mission-led.",
        [
            {
                "title": "Steward ownership",
                "source_url": "https://www.getklai.com/docs/company/steward-ownership",
                "text": "Klai is steward-owned and protected from external takeover.",
            }
        ],
    )

    assert composed.content == "Klai is steward-owned and mission-led."
    assert composed.sources == [
        {
            "label": "1",
            "title": "Steward ownership",
            "url": "https://getklai.com/docs/company/steward-ownership",
        }
    ]


def test_citation_composer_ignores_model_citation_text_and_source_lists():
    """Old model citation syntax must not control the rendered widget links."""
    from app.services.citations import compose_citations

    composed = compose_citations(
        "Steward ownership protects Klai (1).\n\n(1)Stichting DOEN, *Wat is steward ownership?* (2023)",
        [
            {
                "title": "Klai steward ownership",
                "source_url": "https://www.getklai.com/docs/company/steward-ownership",
                "text": "Steward ownership protects Klai from mission drift.",
            }
        ],
    )

    assert composed.content == "Steward ownership protects Klai."
    assert composed.sources[0]["url"] == "https://getklai.com/docs/company/steward-ownership"
    assert "Stichting DOEN" not in composed.content


def test_citation_composer_dedupes_www_and_non_www_sources():
    """A document must render once even when retrieval returns several chunks."""
    from app.services.citations import compose_citations

    composed = compose_citations(
        "Klai stores account data and query data.",
        [
            {
                "title": "Privacy policy",
                "source_url": "https://www.getklai.com/docs/legal/privacy",
                "text": "Klai stores account data.",
            },
            {
                "title": "Privacy policy duplicate",
                "source_url": "https://getklai.com/docs/legal/privacy/",
                "text": "Klai stores query data.",
            },
        ],
    )

    assert composed.content == "Klai stores account data and query data."
    assert composed.sources == [
        {
            "label": "1",
            "title": "Privacy policy",
            "url": "https://getklai.com/docs/legal/privacy",
        }
    ]


def test_citation_composer_uses_source_ref_as_url_fallback():
    """Retrieval may expose a citable URL as source_ref instead of source_url."""
    from app.services.citations import compose_citations

    composed = compose_citations(
        "Klai stores account data.",
        [
            {
                "title": "Privacy policy",
                "source_url": None,
                "source_ref": "https://www.getklai.com/docs/legal/privacy",
                "text": "Klai stores account data.",
            }
        ],
    )

    assert composed.content == "Klai stores account data."
    assert composed.sources[0]["url"] == "https://getklai.com/docs/legal/privacy"


def test_sanitizer_removes_links_not_in_retrieved_sources():
    """Partner/widget output cannot keep URLs absent from retrieved chunk metadata."""
    from app.services.partner_chat import _sanitize_kb_markdown_output

    sanitized, changed = _sanitize_kb_markdown_output(
        "Good [home](https://getklai.com/) bad [fake](https://getklai.com/missing) raw https://bad.example/x",
        allowed_source_urls={"https://getklai.com/"},
    )

    assert changed == 2
    assert "[home](https://getklai.com/)" in sanitized
    assert "https://getklai.com/missing" not in sanitized
    assert "https://bad.example/x" not in sanitized
    assert "link removed" not in sanitized
    assert "fake" in sanitized


def test_sanitizer_rewrites_citations_from_chunk_source_map():
    """The model may choose [n]; the backend owns the actual citation URL."""
    from app.services.partner_chat import _sanitize_kb_markdown_output

    sanitized, changed = _sanitize_kb_markdown_output(
        "Klai verwerkt accountgegevens [1](undefined) en factuurgegevens [2].",
        allowed_source_urls=set(),
        citation_source_urls={
            1: "https://www.getklai.com/privacy",
            2: "https://www.getklai.com/subprocessors",
        },
    )

    assert changed >= 1
    assert "[1](https://getklai.com/privacy)" in sanitized
    assert "[2](https://getklai.com/subprocessors)" in sanitized
    assert "undefined" not in sanitized


def test_sanitizer_deduplicates_adjacent_citations_for_same_document():
    """Multiple chunks from one document should render as one source link."""
    from app.services.partner_chat import _sanitize_kb_markdown_output

    sanitized, changed = _sanitize_kb_markdown_output(
        "Klai is steward-owned [1][2], [3] and mission-led [4].",
        allowed_source_urls=set(),
        citation_source_urls={
            1: "https://www.getklai.com/docs/company/steward-ownership",
            2: "https://getklai.com/docs/company/steward-ownership",
            3: "https://www.getklai.com/docs/company/steward-ownership/",
            4: "https://www.getklai.com/docs/company/mission",
        },
    )

    assert changed >= 1
    assert sanitized == (
        "Klai is steward-owned "
        "[1](https://getklai.com/docs/company/steward-ownership) "
        "and mission-led [2](https://getklai.com/docs/company/mission)."
    )
    assert "www.getklai.com" not in sanitized


def test_sanitizer_removes_repeated_same_document_citations_across_answer():
    """A widget answer should not show the same source link after every bullet."""
    from app.services.partner_chat import _sanitize_kb_markdown_output

    sanitized, changed = _sanitize_kb_markdown_output(
        ("Klai B.V. collects:\n- Account data [1].\n- Usage data [1].\n- Query data [1].\n- Billing data [1]."),
        allowed_source_urls=set(),
        citation_source_urls={1: "https://www.getklai.com/docs/legal/privacy"},
    )

    assert changed >= 1
    assert sanitized.count("https://getklai.com/docs/legal/privacy") == 1
    assert sanitized == (
        "Klai B.V. collects:\n"
        "- Account data [1](https://getklai.com/docs/legal/privacy).\n"
        "- Usage data.\n"
        "- Query data.\n"
        "- Billing data."
    )


def test_sanitizer_separates_multiple_different_citations():
    """Adjacent different source links must not render as one glued label."""
    from app.services.partner_chat import _sanitize_kb_markdown_output

    sanitized, changed = _sanitize_kb_markdown_output(
        "Klai is public [1][3][7].",
        allowed_source_urls=set(),
        citation_source_urls={
            1: "https://getklai.com/docs/company/open-source",
            3: "https://getklai.com/docs/legal/dpa",
            7: "https://getklai.com/docs/legal/subprocessors",
        },
    )

    assert changed >= 1
    assert sanitized == (
        "Klai is public "
        "[1](https://getklai.com/docs/company/open-source), "
        "[2](https://getklai.com/docs/legal/dpa), "
        "[3](https://getklai.com/docs/legal/subprocessors)."
    )


def test_sanitizer_maps_chunk_numbers_to_document_source_numbers():
    """Visible source numbers are document numbers, not raw retrieval chunk indices."""
    from app.services.partner_chat import _sanitize_kb_markdown_output

    sanitized, changed = _sanitize_kb_markdown_output(
        "Steward ownership is written into the articles [13].",
        allowed_source_urls=set(),
        citation_source_urls={
            1: "https://getklai.com/docs/company/open-source",
            13: "https://getklai.com/docs/company/steward-ownership",
        },
    )

    assert changed == 0
    assert sanitized == (
        "Steward ownership is written into the articles [1](https://getklai.com/docs/company/steward-ownership)."
    )


def test_sanitizer_repairs_malformed_citation_link_text():
    """The backend should not leak '4(https://...)' into the widget."""
    from app.services.partner_chat import _sanitize_kb_markdown_output

    sanitized, changed = _sanitize_kb_markdown_output(
        "Naam en e-mailadres 4(https://getklai.com/docs/legal/privacy).",
        allowed_source_urls=set(),
        citation_source_urls={4: "https://getklai.com/docs/legal/privacy"},
    )

    assert changed >= 1
    assert sanitized == ("Naam en e-mailadres [1](https://getklai.com/docs/legal/privacy).")


def test_sanitizer_can_emit_structured_citation_markers():
    """Widget output keeps source markers in text and moves URLs to structured sources."""
    from app.services.partner_chat import _sanitize_kb_markdown_output

    emitted_order: list[str] = []
    sanitized, changed = _sanitize_kb_markdown_output(
        ("Naam en e-mailadres 4(https://getklai.com/docs/legal/privacy). Gebruik [4]. DPA [7]."),
        allowed_source_urls=set(),
        citation_source_urls={
            4: "https://getklai.com/docs/legal/privacy",
            7: "https://getklai.com/docs/legal/dpa",
        },
        emitted_source_key_order=emitted_order,
        citation_output="markers",
    )

    assert changed >= 1
    assert sanitized == "Naam en e-mailadres (1). Gebruik. DPA (2)."
    assert emitted_order == [
        "https://getklai.com/docs/legal/privacy",
        "https://getklai.com/docs/legal/dpa",
    ]


def test_sanitizer_groups_structured_citation_markers_with_commas():
    """Widget markers use one compact parenthesized group for same-position sources."""
    from app.services.partner_chat import _sanitize_kb_markdown_output

    emitted_order: list[str] = []
    sanitized, changed = _sanitize_kb_markdown_output(
        "Klai is public [1][3][7].",
        allowed_source_urls=set(),
        citation_source_urls={
            1: "https://getklai.com/docs/company/open-source",
            3: "https://getklai.com/docs/legal/dpa",
            7: "https://getklai.com/docs/legal/subprocessors",
        },
        emitted_source_key_order=emitted_order,
        citation_output="markers",
    )

    assert changed >= 1
    assert sanitized == "Klai is public (1,2,3)."
    assert emitted_order == [
        "https://getklai.com/docs/company/open-source",
        "https://getklai.com/docs/legal/dpa",
        "https://getklai.com/docs/legal/subprocessors",
    ]


def test_sanitizer_converts_bare_number_citation_runs_for_widget_markers():
    """Models sometimes emit citation numbers as '3,5,8' without brackets."""
    from app.services.partner_chat import _sanitize_kb_markdown_output

    emitted_order: list[str] = []
    sanitized, changed = _sanitize_kb_markdown_output(
        "Klai is een Europees AI-platform. 3,5,8",
        allowed_source_urls=set(),
        citation_source_urls={
            3: "https://getklai.com/",
            5: "https://getklai.com/docs/company/steward-ownership",
            8: "https://getklai.com/docs/legal/privacy",
        },
        emitted_source_key_order=emitted_order,
        citation_output="markers",
    )

    assert changed >= 1
    assert sanitized == "Klai is een Europees AI-platform. (1,2,3)"
    assert emitted_order == [
        "https://getklai.com/",
        "https://getklai.com/docs/company/steward-ownership",
        "https://getklai.com/docs/legal/privacy",
    ]


def test_sanitizer_removes_parenthesized_raw_urls_without_placeholder():
    """Raw unretrieved URLs in parentheses should not render as '(link removed)'."""
    from app.services.partner_chat import _sanitize_kb_markdown_output

    sanitized, changed = _sanitize_kb_markdown_output(
        "Klai is open source [1] (https://github.com/getklai/klai).",
        allowed_source_urls={"https://getklai.com/"},
    )

    assert changed == 1
    assert "https://github.com/getklai/klai" not in sanitized
    assert "link removed" not in sanitized
    assert "()" not in sanitized
    assert sanitized == "Klai is open source [1]."


def test_stream_sanitizer_holds_split_markdown_links_until_safe():
    """Split SSE deltas cannot leak an unapproved URL before the closing ')'."""
    from app.services.partner_chat import _pop_sanitized_stream_text

    allowed = {"https://getklai.com/"}
    out1, pending, changed1 = _pop_sanitized_stream_text(
        "See [fake](https://getklai.com/miss",
        allowed_source_urls=allowed,
        final=False,
    )
    out2, pending, changed2 = _pop_sanitized_stream_text(
        pending + "ing) and [home](https://getklai.com/).",
        allowed_source_urls=allowed,
        final=True,
    )

    assert out1 == "See "
    assert pending == ""
    assert changed1 + changed2 == 1
    assert "https://getklai.com/missing" not in out1 + out2
    assert "[home](https://getklai.com/)" in out1 + out2


def test_stream_sanitizer_rewrites_split_undefined_citation_to_chunk_url():
    """A streamed [1] followed by (undefined) must not become /undefined in the widget."""
    from app.services.partner_chat import _pop_sanitized_stream_text

    citations = {1: "https://www.getklai.com/privacy"}
    out1, pending, changed1 = _pop_sanitized_stream_text(
        "Klai verwerkt accountgegevens [1]",
        allowed_source_urls=set(),
        citation_source_urls=citations,
        final=False,
    )
    out2, pending, changed2 = _pop_sanitized_stream_text(
        pending + "(undefined).",
        allowed_source_urls=set(),
        citation_source_urls=citations,
        final=True,
    )

    body = out1 + out2
    assert out1 == "Klai verwerkt accountgegevens "
    assert pending == ""
    assert changed1 + changed2 == 1
    assert body == "Klai verwerkt accountgegevens [1](https://getklai.com/privacy)."
    assert "undefined" not in body


def test_stream_sanitizer_links_bare_citation_from_chunk_url():
    """Bare [n] citations are linked from retrieval metadata once the token is complete."""
    from app.services.partner_chat import _pop_sanitized_stream_text

    out, pending, changed = _pop_sanitized_stream_text(
        "Zie [1].",
        allowed_source_urls=set(),
        citation_source_urls={1: "https://www.getklai.com/privacy"},
        final=True,
    )

    assert pending == ""
    assert changed == 0
    assert out == "Zie [1](https://getklai.com/privacy)."


def test_stream_sanitizer_deduplicates_split_bare_citation_run():
    """Streaming should hold citation runs long enough to avoid repeated links."""
    from app.services.partner_chat import _pop_sanitized_stream_text

    citations = {
        1: "https://www.getklai.com/docs/company/steward-ownership",
        2: "https://getklai.com/docs/company/steward-ownership",
        3: "https://www.getklai.com/docs/company/steward-ownership/",
    }
    out1, pending, changed1 = _pop_sanitized_stream_text(
        "Klai is steward-owned [1]",
        allowed_source_urls=set(),
        citation_source_urls=citations,
        final=False,
    )
    out2, pending, changed2 = _pop_sanitized_stream_text(
        pending + "[2][3].",
        allowed_source_urls=set(),
        citation_source_urls=citations,
        final=True,
    )

    assert out1 == "Klai is steward-owned "
    assert pending == ""
    assert changed1 + changed2 == 1
    assert out2 == "[1](https://getklai.com/docs/company/steward-ownership)."


def test_stream_sanitizer_removes_repeated_same_document_citations_across_deltas():
    """Streaming keeps answer-level source state, not just per-delta state."""
    from app.services.partner_chat import _pop_sanitized_stream_text

    emitted: set[str] = set()
    citations = {1: "https://www.getklai.com/docs/legal/privacy"}
    out1, pending, changed1 = _pop_sanitized_stream_text(
        "Account data [1].\nUsage data ",
        allowed_source_urls=set(),
        citation_source_urls=citations,
        emitted_source_keys=emitted,
        final=False,
    )
    out2, pending, changed2 = _pop_sanitized_stream_text(
        pending + "[1].",
        allowed_source_urls=set(),
        citation_source_urls=citations,
        emitted_source_keys=emitted,
        final=True,
    )

    body = out1 + out2
    assert pending == ""
    assert changed1 + changed2 == 1
    assert body == "Account data [1](https://getklai.com/docs/legal/privacy).\nUsage data."
    assert body.count("https://getklai.com/docs/legal/privacy") == 1


def test_stream_sanitizer_separates_explicit_citation_link_runs():
    """Explicit Markdown citation links can also arrive glued together."""
    from app.services.partner_chat import _pop_sanitized_stream_text

    citations = {
        1: "https://getklai.com/docs/company/open-source",
        3: "https://getklai.com/docs/legal/dpa",
        7: "https://getklai.com/docs/legal/subprocessors",
    }
    out, pending, changed = _pop_sanitized_stream_text(
        (
            "[1](https://getklai.com/docs/company/open-source)"
            "[3](https://getklai.com/docs/legal/dpa)"
            "[7](https://getklai.com/docs/legal/subprocessors)."
        ),
        allowed_source_urls=set(),
        citation_source_urls=citations,
        emitted_source_keys=set(),
        final=True,
    )

    assert pending == ""
    assert changed == 0
    assert out == (
        "[1](https://getklai.com/docs/company/open-source), "
        "[2](https://getklai.com/docs/legal/dpa), "
        "[3](https://getklai.com/docs/legal/subprocessors)."
    )


def test_stream_sanitizer_repairs_malformed_citation_link_text():
    """Streaming should repair label(url) citations before the widget sees them."""
    from app.services.partner_chat import _pop_sanitized_stream_text

    out, pending, changed = _pop_sanitized_stream_text(
        "Naam en e-mailadres 4(https://getklai.com/docs/legal/privacy).",
        allowed_source_urls=set(),
        citation_source_urls={4: "https://getklai.com/docs/legal/privacy"},
        emitted_source_keys=set(),
        final=True,
    )

    assert pending == ""
    assert changed >= 1
    assert out == "Naam en e-mailadres [1](https://getklai.com/docs/legal/privacy)."


def test_stream_sanitizer_can_emit_structured_citation_markers():
    """Streaming widget output keeps URLs out of assistant text."""
    from app.services.partner_chat import _pop_sanitized_stream_text

    emitted_order: list[str] = []
    out, pending, changed = _pop_sanitized_stream_text(
        "Naam en e-mailadres 4(https://getklai.com/docs/legal/privacy).",
        allowed_source_urls=set(),
        citation_source_urls={4: "https://getklai.com/docs/legal/privacy"},
        emitted_source_keys=set(),
        emitted_source_key_order=emitted_order,
        citation_output="markers",
        final=True,
    )

    assert pending == ""
    assert changed >= 1
    assert out == "Naam en e-mailadres (1)."
    assert emitted_order == ["https://getklai.com/docs/legal/privacy"]


def test_stream_sanitizer_converts_bare_number_citation_runs_for_widget_markers():
    """The stream guard keeps citation-number tails long enough to normalize them."""
    from app.services.partner_chat import _pop_sanitized_stream_text

    emitted_order: list[str] = []
    citations = {
        3: "https://getklai.com/",
        5: "https://getklai.com/docs/company/steward-ownership",
        8: "https://getklai.com/docs/legal/privacy",
    }
    out1, pending, changed1 = _pop_sanitized_stream_text(
        "Klai is een Europees AI-platform. 3,",
        allowed_source_urls=set(),
        citation_source_urls=citations,
        emitted_source_keys=set(),
        emitted_source_key_order=emitted_order,
        citation_output="markers",
        final=False,
    )
    out2, pending, changed2 = _pop_sanitized_stream_text(
        pending + "5,8",
        allowed_source_urls=set(),
        citation_source_urls=citations,
        emitted_source_keys=set(),
        emitted_source_key_order=emitted_order,
        citation_output="markers",
        final=True,
    )

    assert pending == ""
    assert changed1 + changed2 >= 1
    assert out1 + out2 == "Klai is een Europees AI-platform. (1,2,3)"
    assert emitted_order == [
        "https://getklai.com/",
        "https://getklai.com/docs/company/steward-ownership",
        "https://getklai.com/docs/legal/privacy",
    ]


@pytest.mark.asyncio
async def test_streaming_widget_mode_emits_structured_sources(monkeypatch):
    """Widget streams receive controlled source metadata before DONE."""
    from app.services.partner_chat import chat_completion_streaming

    events = [{"choices": [{"delta": {"content": "Naam 4(https://getklai.com/docs/legal/privacy)."}}]}]

    class _StreamResp:
        def raise_for_status(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def aiter_lines(self):
            for event in events:
                yield "data: " + json.dumps(event)
            yield "data: [DONE]"

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        def stream(self, *_, **__):
            return _StreamResp()

    monkeypatch.setattr("app.services.partner_chat.httpx.AsyncClient", lambda timeout: _Client())

    settings = MagicMock()
    settings.litellm_base_url = "http://litellm"
    settings.litellm_master_key = "secret"

    chunks = []
    async for chunk in chat_completion_streaming(
        messages=[{"role": "user", "content": "Welke gegevens?"}],
        model="klai-primary",
        temperature=0.7,
        system_prompt="prompt",
        settings=settings,
        allowed_source_urls=set(),
        citation_source_urls={4: "https://getklai.com/docs/legal/privacy"},
        citation_source_metadata={
            "https://getklai.com/docs/legal/privacy": {
                "title": "Privacy policy",
                "url": "https://getklai.com/docs/legal/privacy",
            }
        },
        citation_chunks=[
            {
                "title": "Privacy policy",
                "source_url": "https://getklai.com/docs/legal/privacy",
                "text": "Naam en e-mailadres staan in de privacy policy.",
            }
        ],
        trusted_sources=[
            {
                "label": "1",
                "title": "Privacy policy",
                "url": "https://getklai.com/docs/legal/privacy",
            }
        ],
        citation_output="markers",
    ):
        chunks.append(chunk)

    body = b"".join(chunks).decode()
    assert '"content": "Naam."' in body
    assert (
        '"sources": [{"label": "1", "title": "Privacy policy", "url": "https://getklai.com/docs/legal/privacy"}]'
    ) in body
    assert "4(https://getklai.com/docs/legal/privacy)" not in body
    assert "[DONE]" in body


@pytest.mark.asyncio
async def test_streaming_widget_mode_composes_sources_without_model_citations(monkeypatch):
    """Production regression: a plain widget answer must still receive clickable sources."""
    from app.services.partner_chat import chat_completion_streaming

    events = [
        {"choices": [{"delta": {"content": "Klai is steward-owned and mission-led."}}]},
    ]

    class _StreamResp:
        def raise_for_status(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def aiter_lines(self):
            for event in events:
                yield "data: " + json.dumps(event)
            yield "data: [DONE]"

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        def stream(self, *_, **__):
            return _StreamResp()

    monkeypatch.setattr("app.services.partner_chat.httpx.AsyncClient", lambda timeout: _Client())

    settings = MagicMock()
    settings.litellm_base_url = "http://litellm"
    settings.litellm_master_key = "secret"

    chunks = []
    async for chunk in chat_completion_streaming(
        messages=[{"role": "user", "content": "Wat is Klai?"}],
        model="klai-primary",
        temperature=0.7,
        system_prompt="prompt",
        settings=settings,
        citation_chunks=[
            {
                "title": "Steward ownership",
                "source_url": "https://www.getklai.com/docs/company/steward-ownership",
                "text": "Klai is steward-owned and mission-led.",
            }
        ],
        trusted_sources=[
            {
                "label": "1",
                "title": "Steward ownership",
                "url": "https://getklai.com/docs/company/steward-ownership",
            }
        ],
        citation_output="markers",
    ):
        chunks.append(chunk)

    body = b"".join(chunks).decode()
    assert '"sources": [{"label": "1", "title": "Steward ownership"' in body
    assert '"content": "Klai is steward-owned and mission-led."' in body
    assert body.index('"sources"') < body.index('"content"')


@pytest.mark.asyncio
async def test_streaming_widget_mode_refuses_uncited_answer_without_sources(monkeypatch):
    """Widget mode should not hallucinate when retrieval yields no citable source URL."""
    from app.services.partner_chat import chat_completion_streaming

    events = [{"choices": [{"delta": {"content": "Klai is a legal search engine."}}]}]

    class _StreamResp:
        def raise_for_status(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def aiter_lines(self):
            for event in events:
                yield "data: " + json.dumps(event)
            yield "data: [DONE]"

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        def stream(self, *_, **__):
            return _StreamResp()

    monkeypatch.setattr("app.services.partner_chat.httpx.AsyncClient", lambda timeout: _Client())

    settings = MagicMock()
    settings.litellm_base_url = "http://litellm"
    settings.litellm_master_key = "secret"

    chunks = []
    async for chunk in chat_completion_streaming(
        messages=[{"role": "user", "content": "Wat is Klai?"}],
        model="klai-primary",
        temperature=0.7,
        system_prompt="prompt",
        settings=settings,
        citation_chunks=[{"title": "Untitled", "text": "Klai information without URL."}],
        citation_output="markers",
    ):
        chunks.append(chunk)

    body = b"".join(chunks).decode()
    assert "juridisch" not in body
    assert "legal search" not in body
    assert "beschikbare kennisbronnen" in body
    assert '"sources"' not in body


def test_stream_sanitizer_removes_split_parenthesized_raw_urls_without_placeholder():
    """Split parenthesized raw URLs should be removed silently, not rendered."""
    from app.services.partner_chat import _pop_sanitized_stream_text

    allowed = {"https://getklai.com/"}
    out1, pending, changed1 = _pop_sanitized_stream_text(
        "Klai is open source [1] (https://github.com/get",
        allowed_source_urls=allowed,
        final=False,
    )
    out2, pending, changed2 = _pop_sanitized_stream_text(
        pending + "klai/klai).",
        allowed_source_urls=allowed,
        final=True,
    )

    body = out1 + out2
    assert pending == ""
    assert changed1 + changed2 == 1
    assert "https://github.com/getklai/klai" not in body
    assert "link removed" not in body
    assert "()" not in body
    assert body == "Klai is open source [1]."


def test_language_correctness_log_does_not_duplicate_structlog_event(monkeypatch):
    """Regression guard for BoundLogger.info(event, event=...) TypeError."""
    from app.services import partner_chat

    logger = MagicMock()
    monkeypatch.setattr(partner_chat, "logger", logger)
    monkeypatch.setattr(partner_chat, "detect_language", MagicMock(return_value="nl"))
    monkeypatch.setattr(partner_chat, "language_correctness", MagicMock(return_value=True))

    partner_chat._emit_language_correctness_log(
        org_id=1,
        query="Wat verzamelt Klai?",
        response_text="Klai verzamelt accountgegevens.",
    )

    logger.info.assert_called_once()
    args, kwargs = logger.info.call_args
    assert args == ("chat_synthesis_complete",)
    assert "event" not in kwargs
    assert kwargs["org_id"] == 1


@pytest.mark.asyncio
async def test_retrieve_context_sends_caller_service_header(monkeypatch):
    """retrieve_context MUST send X-Caller-Service: portal-api on /retrieve.

    Phase D of SPEC-SEC-IDENTITY-ASSERT-001 (landed 2026-04-28) made this
    header mandatory. Without it retrieval-api returns 400
    `missing_caller_service` and partner chat returns empty context for
    every customer call. The hook silently degraded for 7 days. This test
    locks the header in. See pitfalls →
    retrieve-caller-service-header-mismatch.
    """
    from app.services.partner_chat import retrieve_context

    captured: dict = {}

    class _MockResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"chunks": []}

    class _MockClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["headers"] = headers or {}
            return _MockResp()

    monkeypatch.setattr("app.services.partner_chat.httpx.AsyncClient", lambda timeout: _MockClient())

    fake_settings = MagicMock()
    fake_settings.knowledge_retrieve_url = "http://retrieval-api:8040"
    fake_settings.retrieval_api_internal_secret = "test-retrieval-secret"
    fake_settings.internal_secret = "test-portal-secret"

    await retrieve_context(
        org_id=42,
        zitadel_org_id="z-1",
        kb_slugs=["kb-alpha"],
        messages=[{"role": "user", "content": "hello"}],
        settings=fake_settings,
    )

    assert captured["headers"].get("X-Caller-Service") == "portal-api", (
        "X-Caller-Service header missing — retrieval-api 400s and partner chat returns no KB context. See pitfalls."
    )
    assert captured["headers"].get("X-Internal-Secret") == "test-retrieval-secret"


# ---------------------------------------------------------------------------
# F2 (audit retrieval-coupling-2026-05-06): synthetic partner user_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieve_context_passes_partner_user_id(monkeypatch):
    """When `partner_user_id` is given, /retrieve body carries `user_id`.

    Without this, retrieval-api's verify_body_identity returns early without
    pinning verified_caller, so emit_event drops the knowledge.queried event
    via the product_event_skipped_no_identity warning branch.
    """
    from app.services.partner_chat import retrieve_context

    captured: dict = {}

    class _MockResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"chunks": []}

    class _MockClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, url, json=None, headers=None):
            captured["body"] = json
            return _MockResp()

    monkeypatch.setattr(
        "app.services.partner_chat.httpx.AsyncClient",
        lambda timeout: _MockClient(),
    )

    fake_settings = MagicMock()
    fake_settings.knowledge_retrieve_url = "http://retrieval-api:8040"
    fake_settings.retrieval_api_internal_secret = "secret"
    fake_settings.internal_secret = "fallback"

    await retrieve_context(
        org_id=42,
        zitadel_org_id="z-1",
        kb_slugs=[],
        messages=[{"role": "user", "content": "hello"}],
        settings=fake_settings,
        partner_user_id="partner:key-abc-123",
    )

    assert captured["body"]["user_id"] == "partner:key-abc-123", (
        "partner_user_id MUST flow through to retrieve body or knowledge.queried events drop. F2 audit ref."
    )


@pytest.mark.asyncio
async def test_retrieve_context_omits_user_id_when_partner_user_id_none(monkeypatch):
    """Backwards-compat: existing callers without partner_user_id parameter
    MUST NOT have a user_id field in the body. Existing /retrieve behaviour
    relies on this for non-partner internal callers."""
    from app.services.partner_chat import retrieve_context

    captured: dict = {}

    class _MockResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"chunks": []}

    class _MockClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, url, json=None, headers=None):
            captured["body"] = json
            return _MockResp()

    monkeypatch.setattr(
        "app.services.partner_chat.httpx.AsyncClient",
        lambda timeout: _MockClient(),
    )

    fake_settings = MagicMock()
    fake_settings.knowledge_retrieve_url = "http://retrieval-api:8040"
    fake_settings.retrieval_api_internal_secret = "s"
    fake_settings.internal_secret = "s"

    await retrieve_context(
        org_id=42,
        zitadel_org_id="z-1",
        kb_slugs=[],
        messages=[{"role": "user", "content": "hello"}],
        settings=fake_settings,
        # Note: no partner_user_id
    )

    assert "user_id" not in captured["body"], (
        f"user_id leaked into body without explicit partner_user_id: {captured['body']}"
    )
