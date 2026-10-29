"""The writer answers in the language the turn decided, whatever language the sources are in.

On 29 October 2026 the widget and the internal chat answered English questions
in Dutch under a "Respond in English" instruction: the model followed its
Dutch source lines. The instruction is not enough, so the writer now checks
the language of what the model wrote and has it translated when it differs
from the turn's decision: a held reply before it is composed, a streamed one
before its first words are shown.

synthetic-data: generator=hand-written seed=0 (fictional HR article, example.com URLs).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import httpx
import pytest

from app.services.chat_profile import ChatProfile
from app.services.widget_reply import streamed_frames
from app.services.widget_write import write_reply

DUTCH = "Je vraagt verlof aan in het HR-portaal onder Verlof, daar staat de knop Aanvragen."
ENGLISH = "You request leave in the HR-portaal under Verlof, where the Aanvragen button is."
SOURCE = {"label": "1", "title": "Verlof aanvragen", "url": "https://kb.example.com/verlof"}
CHUNK = {"title": "Verlof aanvragen", "source_url": "https://kb.example.com/verlof", "text": DUTCH}


class _Model:
    """LiteLLM: the answer call writes ``draft``; a translation call answers ``translated``."""

    def __init__(self, draft: str, translated: str) -> None:
        self.draft, self.translated = draft, translated
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        if "Translate" in body["messages"][0]["content"]:
            return httpx.Response(200, json={"choices": [{"message": {"content": self.translated}}]})
        if body.get("stream"):
            frames = "".join(
                f"data: {json.dumps({'choices': [{'delta': {'content': part}}]})}\n\n"
                for part in (self.draft[:12], self.draft[12:40], self.draft[40:])
            )
            return httpx.Response(200, content=f"{frames}data: [DONE]\n\n".encode())
        return httpx.Response(200, json={"choices": [{"message": {"content": self.draft}, "finish_reason": "stop"}]})


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


@pytest.mark.asyncio
async def test_a_held_reply_in_the_source_language_is_translated_once_it_is_accepted(monkeypatch, _settings):
    model = _Model(DUTCH, ENGLISH)
    _patch(monkeypatch, model)

    reply = await write_reply(
        messages=[{"role": "user", "content": "How do I request leave for next week?"}],
        model="klai-primary",
        temperature=0.2,
        system_prompt="prompt",
        settings=_settings,
        citation_chunks=[CHUNK],
        trusted_sources=[SOURCE],
        profile=ChatProfile(surface="widget", kb_mode="strict"),
    )

    assert reply.text.startswith(ENGLISH)
    assert DUTCH not in reply.text
    translation = model.requests[1]
    assert "English" in translation["messages"][0]["content"]
    assert DUTCH in translation["messages"][1]["content"]
    assert reply.signals["language_repaired"] == {"from": "nl", "to": "en"}


@pytest.mark.asyncio
async def test_a_held_reply_in_the_decided_language_is_not_translated(monkeypatch, _settings):
    model = _Model(DUTCH, ENGLISH)
    _patch(monkeypatch, model)

    reply = await write_reply(
        messages=[{"role": "user", "content": "Hoe vraag ik verlof aan voor volgende week?"}],
        model="klai-primary",
        temperature=0.2,
        system_prompt="prompt",
        settings=_settings,
        citation_chunks=[CHUNK],
        trusted_sources=[SOURCE],
        profile=ChatProfile(surface="widget", kb_mode="strict"),
    )

    assert reply.text.startswith(DUTCH)
    assert len(model.requests) == 1
    assert "language_repaired" not in reply.signals


@pytest.mark.asyncio
async def test_a_streamed_reply_in_the_source_language_shows_only_the_translation(monkeypatch, _settings):
    model = _Model(DUTCH, ENGLISH)
    _patch(monkeypatch, model)

    reply = await write_reply(
        messages=[{"role": "user", "content": "How do I request leave for next week?"}],
        model="klai-primary",
        temperature=0.2,
        system_prompt="prompt",
        settings=_settings,
        citation_chunks=[CHUNK],
        trusted_sources=[SOURCE],
        profile=ChatProfile(surface="internal", kb_mode="open"),
        stream=True,
    )
    frames = [frame async for frame in streamed_frames(reply)]

    shown = "".join(_contents(frames))
    assert shown.startswith(ENGLISH)
    assert "Je vraagt" not in shown
    assert reply.signals["language_repaired"] == {"from": "nl", "to": "en"}


@pytest.mark.asyncio
async def test_a_streamed_reply_in_the_decided_language_streams_the_model_s_own_words(monkeypatch, _settings):
    model = _Model(DUTCH, ENGLISH)
    _patch(monkeypatch, model)

    reply = await write_reply(
        messages=[{"role": "user", "content": "Hoe vraag ik verlof aan voor volgende week?"}],
        model="klai-primary",
        temperature=0.2,
        system_prompt="prompt",
        settings=_settings,
        citation_chunks=[CHUNK],
        trusted_sources=[SOURCE],
        profile=ChatProfile(surface="internal", kb_mode="open"),
        stream=True,
    )
    frames = [frame async for frame in streamed_frames(reply)]

    contents = _contents(frames)
    assert "".join(contents).startswith(DUTCH)
    # The first words went out before the model had finished: the gate decided on the opening words.
    assert len(contents) > 2
    assert len(model.requests) == 1
