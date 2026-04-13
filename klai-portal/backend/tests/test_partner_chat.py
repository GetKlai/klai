"""RED: Verify POST /partner/v1/chat/completions.

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

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException


@dataclass
class FakeKB:
    """Mimics a PortalKnowledgeBase row."""

    id: int
    name: str
    slug: str
    org_id: int


def _make_auth(permissions: dict | None = None, kb_access: dict | None = None):
    """Create a PartnerAuthContext for testing."""
    from app.api.partner_dependencies import PartnerAuthContext

    return PartnerAuthContext(
        key_id="key-uuid-1",
        org_id=42,
        zitadel_org_id="zit-org-42",
        permissions=permissions or {"chat": True, "feedback": True, "knowledge_append": False},
        kb_access=kb_access or {10: "read", 20: "read_write"},
        rate_limit_rpm=60,
    )


def _mock_scalars_all(values):
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = values
    result.scalars.return_value = scalars
    return result


# ---------------------------------------------------------------------------
# TASK-008: Non-streaming chat completions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_model_returns_400():
    """Model must be klai-primary or klai-fast; anything else -> 400."""
    from app.api.partner import ChatCompletionsRequest, chat_completions

    auth = _make_auth()
    db = AsyncMock()

    req = ChatCompletionsRequest(
        messages=[{"role": "user", "content": "Hello"}],
        model="gpt-4",
        stream=False,
    )

    with pytest.raises(HTTPException) as exc_info:
        await chat_completions(request=req, auth=auth, db=db)
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

    auth = _make_auth()
    db = AsyncMock()

    req = ChatCompletionsRequest(
        messages=[{"role": "system", "content": "You are helpful"}],
        model="klai-primary",
        stream=False,
    )

    with pytest.raises(HTTPException) as exc_info:
        await chat_completions(request=req, auth=auth, db=db)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_kb_out_of_scope_returns_403():
    """Requesting a KB not in key scope -> 403."""
    from app.api.partner import ChatCompletionsRequest, chat_completions

    auth = _make_auth(kb_access={10: "read"})
    db = AsyncMock()

    req = ChatCompletionsRequest(
        messages=[{"role": "user", "content": "Hello"}],
        model="klai-primary",
        stream=False,
        knowledge_base_ids=[99],  # not in scope
    )

    with pytest.raises(HTTPException) as exc_info:
        await chat_completions(request=req, auth=auth, db=db)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_retrieval_timeout_returns_502():
    """Retrieval-api timeout -> 502 Bad Gateway."""
    from app.api.partner import ChatCompletionsRequest, chat_completions

    auth = _make_auth(kb_access={10: "read"})
    fake_kbs = [FakeKB(id=10, name="KB Alpha", slug="kb-alpha", org_id=42)]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_mock_scalars_all(fake_kbs))

    req = ChatCompletionsRequest(
        messages=[{"role": "user", "content": "Hello"}],
        model="klai-primary",
        stream=False,
    )

    with (
        patch(
            "app.api.partner.retrieve_context",
            side_effect=httpx.ReadTimeout("timeout"),
        ),
        pytest.raises(HTTPException) as exc_info,
    ):
        await chat_completions(request=req, auth=auth, db=db)
    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_happy_path_non_streaming():
    """Non-streaming: returns OpenAI-shaped JSON with choices."""
    from app.api.partner import ChatCompletionsRequest, chat_completions

    auth = _make_auth(kb_access={10: "read"})
    fake_kbs = [FakeKB(id=10, name="KB Alpha", slug="kb-alpha", org_id=42)]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_mock_scalars_all(fake_kbs))

    req = ChatCompletionsRequest(
        messages=[{"role": "user", "content": "Hello"}],
        model="klai-primary",
        stream=False,
    )

    mock_chunks = [{"chunk_id": "c1", "text": "context"}]
    mock_system_prompt = "You are a helpful assistant.\n\nContext:\ncontext"

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
        patch(
            "app.api.partner.retrieve_context",
            return_value=(mock_chunks, mock_system_prompt),
        ),
        patch(
            "app.api.partner.chat_completion_non_streaming",
            return_value=litellm_response,
        ),
        patch("app.api.partner.asyncio"),
    ):
        result = await chat_completions(request=req, auth=auth, db=db)

    assert result["id"] == "chatcmpl-123"
    assert result["choices"][0]["message"]["content"] == "Hi there!"


@pytest.mark.asyncio
async def test_retrieval_log_scheduled():
    """Retrieval log is scheduled as fire-and-forget asyncio.create_task."""
    from app.api.partner import ChatCompletionsRequest, chat_completions

    auth = _make_auth(kb_access={10: "read"})
    fake_kbs = [FakeKB(id=10, name="KB Alpha", slug="kb-alpha", org_id=42)]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_mock_scalars_all(fake_kbs))

    req = ChatCompletionsRequest(
        messages=[{"role": "user", "content": "Hello"}],
        model="klai-primary",
        stream=False,
    )

    mock_chunks = [{"chunk_id": "c1", "text": "context", "reranker_score": 0.9}]
    mock_system_prompt = "system prompt"

    litellm_response = {
        "id": "chatcmpl-123",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hi"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }

    with (
        patch(
            "app.api.partner.retrieve_context",
            return_value=(mock_chunks, mock_system_prompt),
        ),
        patch(
            "app.api.partner.chat_completion_non_streaming",
            return_value=litellm_response,
        ),
        patch("app.api.partner.asyncio") as mock_asyncio,
    ):
        await chat_completions(request=req, auth=auth, db=db)

    # create_task was called to schedule the retrieval log
    mock_asyncio.create_task.assert_called_once()


@pytest.mark.asyncio
async def test_kb_id_to_slug_translation():
    """kb_ids are translated to kb_slugs via DB lookup before retrieval."""
    from app.api.partner import ChatCompletionsRequest, chat_completions

    auth = _make_auth(kb_access={10: "read", 20: "read_write"})
    fake_kbs = [
        FakeKB(id=10, name="KB Alpha", slug="kb-alpha", org_id=42),
        FakeKB(id=20, name="KB Beta", slug="kb-beta", org_id=42),
    ]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_mock_scalars_all(fake_kbs))

    req = ChatCompletionsRequest(
        messages=[{"role": "user", "content": "Hello"}],
        model="klai-primary",
        stream=False,
        knowledge_base_ids=[10, 20],
    )

    mock_chunks = []
    mock_system_prompt = "system prompt"

    litellm_response = {
        "id": "chatcmpl-123",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hi"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }

    with (
        patch(
            "app.api.partner.retrieve_context",
            return_value=(mock_chunks, mock_system_prompt),
        ) as mock_retrieve,
        patch(
            "app.api.partner.chat_completion_non_streaming",
            return_value=litellm_response,
        ),
        patch("app.api.partner.asyncio"),
    ):
        await chat_completions(request=req, auth=auth, db=db)

    # Verify retrieve_context was called with slugs, not IDs
    call_kwargs = mock_retrieve.call_args
    kb_slugs_arg = call_kwargs[1].get("kb_slugs") or call_kwargs[0][2]
    assert set(kb_slugs_arg) == {"kb-alpha", "kb-beta"}


# ---------------------------------------------------------------------------
# TASK-009: Streaming chat completions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_returns_event_stream_content_type():
    """Streaming response has content-type text/event-stream."""
    from app.api.partner import ChatCompletionsRequest, chat_completions

    auth = _make_auth(kb_access={10: "read"})
    fake_kbs = [FakeKB(id=10, name="KB Alpha", slug="kb-alpha", org_id=42)]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_mock_scalars_all(fake_kbs))

    req = ChatCompletionsRequest(
        messages=[{"role": "user", "content": "Hello"}],
        model="klai-primary",
        stream=True,
    )

    mock_chunks = [{"chunk_id": "c1", "text": "context"}]
    mock_system_prompt = "system prompt"

    async def mock_streaming_gen():
        yield b"data: {}\n\n"
        yield b"data: [DONE]\n\n"

    with (
        patch(
            "app.api.partner.retrieve_context",
            return_value=(mock_chunks, mock_system_prompt),
        ),
        patch(
            "app.api.partner.chat_completion_streaming",
            return_value=mock_streaming_gen(),
        ),
        patch("app.api.partner.asyncio"),
    ):
        from starlette.responses import StreamingResponse

        result = await chat_completions(request=req, auth=auth, db=db)
        assert isinstance(result, StreamingResponse)
        assert result.media_type == "text/event-stream"


@pytest.mark.asyncio
async def test_streaming_chunks_forwarded():
    """Mock LiteLLM streaming chunks are forwarded byte-for-byte."""
    from app.api.partner import ChatCompletionsRequest, chat_completions

    auth = _make_auth(kb_access={10: "read"})
    fake_kbs = [FakeKB(id=10, name="KB Alpha", slug="kb-alpha", org_id=42)]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_mock_scalars_all(fake_kbs))

    req = ChatCompletionsRequest(
        messages=[{"role": "user", "content": "Hello"}],
        model="klai-primary",
        stream=True,
    )

    mock_chunks_data = [{"chunk_id": "c1", "text": "context"}]
    mock_system_prompt = "system prompt"

    expected_bytes = [
        b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n',
        b"data: [DONE]\n\n",
    ]

    async def mock_streaming_gen():
        for chunk in expected_bytes:
            yield chunk

    with (
        patch(
            "app.api.partner.retrieve_context",
            return_value=(mock_chunks_data, mock_system_prompt),
        ),
        patch(
            "app.api.partner.chat_completion_streaming",
            return_value=mock_streaming_gen(),
        ),
        patch("app.api.partner.asyncio"),
    ):
        result = await chat_completions(request=req, auth=auth, db=db)

        # Collect streamed bytes
        received = []
        async for chunk in result.body_iterator:
            received.append(chunk)

        assert len(received) == 2
        assert b"[DONE]" in received[-1]


@pytest.mark.asyncio
async def test_streaming_done_terminator():
    """[DONE] terminator is present in streaming output."""
    from app.api.partner import ChatCompletionsRequest, chat_completions

    auth = _make_auth(kb_access={10: "read"})
    fake_kbs = [FakeKB(id=10, name="KB Alpha", slug="kb-alpha", org_id=42)]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_mock_scalars_all(fake_kbs))

    req = ChatCompletionsRequest(
        messages=[{"role": "user", "content": "Hello"}],
        model="klai-primary",
        stream=True,
    )

    async def mock_streaming_gen():
        yield b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n'
        yield b"data: [DONE]\n\n"

    with (
        patch(
            "app.api.partner.retrieve_context",
            return_value=([], "prompt"),
        ),
        patch(
            "app.api.partner.chat_completion_streaming",
            return_value=mock_streaming_gen(),
        ),
        patch("app.api.partner.asyncio"),
    ):
        result = await chat_completions(request=req, auth=auth, db=db)

        all_bytes = b""
        async for chunk in result.body_iterator:
            all_bytes += chunk

        assert b"[DONE]" in all_bytes


@pytest.mark.asyncio
async def test_streaming_retrieval_log_fires():
    """Retrieval log fires even on streaming path."""
    from app.api.partner import ChatCompletionsRequest, chat_completions

    auth = _make_auth(kb_access={10: "read"})
    fake_kbs = [FakeKB(id=10, name="KB Alpha", slug="kb-alpha", org_id=42)]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_mock_scalars_all(fake_kbs))

    req = ChatCompletionsRequest(
        messages=[{"role": "user", "content": "Hello"}],
        model="klai-primary",
        stream=True,
    )

    async def mock_streaming_gen():
        yield b"data: [DONE]\n\n"

    with (
        patch(
            "app.api.partner.retrieve_context",
            return_value=([{"chunk_id": "c1"}], "prompt"),
        ),
        patch(
            "app.api.partner.chat_completion_streaming",
            return_value=mock_streaming_gen(),
        ),
        patch("app.api.partner.asyncio") as mock_asyncio,
    ):
        await chat_completions(request=req, auth=auth, db=db)

    mock_asyncio.create_task.assert_called_once()
