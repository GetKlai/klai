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
    client = _Client(["DONE", '{"reached": true, "handed_off": false, "turns_wasted": 0, "why": "answered"}'])

    result = await sim._one_conversation(
        client, "tok", {"cid": "c1", "eerste": "Hoe stel ik een wachtrij in?", "doel": "een wachtrij instellen"}, 4
    )

    assert result["beurten"] == 1, "the visitor said DONE, so the widget may not be asked again"
    assert client.widget_calls == 1
    assert result["bereikt"] is True


@pytest.mark.asyncio
async def test_a_hand_off_is_reported_and_not_counted_as_reached():
    """The harness used to reward a hand-off because that is the feature it was checking.

    A visitor sent to a person did not get the answer their goal asked for, so
    ``bereikt`` must stay False even though the assistant behaved honestly.
    """
    client = _Client(["DONE", '{"reached": false, "handed_off": true, "turns_wasted": 1, "why": "sent to human"}'])

    result = await sim._one_conversation(
        client, "tok", {"cid": "c1", "eerste": "Hoe stel ik een wachtrij in?", "doel": "een wachtrij instellen"}, 4
    )

    assert result["bereikt"] is False
    assert result["doorverwezen"] is True


@pytest.mark.asyncio
async def test_model_calls_never_use_the_model_under_test():
    """Scoring the grounding check with the grounding check's own model judges nothing.

    Goal, visitor and scorer all go through ``_model``, so one call is enough
    to prove none of them can reach ``answer_grounding_model``.
    """
    calls: list[str] = []

    class _RecordingClient:
        async def post(self, url: str, **kwargs):
            calls.append(kwargs["json"]["model"])
            return _Response({"choices": [{"message": {"content": "ok"}}]})

    await sim._model(_RecordingClient(), "system", "user")

    assert calls[0] != sim.settings.answer_grounding_model
    assert calls[0] == sim._SIMULATION_MODEL


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
            "eerste": "Mijn toestel gaat niet over",
            "doel": "het toestel gaat niet over bij een inkomend gesprek",
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
        client, "tok", {"cid": "c1", "eerste": "Hoe stel ik een wachtrij in?", "doel": "een wachtrij instellen"}, 4
    )

    assert result["bereikt"] is None
    assert result["waarom"] == "score unparseable"


def test_a_score_without_a_verdict_is_not_a_failure():
    """Parseable JSON with the wrong types used to count as a failed conversation.

    The success rate is the number this harness exists to compare between two
    versions, so anything unusable has to leave the denominator rather than
    quietly lower it.
    """
    assert sim._parse_score('{"reached": "yes", "handed_off": false, "turns_wasted": 0}')["reached"] is None
    assert sim._parse_score('{"turns_wasted": 2}')["reached"] is None
    assert sim._parse_score('{"reached": true, "handed_off": "no", "turns_wasted": 0, "why": "ok"}')["reached"] is None
    assert sim._parse_score('{"reached": true, "handed_off": false, "turns_wasted": "two", "why": "ok"}') == {
        "reached": True,
        "handed_off": False,
        "turns_wasted": None,
        "why": "ok",
    }


@pytest.mark.asyncio
async def test_a_throttled_call_waits_instead_of_hammering_through(monkeypatch):
    """The harness shares a rate limit with real visitors.

    Run unpaced on 2026-09-18 it exhausted the klai-fast budget: the alias
    answered 429, the widget returned 502, and a visitor asking a question in
    that window would have got an error. Backing off is therefore a property of
    this script, not a nicety.
    """
    import httpx

    slept: list[float] = []

    async def _no_wait(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(sim.asyncio, "sleep", _no_wait)
    attempts = {"n": 0}

    async def _call():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise httpx.HTTPStatusError("429", request=httpx.Request("POST", "http://x"), response=httpx.Response(429))
        return "ok"

    assert await sim._with_backoff("de widget", _call) == "ok"
    assert attempts["n"] == 3
    assert slept == [5.0, 15.0], "each retry has to wait longer than the last"


@pytest.mark.asyncio
async def test_a_real_failure_is_not_retried(monkeypatch):
    """Only throttling and a brief gateway hiccup are worth waiting for.

    Retrying a 401 or a 400 would spend the budget it is meant to protect.
    """
    import httpx

    monkeypatch.setattr(sim.asyncio, "sleep", lambda _s: _noop())
    attempts = {"n": 0}

    async def _call():
        attempts["n"] += 1
        raise httpx.HTTPStatusError("401", request=httpx.Request("POST", "http://x"), response=httpx.Response(401))

    with pytest.raises(httpx.HTTPStatusError):
        await sim._with_backoff("de widget", _call)
    assert attempts["n"] == 1


async def _noop() -> None:
    return None
