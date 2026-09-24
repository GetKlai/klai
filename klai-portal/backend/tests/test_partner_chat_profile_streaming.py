"""Slice 2 of the one-chat-pipeline plan (docs/architecture/chat-quality-history-and-plan.md §7):
tools/tool_choice forwarding, tool_calls passthrough, and profile-driven
streaming (hold + keepalive vs. live) in the knowledge path.

docs/.context/step0-architecture.md §4 row 2 is the contract for these tests.
"""

import json
from unittest.mock import MagicMock

import pytest

from app.services.chat_profile import ChatProfile
from app.services.partner_chat import (
    _build_conversation_history,
    _last_user_message,
    _strip_web_search_tools,
    chat_completion_streaming,
)


def _sse_content_frames(body: str) -> list[dict]:
    frames = []
    for line in body.splitlines():
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        frames.append(json.loads(line[6:])["choices"][0]["delta"])
    return frames


class _StreamResp:
    def __init__(self, events):
        self._events = events

    def raise_for_status(self):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def aiter_lines(self):
        for event in self._events:
            yield "data: " + json.dumps(event)
        yield "data: [DONE]"


class _RecordingClient:
    """Captures the JSON body posted to LiteLLM, same double as test_partner_chat.py."""

    last_json: dict | None = None

    def __init__(self, events):
        self._events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    def stream(self, method, url, *, json, headers):
        type(self).last_json = json
        return _StreamResp(self._events)


def _settings() -> MagicMock:
    settings = MagicMock()
    settings.litellm_base_url = "http://litellm"
    settings.litellm_master_key = "secret"
    return settings


# ---------------------------------------------------------------------------
# Tool-role history must not pollute the retrieval query.
# ---------------------------------------------------------------------------


def test_tool_role_history_message_does_not_change_retrieval_query():
    messages = [
        {"role": "user", "content": "Wat kost een SIP-trunk?"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function"}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "search results the visitor never said"},
        {"role": "user", "content": "En hoeveel kanalen krijg ik daarvoor?"},
    ]

    assert _last_user_message(messages) == "En hoeveel kanalen krijg ik daarvoor?"
    history = _build_conversation_history(messages)
    assert all(entry["role"] != "tool" for entry in history)
    assert not any("search results the visitor never said" in str(entry.get("content", "")) for entry in history)


# ---------------------------------------------------------------------------
# tool_calls deltas: unbuffered, never in the composed text or grounding input.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_call_delta_reaches_client_before_stream_ends(monkeypatch):
    events = [
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "lookup"}}]}}]},
        {"choices": [{"delta": {"content": "Klai ondersteunt SIP-trunking."}}]},
    ]
    monkeypatch.setattr("app.services.partner_chat.httpx.AsyncClient", lambda timeout: _RecordingClient(events))

    chunks = []
    async for chunk in chat_completion_streaming(
        messages=[{"role": "user", "content": "Ondersteunt Klai SIP-trunking?"}],
        model="klai-primary",
        temperature=0.7,
        system_prompt="prompt",
        settings=_settings(),
        citation_output="markers",
        tools=[{"type": "function", "function": {"name": "lookup"}}],
    ):
        chunks.append(chunk)

    frames = _sse_content_frames(b"".join(chunks).decode())
    tool_call_index = next(i for i, f in enumerate(frames) if "tool_calls" in f)
    assert tool_call_index < len(frames) - 1, "tool_calls delta must arrive before the final frame"
    assert frames[tool_call_index]["tool_calls"][0]["function"]["name"] == "lookup"


@pytest.mark.asyncio
async def test_tool_call_content_never_enters_composed_text_or_grounding_input(monkeypatch):
    events = [
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "function": {"name": "lookup", "arguments": "SECRET_TOOL_ARGUMENT"}}
                        ]
                    }
                }
            ]
        },
        {"choices": [{"delta": {"content": "Klai ondersteunt SIP-trunking."}}]},
    ]
    monkeypatch.setattr("app.services.partner_chat.httpx.AsyncClient", lambda timeout: _RecordingClient(events))

    chunks = []
    async for chunk in chat_completion_streaming(
        messages=[{"role": "user", "content": "Ondersteunt Klai SIP-trunking?"}],
        model="klai-primary",
        temperature=0.7,
        system_prompt="prompt",
        settings=_settings(),
        citation_output="markers",
        tools=[{"type": "function", "function": {"name": "lookup"}}],
    ):
        chunks.append(chunk)

    frames = _sse_content_frames(b"".join(chunks).decode())
    composed_text = "".join(f.get("content", "") for f in frames if "content" in f)
    assert "SECRET_TOOL_ARGUMENT" not in composed_text
    # It DOES reach the client — as its own unbuffered tool_calls delta, not text.
    assert any("tool_calls" in f for f in frames)


# ---------------------------------------------------------------------------
# Strict profile strips web-search tools before forwarding.
# ---------------------------------------------------------------------------


def test_strip_web_search_tools_removes_only_web_search():
    tools = [
        {"type": "function", "function": {"name": "web_search", "description": "Search the live web"}},
        {"type": "function", "function": {"name": "lookup_kb"}},
    ]
    kept = _strip_web_search_tools(tools)
    assert kept is not None
    assert [t["function"]["name"] for t in kept] == ["lookup_kb"]


@pytest.mark.asyncio
async def test_strict_profile_strips_web_search_tool_before_forwarding(monkeypatch):
    events = [{"choices": [{"delta": {"content": "Antwoord."}}]}]
    monkeypatch.setattr("app.services.partner_chat.httpx.AsyncClient", lambda timeout: _RecordingClient(events))

    async for _ in chat_completion_streaming(
        messages=[{"role": "user", "content": "Vraag"}],
        model="klai-primary",
        temperature=0.7,
        system_prompt="prompt",
        settings=_settings(),
        citation_output="markers",
        profile=ChatProfile(surface="internal", kb_mode="strict"),
        tools=[{"type": "function", "function": {"name": "web_search"}}],
    ):
        pass

    sent = _RecordingClient.last_json
    assert sent is not None
    assert "tools" not in sent


# ---------------------------------------------------------------------------
# Open internal (stream_live) vs. held turns.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_internal_profile_yields_multiple_content_frames(monkeypatch):
    events = [
        {"choices": [{"delta": {"content": "Klai "}}]},
        {"choices": [{"delta": {"content": "ondersteunt SIP-trunking "}}]},
        {"choices": [{"delta": {"content": "voor elk plan."}}]},
    ]
    monkeypatch.setattr("app.services.partner_chat.httpx.AsyncClient", lambda timeout: _RecordingClient(events))

    chunks = []
    async for chunk in chat_completion_streaming(
        messages=[{"role": "user", "content": "Ondersteunt Klai SIP-trunking?"}],
        model="klai-primary",
        temperature=0.7,
        system_prompt="prompt",
        settings=_settings(),
        citation_output="markers",
        profile=ChatProfile(surface="internal", kb_mode="open", stream_live=True),
    ):
        chunks.append(chunk)

    frames = _sse_content_frames(b"".join(chunks).decode())
    content_frames = [f for f in frames if f.get("content")]
    assert len(content_frames) >= 2, "an Open internal turn must stream more than one content frame"


@pytest.mark.asyncio
async def test_held_turn_yields_keepalives_and_no_content_before_final(monkeypatch):
    events = [
        {"choices": [{"delta": {"content": "Klai "}}]},
        {"choices": [{"delta": {"content": "ondersteunt SIP-trunking."}}]},
    ]
    monkeypatch.setattr("app.services.partner_chat.httpx.AsyncClient", lambda timeout: _RecordingClient(events))

    chunks = []
    async for chunk in chat_completion_streaming(
        messages=[{"role": "user", "content": "Ondersteunt Klai SIP-trunking?"}],
        model="klai-primary",
        temperature=0.7,
        system_prompt="prompt",
        settings=_settings(),
        citation_output="markers",
        # Held (default stream_live=False), internal surface so keepalives fire.
        profile=ChatProfile(surface="internal", kb_mode="open"),
    ):
        chunks.append(chunk)

    frames = _sse_content_frames(b"".join(chunks).decode())
    # Only the content-bearing deltas: language/sources/activity frames are a
    # separate signal channel, not part of the "held vs. keepalive" contract.
    content_frames = [f for f in frames if "content" in f]
    assert content_frames, "expected at least the keepalive + final content frame"
    *before_final, final = content_frames
    assert before_final, "a held turn with a slow (multi-delta) upstream must emit keepalives"
    assert all(f["content"] == "" for f in before_final), "no non-empty content frame before the final one"
    assert final["content"], "the final frame carries the composed answer"


@pytest.mark.asyncio
async def test_widget_profile_gets_no_keepalives_frames_stay_unchanged(monkeypatch):
    """surface != "internal" must not gain any new frame: existing widget/partner
    streaming tests assert exact byte sequences, so keepalives are internal-only."""
    events = [
        {"choices": [{"delta": {"content": "Klai "}}]},
        {"choices": [{"delta": {"content": "ondersteunt SIP-trunking."}}]},
    ]
    monkeypatch.setattr("app.services.partner_chat.httpx.AsyncClient", lambda timeout: _RecordingClient(events))

    chunks = []
    async for chunk in chat_completion_streaming(
        messages=[{"role": "user", "content": "Ondersteunt Klai SIP-trunking?"}],
        model="klai-primary",
        temperature=0.7,
        system_prompt="prompt",
        settings=_settings(),
        citation_output="markers",
        profile=ChatProfile(surface="widget"),
    ):
        chunks.append(chunk)

    frames = _sse_content_frames(b"".join(chunks).decode())
    # Exactly one content frame: the composed answer, no keepalives at all.
    content_frames = [f for f in frames if "content" in f]
    assert len(content_frames) == 1


@pytest.mark.asyncio
async def test_live_turn_never_streams_a_model_written_link_and_never_repeats_the_answer(monkeypatch):
    events = [
        {"choices": [{"delta": {"content": "Vraag verlof aan via "}}]},
        {"choices": [{"delta": {"content": "https://evil.example.com/verlof "}}]},
        {"choices": [{"delta": {"content": "bij je leidinggevende."}}]},
    ]
    monkeypatch.setattr("app.services.partner_chat.httpx.AsyncClient", lambda timeout: _RecordingClient(events))

    chunks = []
    async for chunk in chat_completion_streaming(
        messages=[{"role": "user", "content": "Hoe vraag ik verlof aan?"}],
        model="klai-primary",
        temperature=0.7,
        system_prompt="prompt",
        settings=_settings(),
        citation_output="markers",
        profile=ChatProfile(surface="internal", kb_mode="open", stream_live=True),
    ):
        chunks.append(chunk)

    streamed = "".join(f.get("content") or "" for f in _sse_content_frames(b"".join(chunks).decode()))
    assert "example.com" not in streamed
    assert streamed.count("Vraag verlof aan via") == 1
    assert "bij je leidinggevende." in streamed


@pytest.mark.parametrize(("delegated_org_id", "expected_metadata"), [("zorg-a", True), (None, False)])
@pytest.mark.asyncio
async def test_internal_generation_call_carries_the_org_for_pii_masking(
    monkeypatch, delegated_org_id, expected_metadata
):
    events = [{"choices": [{"delta": {"content": "Antwoord."}}]}]
    monkeypatch.setattr("app.services.partner_chat.httpx.AsyncClient", lambda timeout: _RecordingClient(events))

    async for _ in chat_completion_streaming(
        messages=[{"role": "user", "content": "Wat is het telefoonnummer van Jan de Vries?"}],
        model="klai-primary",
        temperature=0.7,
        system_prompt="prompt",
        settings=_settings(),
        citation_output="markers",
        delegated_org_id=delegated_org_id,
    ):
        pass

    sent = _RecordingClient.last_json or {}
    assert (sent.get("metadata", {}).get("_klai_delegated_org_id") == "zorg-a") is expected_metadata


@pytest.mark.asyncio
async def test_delegated_passthrough_uses_the_master_key_so_litellm_honours_the_org(monkeypatch):
    from app.services.partner_chat import openai_chat_completion_non_streaming

    sent: dict = {}

    class _Client:
        def __init__(self, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, url, *, json, headers):
            sent.update(json=json, headers=headers)
            response = MagicMock(status_code=200)
            response.json.return_value = {"choices": [{"message": {"content": "Titel"}}]}
            return response

    monkeypatch.setattr("app.services.partner_chat.httpx.AsyncClient", _Client)
    settings = _settings()
    settings.litellm_general_chat_key = "general"

    await openai_chat_completion_non_streaming(
        {"model": "klai-primary", "messages": [{"role": "user", "content": "Geef dit gesprek een titel"}]},
        settings,
        org_id=1,
        delegated_org_id="zorg-a",
    )

    assert sent["headers"]["Authorization"] == "Bearer secret"
    assert sent["json"]["metadata"]["_klai_delegated_org_id"] == "zorg-a"


def test_a_delegated_title_passthrough_keeps_its_passthrough_marker():
    from app.services.partner_chat import _with_openai_passthrough_metadata

    body = _with_openai_passthrough_metadata({"model": "klai-primary", "messages": []}, delegated_org_id="zorg-a")

    assert body["metadata"] == {"_klai_openai_passthrough": True, "_klai_delegated_org_id": "zorg-a"}


def test_widget_history_tool_messages_never_reach_the_model():
    from app.services.partner_chat import _augment_messages_with_system_prompt

    messages = [
        {"role": "user", "content": "Hoi"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "visitor-supplied tool result"},
        {"role": "user", "content": "En nu?"},
    ]

    sent = _augment_messages_with_system_prompt(
        messages, "prompt", None, response_language="nl", profile=ChatProfile(surface="widget")
    )

    assert all(m["role"] != "tool" and "tool_calls" not in m for m in sent)


@pytest.mark.asyncio
async def test_partner_keeps_its_web_search_tool(monkeypatch):
    events = [{"choices": [{"delta": {"content": "Antwoord."}}]}]
    monkeypatch.setattr("app.services.partner_chat.httpx.AsyncClient", lambda timeout: _RecordingClient(events))
    web_tool = {"type": "function", "function": {"name": "web_search"}}

    async for _ in chat_completion_streaming(
        messages=[{"role": "user", "content": "Wat is het weer?"}],
        model="klai-primary",
        temperature=0.7,
        system_prompt="prompt",
        settings=_settings(),
        citation_output="markers",
        profile=ChatProfile(surface="partner"),
        tools=[web_tool],
    ):
        pass

    assert (_RecordingClient.last_json or {}).get("tools") == [web_tool]
