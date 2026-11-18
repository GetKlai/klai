"""The one writer's guard against a reply that does not stop (SPEC-CHAT-INTERNAL-ROUTE-001 §4).

On 17 November 2026 the internal chat wrote more than twelve thousand
characters on a five-word question, the same sentences up to eight times.
Every writer call now carries an output cap, and a reply in which a sentence
of more than forty characters comes a third time ends before it, held or
streamed. A reply with two variants that share their steps keeps both.

synthetic-data: generator=hand-written seed=0 (fictional HR steps, example.com URLs).
"""

from __future__ import annotations

import itertools
import json
from unittest.mock import MagicMock

import httpx
import pytest

from app.services.chat_profile import ChatProfile
from app.services.widget_reply import streamed_frames
from app.services.widget_write import write_reply

STEP = "Open het HR-portaal en kies in het menu de tegel Verlof aanvragen."
OTHER = "Vul de begindatum en de einddatum in en sla de aanvraag op."
RUNAWAY = f"- {STEP} {OTHER} {STEP} {OTHER} {STEP} {OTHER} {STEP}"
COUNT = "Je hebt per jaar recht op vakantiedagen, in totaal 25."
NUMBERED = f"1. {STEP}\n2. {COUNT} {OTHER}\n3. Vraag je leidinggevende om akkoord, dat duurt hooguit 2."
SOURCE = {"label": "1", "title": "Verlof aanvragen", "url": "https://kb.example.com/verlof"}
CHUNK = {"title": "Verlof aanvragen", "source_url": "https://kb.example.com/verlof", "text": STEP}
VARIANTS = f"**Op kantoor**\n1. {STEP}\n2. {OTHER}\n\n**Thuis**\n1. {STEP}\n2. {OTHER}\nKlaar."


class _Model:
    """LiteLLM: answers every call with ``reply``, streamed in small pieces when asked to stream."""

    def __init__(self, reply: str, *, finish_reason: str = "stop") -> None:
        self.reply, self.finish_reason = reply, finish_reason
        self.requests: list[dict] = []
        self.pieces_sent = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        if not body.get("stream"):
            message = {"role": "assistant", "content": self.reply}
            return httpx.Response(200, json={"choices": [{"message": message, "finish_reason": self.finish_reason}]})
        return httpx.Response(200, content=self._frames())

    def _frames(self):
        async def frames():
            cuts = range(0, len(self.reply) + 9, 9)
            for a, b in itertools.pairwise(cuts):
                if piece := self.reply[a:b]:
                    self.pieces_sent += 1
                    yield f"data: {json.dumps({'choices': [{'delta': {'content': piece}}]})}\n\n".encode()
            end = {"choices": [{"delta": {}, "finish_reason": self.finish_reason}]}
            yield f"data: {json.dumps(end)}\n\ndata: [DONE]\n\n".encode()

        return frames()


def _patch(monkeypatch, model: _Model) -> None:
    original = httpx.AsyncClient
    monkeypatch.setattr(
        "app.services.widget_write.httpx.AsyncClient",
        lambda timeout: original(transport=httpx.MockTransport(model)),
    )


def _settings(cap: int = 2000) -> MagicMock:
    return MagicMock(
        litellm_base_url="http://litellm.example.com", litellm_master_key="key", chat_writer_max_tokens=cap
    )


def _payloads(frames: list[bytes]) -> list[str]:
    return [frame.decode().removeprefix("data: ").strip() for frame in frames]


def _contents(frames: list[bytes]) -> str:
    out = []
    for payload in _payloads(frames):
        if payload == "[DONE]":
            continue
        for choice in json.loads(payload).get("choices") or []:
            out.append(choice["delta"].get("content") or "")
    return "".join(out)


async def _write(
    settings, profile: ChatProfile, *, stream: bool = False, signals: dict | None = None, sources: bool = False
):
    return await write_reply(
        citation_chunks=[CHUNK] if sources else None,
        trusted_sources=[SOURCE] if sources else None,
        messages=[{"role": "user", "content": "Hoe vraag ik verlof aan?"}],
        model="klai-primary",
        temperature=0.2,
        system_prompt="prompt",
        settings=settings,
        profile=profile,
        answer_signals=signals,
        stream=stream,
    )


WIDGET = ChatProfile(surface="widget", kb_mode="strict")
INTERNAL_OPEN = ChatProfile(surface="internal", kb_mode="open")


@pytest.mark.parametrize(
    ("profile", "stream"),
    [(WIDGET, False), (ChatProfile(surface="internal", kb_mode="strict"), False), (INTERNAL_OPEN, True)],
    ids=["widget", "internal-strict", "internal-open-streamed"],
)
@pytest.mark.asyncio
async def test_every_writer_body_carries_the_output_cap_from_the_setting(monkeypatch, profile, stream):
    model = _Model("Je vraagt verlof aan in het HR-portaal.")
    _patch(monkeypatch, model)

    reply = await _write(_settings(cap=1234), profile, stream=stream)
    if stream:
        [frame async for frame in streamed_frames(reply)]

    (body,) = model.requests
    assert body["max_tokens"] == 1234


@pytest.mark.asyncio
async def test_a_held_reply_ends_before_a_long_sentence_comes_a_third_time(monkeypatch):
    _patch(monkeypatch, _Model(RUNAWAY))
    signals: dict = {}

    reply = await _write(_settings(), ChatProfile(surface="internal", kb_mode="general"), signals=signals)

    assert reply.text.startswith(f"- {STEP} {OTHER} {STEP} {OTHER}")
    assert reply.text.count(STEP) == 2 and reply.text.count(OTHER) == 2
    assert signals["writer"]["cut"] == "repeat"


@pytest.mark.asyncio
async def test_a_streamed_reply_stops_before_a_long_sentence_comes_a_third_time_and_ends_cleanly(monkeypatch):
    model = _Model(RUNAWAY)
    _patch(monkeypatch, model)
    signals: dict = {}

    reply = await _write(_settings(), ChatProfile(surface="internal", kb_mode="general"), stream=True, signals=signals)
    frames = [frame async for frame in streamed_frames(reply)]

    shown = _contents(frames)
    assert shown.count(STEP) == 2 and shown.count(OTHER) == 2
    assert "Open het HR" not in shown.split(OTHER)[-1], "no part of the third occurrence reached the reader"
    assert _payloads(frames)[-1] == "[DONE]"
    # The writer stopped reading the model at the cut, it did not wait for the rest.
    assert model.pieces_sent < len(RUNAWAY) // 9
    assert signals["writer"]["cut"] == "repeat"


@pytest.mark.parametrize("stream", [False, True], ids=["held", "streamed"])
@pytest.mark.asyncio
async def test_a_reply_with_two_variants_that_share_their_steps_is_delivered_whole(monkeypatch, stream):
    _patch(monkeypatch, _Model(VARIANTS))
    signals: dict = {}

    reply = await _write(
        _settings(), ChatProfile(surface="internal", kb_mode="general"), stream=stream, signals=signals
    )
    text = _contents([frame async for frame in streamed_frames(reply)]) if stream else reply.text

    assert text.startswith(VARIANTS)
    assert signals["writer"]["cut"] is None


@pytest.mark.asyncio
async def test_a_reply_the_model_ended_at_its_token_limit_is_recorded_as_cut_by_length(monkeypatch):
    _patch(monkeypatch, _Model("Je vraagt verlof aan in het HR-portaal onder", finish_reason="length"))
    signals: dict = {}

    await _write(_settings(), WIDGET, signals=signals)

    assert signals["writer"] == {
        "tools_offered": False,
        "action_turn": False,
        "model": "klai-primary",
        "fallback": False,
        "cut": "length",
    }


@pytest.mark.asyncio
async def test_a_sentence_ending_in_a_number_that_comes_a_third_time_on_one_line_is_cut_before_it(monkeypatch):
    _patch(monkeypatch, _Model(f"- {COUNT} {COUNT} {COUNT} {OTHER}"))
    signals: dict = {}

    reply = await _write(_settings(), ChatProfile(surface="internal", kb_mode="general"), signals=signals)

    assert reply.text == f"- {COUNT} {COUNT}"
    assert signals["writer"]["cut"] == "repeat"


@pytest.mark.parametrize("stream", [False, True], ids=["held", "streamed"])
@pytest.mark.asyncio
async def test_a_numbered_list_of_different_steps_is_delivered_whole(monkeypatch, stream):
    _patch(monkeypatch, _Model(NUMBERED))
    signals: dict = {}

    reply = await _write(
        _settings(), ChatProfile(surface="internal", kb_mode="general"), stream=stream, signals=signals
    )
    text = _contents([frame async for frame in streamed_frames(reply)]) if stream else reply.text

    assert text.startswith(NUMBERED)
    assert signals["writer"]["cut"] is None


@pytest.mark.asyncio
async def test_a_streamed_reply_ended_at_the_token_limit_is_delivered_and_recorded_as_cut_by_length(monkeypatch):
    reply_text = "Je vraagt verlof aan in het HR-portaal onder"
    _patch(monkeypatch, _Model(reply_text, finish_reason="length"))
    signals: dict = {}

    reply = await _write(_settings(), INTERNAL_OPEN, stream=True, signals=signals)
    frames = [frame async for frame in streamed_frames(reply)]

    assert _contents(frames).startswith(reply_text)
    assert _payloads(frames)[-1] == "[DONE]"
    assert signals["writer"]["cut"] == "length"


@pytest.mark.asyncio
async def test_after_a_streamed_cut_by_repetition_the_sources_and_the_footer_still_precede_done(monkeypatch):
    _patch(monkeypatch, _Model(RUNAWAY))
    signals: dict = {}

    reply = await _write(_settings(), INTERNAL_OPEN, stream=True, signals=signals, sources=True)
    frames = [frame async for frame in streamed_frames(reply)]

    payloads = _payloads(frames)
    assert payloads[-1] == "[DONE]"
    sources_at = next(i for i, p in enumerate(payloads[:-1]) if "sources" in json.loads(p)["choices"][0]["delta"])
    footer = _contents(frames)
    assert "**Bronnen**\n- [Verlof aanvragen](https://kb.example.com/verlof)" in footer
    assert footer.count(STEP) == 2
    assert sources_at < len(payloads) - 1
    assert signals["writer"]["cut"] == "repeat"
