"""A widget reply written from chosen passages is recorded at once; its signals are completed after the later check.

Step M (plan §8): the statement check on such a reply runs after the reply
went out. The row is written first, so a quick follow-up keeps the transcript
order and the feedback endpoint finds the turn, and the signals are replaced
once the check is done.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.api import partner
from app.services.retrieval_context import KnowledgeTurn
from app.services.widget_reply import Reply, reply_frames


@pytest.mark.asyncio
async def test_the_record_is_written_before_the_later_check_and_its_signals_after(monkeypatch):
    signals: dict[str, Any] = {"decision": "answer"}
    order: list[str] = []

    async def record(**fields: Any) -> int:
        order.append(f"record:{'grounding' in fields['answer_signals']}")
        return 77

    async def patch(**fields: Any) -> None:
        order.append(f"patch:{fields['answer_signals'].get('grounding')}:{fields['message_id']}")

    async def later_check() -> None:
        await asyncio.sleep(0.01)
        signals["grounding"] = "all_in_articles"

    monkeypatch.setattr(partner, "record_widget_turn", record)
    monkeypatch.setattr(partner, "patch_answer_signals", patch)
    turn = KnowledgeTurn()
    turn.grounding_check = asyncio.create_task(later_check())

    before = set(partner._pending)
    partner._write_widget_record(
        turn, widget_id="w1", session_key="s1", role="assistant", content="x", turn_id="t1", answer_signals=signals
    )
    await asyncio.gather(*(partner._pending - before))

    assert order == ["record:False", "patch:all_in_articles:77"]


@pytest.mark.asyncio
async def test_without_a_later_check_the_record_is_written_once_and_never_patched(monkeypatch):
    record, patch = AsyncMock(), AsyncMock()
    monkeypatch.setattr(partner, "record_widget_turn", record)
    monkeypatch.setattr(partner, "patch_answer_signals", patch)

    before = set(partner._pending)
    partner._write_widget_record(
        KnowledgeTurn(), widget_id="w1", session_key="s1", role="assistant", content="x", answer_signals={}
    )
    await asyncio.gather(*(partner._pending - before))

    record.assert_awaited_once()
    patch.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_streamed_reply_is_recorded_from_the_reply_before_its_first_frame(monkeypatch):
    source = {"label": "1", "title": "Voorbeeld artikel", "url": "https://help.example.com/a", "origin": "kb"}
    reply = Reply(
        text="Open de instellingen.",
        sources=[source],
        signals={"decision": "answer"},
        language="nl",
        turn=KnowledgeTurn(),
    )
    written: list[dict[str, Any]] = []
    monkeypatch.setattr(
        partner, "_write_widget_record", lambda turn, **fields: written.append({"turn": turn, **fields})
    )
    request = partner.ChatCompletionsRequest(messages=[{"role": "user", "content": "Hoe?"}], stream=True)

    response = partner._reply_response(reply, request, {"widget_id": "w1", "session_key": "s1", "turn_id": "t1"})

    assert written == [
        {
            "turn": reply.turn,
            "widget_id": "w1",
            "session_key": "s1",
            "turn_id": "t1",
            "role": "assistant",
            "content": "Open de instellingen.",
            "sources": [source],
            "answer_signals": {"decision": "answer"},
        }
    ]
    assert [frame async for frame in response.body_iterator] == reply_frames(reply)
