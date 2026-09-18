"""The simulated visitor stops when it should, and never writes to a tenant.

SPEC-RAG-ANSWER-JUDGES-001. This harness talks to the live widget, so two
properties matter more than anything it measures: it must use a preview session
(a real one would file its output as the tenant's own conversations), and a
visitor that has its answer must stop instead of filling the turn budget with
noise that the scorer then reads as a bad conversation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import simulate_conversations as sim


class _Client:
    """Stands in for httpx: the widget answers, then the simulated visitor speaks."""

    def __init__(self, visitor_turns: list[str]) -> None:
        self.visitor_turns = list(visitor_turns)
        self.widget_calls = 0

    async def post(self, url: str, **kwargs):
        if "/partner/v1/chat/completions" in url:
            self.widget_calls += 1
            return _Response({"choices": [{"message": {"content": "Ga naar Belplan.", "sources": [{"id": 1}]}}]})
        reply = self.visitor_turns.pop(0) if self.visitor_turns else "DONE"
        return _Response({"choices": [{"message": {"content": reply}}]})


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


@pytest.mark.asyncio
async def test_a_visitor_with_its_answer_stops_instead_of_filling_the_budget():
    client = _Client(["DONE", '{"reached": true, "turns_wasted": 0, "why": "answered"}'])

    result = await sim._one_conversation(
        client, "tok", {"cid": "c1", "beurten": ["Hoe stel ik een wachtrij in?", "en daarna?"]}, 4
    )

    assert result["beurten"] == 1, "the visitor said DONE, so the widget may not be asked again"
    assert client.widget_calls == 1
    assert result["bereikt"] is True


@pytest.mark.asyncio
async def test_the_visitors_own_words_open_every_conversation():
    """Turn one is the calibration point against the replay measurements.

    Generating it would make the first turn incomparable with the real numbers,
    and then nothing after it could be trusted either.
    """
    client = _Client(["DONE", '{"reached": true, "turns_wasted": 0, "why": "ok"}'])

    result = await sim._one_conversation(
        client,
        "tok",
        {
            "cid": "c1",
            "beurten": ["Mijn toestel gaat niet over", "en nu?"],
        },
        4,
    )

    assert result["eerste_vraag"] == "Mijn toestel gaat niet over"
    assert result["transcript"][0] == "Visitor: Mijn toestel gaat niet over"


@pytest.mark.asyncio
async def test_an_unscoreable_conversation_is_reported_rather_than_counted():
    """A scorer that returns prose may not silently become a failed conversation."""
    client = _Client(["DONE", "sorry, I cannot do that"])

    result = await sim._one_conversation(
        client, "tok", {"cid": "c1", "beurten": ["Hoe stel ik een wachtrij in?", "en daarna?"]}, 4
    )

    assert result["bereikt"] is None
    assert result["waarom"] == "score unparseable"
