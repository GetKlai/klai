"""The writer answers in the language the turn decided, whatever language the sources are in.

On 29 October 2026 the widget and the internal chat answered English questions
in Dutch under a "Respond in English" instruction: the model followed its
Dutch source lines, on 8 of 20 replayed turns, whatever the instruction's
wording, position or temperature. Made to write the decided language before
one word of the reply (the language-first output shape), it stayed in that
language on 20 of 20 turns. The shape is enforced only on a firm decision,
and the streamed reply shows the reply field as it is written.

synthetic-data: generator=hand-written seed=0 (fictional HR article, example.com URLs).
"""

from __future__ import annotations

import itertools
import json
from unittest.mock import MagicMock

import httpx
import pytest

from app.services.chat_profile import ChatProfile
from app.services.widget_reply import streamed_frames
from app.services.widget_write import _ReplyField, write_reply

DUTCH = "Je vraagt verlof aan in het HR-portaal onder Verlof, daar staat de knop Aanvragen."
ENGLISH = 'You request leave in the HR-portaal under "Verlof", where the Aanvragen button is.\nDone in a minute.'
SOURCE = {"label": "1", "title": "Verlof aanvragen", "url": "https://kb.example.com/verlof"}
CHUNK = {"title": "Verlof aanvragen", "source_url": "https://kb.example.com/verlof", "text": DUTCH}


class _Model:
    """LiteLLM: answers the language-first shape when asked for it, plain text otherwise."""

    def __init__(self, reply: str, *, split: tuple[int, ...] = (7, 29)) -> None:
        self.reply, self.split = reply, split
        self.requests: list[dict] = []

    def _text(self, body: dict) -> str:
        if "response_format" in body:
            language = body["response_format"]["json_schema"]["schema"]["properties"]["reply_language"]["enum"][0]
            return json.dumps({"reply_language": language, "reply": self.reply})
        return self.reply

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        text = self._text(body)
        if body.get("stream"):
            cuts = (0, *self.split, len(text))
            frames = "".join(
                f"data: {json.dumps({'choices': [{'delta': {'content': text[a:b]}}]})}\n\n"
                for a, b in itertools.pairwise(cuts)
                if text[a:b]
            )
            return httpx.Response(200, content=f"{frames}data: [DONE]\n\n".encode())
        return httpx.Response(200, json={"choices": [{"message": {"content": text}, "finish_reason": "stop"}]})


@pytest.fixture
def _settings():
    return MagicMock(litellm_base_url="http://litellm.example.com", litellm_master_key="key")


def _patch(monkeypatch, model: _Model) -> None:
    original = httpx.AsyncClient
    monkeypatch.setattr(
        "app.services.widget_write.httpx.AsyncClient",
        lambda timeout: original(transport=httpx.MockTransport(model)),
    )


def _contents(frames: list[bytes]) -> list[str]:
    out = []
    for frame in frames:
        payload = frame.decode().removeprefix("data: ").strip()
        if payload == "[DONE]":
            continue
        for choice in json.loads(payload).get("choices") or []:
            if "content" in choice["delta"]:
                out.append(choice["delta"]["content"])
    return out


async def _write(settings, model_messages: list[dict], *, stream: bool = False, profile: ChatProfile, **kwargs):
    return await write_reply(
        messages=model_messages,
        model="klai-primary",
        temperature=0.2,
        system_prompt="prompt",
        settings=settings,
        citation_chunks=[CHUNK],
        trusted_sources=[SOURCE],
        profile=profile,
        stream=stream,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_a_held_reply_is_asked_for_in_the_decided_language_first(monkeypatch, _settings):
    model = _Model(ENGLISH)
    _patch(monkeypatch, model)

    reply = await _write(
        _settings,
        [{"role": "user", "content": "How do I request leave for next week?"}],
        profile=ChatProfile(surface="widget", kb_mode="strict"),
    )

    body = model.requests[0]
    schema = body["response_format"]["json_schema"]
    assert schema["strict"] is True
    assert schema["schema"]["properties"]["reply_language"]["enum"] == ["en"]
    assert list(schema["schema"]["properties"]) == ["reply_language", "reply"]
    # The contrast is named: the passages are Dutch, the user expects English.
    reminder = body["messages"][-1]["content"]
    assert reminder.startswith("[FINAL RESPONSE LANGUAGE] Respond in English.")
    assert "written in Dutch" in reminder and "expects English" in reminder
    assert reply.text.startswith(ENGLISH)


@pytest.mark.asyncio
async def test_a_question_too_short_to_decide_on_keeps_the_free_text_reply(monkeypatch, _settings):
    """ "Ondersteunt Klai SIP-trunking?" is identified as German at full confidence; nothing is enforced."""
    model = _Model(DUTCH)
    _patch(monkeypatch, model)

    reply = await _write(
        _settings,
        [{"role": "user", "content": "Ondersteunt Klai SIP-trunking?"}],
        profile=ChatProfile(surface="widget", kb_mode="strict"),
    )

    assert "response_format" not in model.requests[0]
    assert reply.text.startswith(DUTCH)


@pytest.mark.asyncio
async def test_pasted_correspondence_is_never_pinned_to_a_language(monkeypatch, _settings):
    model = _Model(DUTCH)
    _patch(monkeypatch, model)

    await _write(
        _settings,
        [{"role": "user", "content": "Summarise this for me:\n\nDear team, the printer on floor two is broken again."}],
        profile=ChatProfile(surface="internal", kb_mode="open"),
        pasted_correspondence=True,
    )

    assert "response_format" not in model.requests[0]


@pytest.mark.asyncio
async def test_a_streamed_reply_shows_the_reply_field_as_it_is_written(monkeypatch, _settings):
    # The deltas cut through the shape's key, through an escaped quote and through a newline escape.
    model = _Model(ENGLISH, split=(12, 26, 64, 65, 100))
    _patch(monkeypatch, model)

    reply = await _write(
        _settings,
        [{"role": "user", "content": "How do I request leave for next week?"}],
        profile=ChatProfile(surface="internal", kb_mode="open"),
        stream=True,
    )
    frames = [frame async for frame in streamed_frames(reply)]

    contents = _contents(frames)
    shown = "".join(contents)
    assert shown.startswith(ENGLISH)
    assert "reply_language" not in shown and '{"' not in shown
    # The employee read the words before the model had finished.
    assert len(contents) > 2


@pytest.mark.asyncio
async def test_a_streamed_reply_with_tools_is_still_asked_for_in_the_decided_language(monkeypatch, _settings):
    """The internal chat forwards the employee's tools; the shape goes along, a tool call still comes as one."""
    model = _Model(ENGLISH)
    _patch(monkeypatch, model)

    reply = await _write(
        _settings,
        [{"role": "user", "content": "How do I request leave for next week?"}],
        profile=ChatProfile(surface="internal", kb_mode="open"),
        stream=True,
        tools=[{"type": "function", "function": {"name": "rooster", "parameters": {"type": "object"}}}],
    )
    frames = [frame async for frame in streamed_frames(reply)]

    assert "tools" in model.requests[0] and "response_format" in model.requests[0]
    assert "".join(_contents(frames)).startswith(ENGLISH)


def test_the_reply_field_decodes_escapes_split_across_deltas():
    field = _ReplyField()
    pieces = [
        '{"reply_language": "en", "re',
        'ply": "Go to \\"',
        'Verlof\\" and press \\u00e9',
        "n\\ud83d",
        '\\ude00 twice.\\n"}',
    ]
    out = "".join(field.feed(p) for p in pieces)
    assert out == 'Go to "Verlof" and press én\U0001f600 twice.\n'
    assert field.closed
    assert field.feed('{"junk": 1}') == ""


def test_a_held_body_is_decoded_like_a_streamed_one():
    from app.services.widget_write import _reply_field

    assert _reply_field('{"reply_language": "en", "reply": "Go to **Verlof**."}', org_id=None) == "Go to **Verlof**."
    # An empty reply is an empty draft, a body cut off by the token limit is readable up to the cut,
    # and a broken escape is shown rather than raised on.
    assert _reply_field('{"reply_language": "en", "reply": ""}', org_id=None) == ""
    assert _reply_field('{"reply_language": "en", "reply": "Step one, then", ', org_id=None) == "Step one, then"
    assert (
        _reply_field('{"reply_language": "en", "reply": "a\\uZZZZb \\ud83dxyz"}', org_id=None) == "a\\uZZZZb \ufffdxyz"
    )
    assert _reply_field("Je vraagt verlof aan.", org_id=None) == "Je vraagt verlof aan."


@pytest.mark.asyncio
async def test_a_question_too_short_to_decide_on_gets_the_plain_contract(monkeypatch, _settings):
    """A soft decision gets neither the shape nor the contrast: the contrast pushes harder than the plain contract."""
    model = _Model(DUTCH)
    _patch(monkeypatch, model)

    await _write(
        _settings,
        [{"role": "user", "content": "Ondersteunt Klai SIP-trunking?"}],
        profile=ChatProfile(surface="widget", kb_mode="strict"),
    )

    reminder = model.requests[0]["messages"][-1]["content"]
    assert reminder.startswith("[FINAL RESPONSE LANGUAGE]") and "written in" not in reminder


def test_a_stream_that_is_not_the_shape_is_shown_as_written():
    field = _ReplyField()
    assert field.feed("Je vraagt verlof ") == "Je vraagt verlof "
    assert field.feed("aan in het portaal.") == "aan in het portaal."
    assert field.finish(org_id=None) == ""
    # A shape cut off before its reply never reaches the reader as raw JSON.
    truncated = _ReplyField()
    assert truncated.feed('{"reply_language": "en", "rep') == ""
    assert truncated.finish(org_id=None) == ""
