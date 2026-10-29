"""Slice 2 of the one-chat-pipeline plan (docs/architecture/chat-quality-history-and-plan.md §7):
tools/tool_choice forwarding, tool_calls passthrough, and profile-driven
streaming (held vs. live) in the knowledge path. Since step O4 the internal
Strict turn is held and written by widget_write.write_reply, so the empty
keepalive frames an internal held stream sent are gone, and their test with them.

docs/.context/step0-architecture.md §4 row 2 is the contract for these tests.
"""

import json
from unittest.mock import MagicMock

import pytest
from helpers import streamed_reply_frames

from app.services.chat_profile import ChatProfile
from app.services.chat_prompting import _build_conversation_history, _strip_web_search_tools
from app.services.chat_safety import _last_user_message


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
    monkeypatch.setattr("app.services.widget_write.httpx.AsyncClient", lambda timeout: _RecordingClient(events))

    chunks = await streamed_reply_frames(
        messages=[{"role": "user", "content": "Ondersteunt Klai SIP-trunking?"}],
        model="klai-primary",
        temperature=0.7,
        system_prompt="prompt",
        settings=_settings(),
        tools=[{"type": "function", "function": {"name": "lookup"}}],
        profile=ChatProfile(surface="internal", kb_mode="open"),
    )

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
    monkeypatch.setattr("app.services.widget_write.httpx.AsyncClient", lambda timeout: _RecordingClient(events))

    chunks = await streamed_reply_frames(
        messages=[{"role": "user", "content": "Ondersteunt Klai SIP-trunking?"}],
        model="klai-primary",
        temperature=0.7,
        system_prompt="prompt",
        settings=_settings(),
        tools=[{"type": "function", "function": {"name": "lookup"}}],
        profile=ChatProfile(surface="internal", kb_mode="open"),
    )

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


class _PostingClient:
    """Captures every JSON body posted to LiteLLM and answers each with the same text."""

    def __init__(self, bodies: list[dict]):
        self.bodies = bodies

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def post(self, url, *, json, headers):
        self.bodies.append(json)
        response = MagicMock()
        response.json.return_value = {"choices": [{"message": {"content": "Antwoord."}}]}
        return response


@pytest.mark.asyncio
async def test_strict_profile_strips_web_search_tool_before_forwarding(monkeypatch):
    """The internal Strict turn is written by write_reply since step O4; Strict never searches the web."""
    from app.services.widget_write import write_reply

    bodies: list[dict] = []
    monkeypatch.setattr("app.services.widget_write.httpx.AsyncClient", lambda timeout: _PostingClient(bodies))

    await write_reply(
        messages=[{"role": "user", "content": "Vraag"}],
        model="klai-primary",
        temperature=0.7,
        system_prompt="prompt",
        settings=_settings(),
        profile=ChatProfile(surface="internal", kb_mode="strict"),
        tools=[
            {"type": "function", "function": {"name": "web_search"}},
            {"type": "function", "function": {"name": "lookup_kb"}},
        ],
    )

    (answer,) = [body for body in bodies if "stream" in body]
    assert [tool["function"]["name"] for tool in answer["tools"]] == ["lookup_kb"]


# ---------------------------------------------------------------------------
# Open internal turns stream live.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_internal_profile_yields_multiple_content_frames(monkeypatch):
    # Long enough to pass the language gate (six words) with text to spare:
    # a streamed reply shows nothing until its language can be told.
    events = [
        {"choices": [{"delta": {"content": "Klai "}}]},
        {"choices": [{"delta": {"content": "ondersteunt SIP-trunking "}}]},
        {"choices": [{"delta": {"content": "voor elk plan, ook "}}]},
        {"choices": [{"delta": {"content": "voor de kleinste bedrijven en hun partners."}}]},
    ]
    monkeypatch.setattr("app.services.widget_write.httpx.AsyncClient", lambda timeout: _RecordingClient(events))

    chunks = await streamed_reply_frames(
        messages=[{"role": "user", "content": "Ondersteunt Klai SIP-trunking?"}],
        model="klai-primary",
        temperature=0.7,
        system_prompt="prompt",
        settings=_settings(),
        profile=ChatProfile(surface="internal", kb_mode="open"),
    )

    frames = _sse_content_frames(b"".join(chunks).decode())
    content_frames = [f for f in frames if f.get("content")]
    assert len(content_frames) >= 2, "an Open internal turn must stream more than one content frame"


@pytest.mark.asyncio
async def test_a_streamed_internal_reply_ends_with_the_questions_that_were_not_checked(monkeypatch):
    from app.services.retrieval_context import KnowledgeTurn

    events = [{"choices": [{"delta": {"content": "Klai ondersteunt SIP-trunking."}}]}]
    monkeypatch.setattr("app.services.widget_write.httpx.AsyncClient", lambda timeout: _RecordingClient(events))

    chunks = await streamed_reply_frames(
        messages=[
            {"role": "user", "content": "Ondersteunt Klai SIP-trunking en hoe vraag ik dat aan voor mijn bedrijf?"}
        ],
        model="klai-primary",
        temperature=0.7,
        system_prompt="prompt",
        settings=_settings(),
        profile=ChatProfile(surface="internal", kb_mode="open"),
        knowledge_turn=KnowledgeTurn(
            answer_mode="open", unchecked_questions=["Wat is het supportnummer?", "Hoe vraag ik een offerte aan?"]
        ),
    )

    streamed = "".join(f.get("content") or "" for f in _sse_content_frames(b"".join(chunks).decode()))
    notice = "Stel ze gerust in een nieuw bericht:\n- Wat is het supportnummer?\n- Hoe vraag ik een offerte aan?"
    assert streamed.startswith("Klai ondersteunt SIP-trunking.\n\nDeze vragen heb ik niet apart opgezocht")
    # The Open turn's footer follows the notice.
    assert notice in streamed
    assert streamed.index("Agent") > streamed.index(notice)


@pytest.mark.asyncio
async def test_live_turn_never_streams_a_model_written_link_and_never_repeats_the_answer(monkeypatch):
    events = [
        {"choices": [{"delta": {"content": "Vraag verlof aan via "}}]},
        {"choices": [{"delta": {"content": "https://evil.example.com/verlof "}}]},
        {"choices": [{"delta": {"content": "bij je leidinggevende."}}]},
    ]
    monkeypatch.setattr("app.services.widget_write.httpx.AsyncClient", lambda timeout: _RecordingClient(events))

    chunks = await streamed_reply_frames(
        messages=[{"role": "user", "content": "Hoe vraag ik verlof aan?"}],
        model="klai-primary",
        temperature=0.7,
        system_prompt="prompt",
        settings=_settings(),
        profile=ChatProfile(surface="internal", kb_mode="open"),
    )

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
    monkeypatch.setattr("app.services.widget_write.httpx.AsyncClient", lambda timeout: _RecordingClient(events))

    await streamed_reply_frames(
        messages=[{"role": "user", "content": "Wat is het telefoonnummer van Jan de Vries?"}],
        model="klai-primary",
        temperature=0.7,
        system_prompt="prompt",
        settings=_settings(),
        delegated_org_id=delegated_org_id,
        profile=ChatProfile(surface="internal", kb_mode="open"),
    )

    sent = _RecordingClient.last_json or {}
    assert (sent.get("metadata", {}).get("_klai_delegated_org_id") == "zorg-a") is expected_metadata


@pytest.mark.asyncio
async def test_delegated_passthrough_uses_the_master_key_so_litellm_honours_the_org(monkeypatch):
    from app.services.openai_passthrough import openai_chat_completion_non_streaming

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

    monkeypatch.setattr("app.services.openai_passthrough.httpx.AsyncClient", _Client)
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
    from app.services.openai_passthrough import _with_openai_passthrough_metadata

    body = _with_openai_passthrough_metadata({"model": "klai-primary", "messages": []}, delegated_org_id="zorg-a")

    assert body["metadata"] == {
        "_klai_openai_passthrough": True,
        "_klai_delegated_org_id": "zorg-a",
        "tags": ["portal:partner-openai-passthrough"],
    }


def test_widget_history_tool_messages_never_reach_the_model():
    from app.services.chat_prompting import _augment_messages_with_system_prompt

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
