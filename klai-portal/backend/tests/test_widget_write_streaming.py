"""The one writer's streamed delivery: an internal Open turn reads as the model writes it.

The model double below holds back its second delta until the test has received
the first content frame, so a writer that waited for the whole answer before
sending anything would never get there and the test times out instead.

synthetic-data: generator=hand-written seed=0 (fictional HR article, example.com URLs).
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from app.services.chat_profile import ChatProfile
from app.services.widget_reply import streamed_frames
from app.services.widget_write import write_reply

ARTICLE = "Je vraagt verlof aan in het HR-portaal onder Verlof."
SOURCE = {"label": "1", "title": "Verlof aanvragen", "url": "https://kb.example.com/verlof"}
CHUNK = {"title": "Verlof aanvragen", "source_url": "https://kb.example.com/verlof", "text": ARTICLE}


class _PausingModel:
    """LiteLLM's stream, paused after its first delta until the reader has seen it."""

    def __init__(self, first: str, second: str) -> None:
        self.deltas = (first, second)
        self.first_seen = asyncio.Event()

    def __call__(self, timeout: float) -> _PausingModel:
        return self

    async def __aenter__(self) -> _PausingModel:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def stream(self, *_: object, **__: object) -> _PausingModel:
        return self

    def raise_for_status(self) -> None:
        return None

    async def aiter_lines(self):
        first, second = self.deltas
        yield "data: " + json.dumps({"choices": [{"delta": {"content": first}}]})
        await self.first_seen.wait()
        yield "data: " + json.dumps({"choices": [{"delta": {"content": second}}]})
        yield "data: [DONE]"


def _deltas(frame: bytes) -> list[dict]:
    payload = frame.decode().removeprefix("data: ").strip()
    return [] if payload == "[DONE]" else [c["delta"] for c in json.loads(payload).get("choices") or []]


@pytest.mark.asyncio
async def test_an_open_reply_streams_its_words_first_and_ends_on_its_sources_and_footer(monkeypatch):
    model = _PausingModel("Je vraagt verlof aan in het HR-portaal ", "onder Verlof, zo regel je het snel en goed.")
    monkeypatch.setattr("app.services.widget_write.httpx.AsyncClient", model)
    settings = MagicMock(litellm_base_url="http://litellm.example.com", litellm_master_key="key")
    reply = await write_reply(
        messages=[{"role": "user", "content": "Hoe vraag ik verlof aan?"}],
        model="klai-primary",
        temperature=0.2,
        system_prompt="prompt",
        settings=settings,
        citation_chunks=[CHUNK],
        trusted_sources=[SOURCE],
        profile=ChatProfile(surface="internal", kb_mode="open"),
        stream=True,
    )

    async def read() -> list[bytes]:
        frames = []
        async for frame in streamed_frames(reply):
            frames.append(frame)
            if any("content" in d for d in _deltas(frame)):
                model.first_seen.set()
        return frames

    frames = await asyncio.wait_for(read(), timeout=2)

    deltas = [d for frame in frames for d in _deltas(frame)]
    contents = [i for i, d in enumerate(deltas) if "content" in d]
    first, last = contents[0], contents[-1]
    # The employee read the model's own words before it finished, without the
    # footer; the guard holds back only the last 32 characters written so far.
    assert deltas[first]["content"] == "Je vraa"
    assert "**Bronnen**" not in "".join(deltas[i]["content"] for i in contents[:-1])
    # The sources and the footer come after what the model wrote, the footer last of all.
    sources = next(i for i, d in enumerate(deltas) if "sources" in d)
    assert first < sources < last
    assert deltas[last]["content"].endswith("- Kennisbank geraadpleegd: 1 fragment opgehaald.")
    assert "**Bronnen**\n- [Verlof aanvragen](https://kb.example.com/verlof)" in deltas[last]["content"]
    assert frames[-1] == b"data: [DONE]\n\n"
    # Every word was sent exactly once.
    text = "".join(deltas[i]["content"] for i in contents)
    assert text.startswith("Je vraagt verlof aan in het HR-portaal onder Verlof, zo regel je het snel en goed.\n\n")
