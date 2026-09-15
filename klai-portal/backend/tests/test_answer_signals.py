"""Tests for SPEC-KNOWLEDGE-ACTIVITY-001 §4.1: answer_signals.

The widget chat already computes retrieval-certainty signals per assistant
answer but discards them, so there is no row to compare a visitor's rating
against later. Fase 0a pinned the storage side: the signals must reach the
assistant row's INSERT, must never be written for a user turn, and must be
part of the ORM table so the column is not only reachable from raw SQL.

Fase 0b pins the producing side: both completion pads must hand the signals
to ``record_widget_turn``, and they must never reach the browser — not as an
SSE frame, not as a field in the non-streaming JSON body.

@MX:SPEC: SPEC-KNOWLEDGE-ACTIVITY-001 §4.1
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

SIGNALS: dict = {"band": "high", "top_score": 0.71, "sources_count": 3}


def _message_insert_params(calls: list[tuple[str, dict | None]]) -> dict:
    """Params of the ``widget_messages`` INSERT among the captured executes.

    Asserting on that specific statement (not on the conversation UPSERT)
    is what proves the signals land on the message row.
    """
    for sql, params in calls:
        if "INSERT INTO widget_messages" in sql:
            return params or {}
    raise AssertionError("record_widget_turn never inserted into widget_messages")


async def _record_turn(**kwargs) -> dict:
    """Run record_widget_turn against a mocked session, return the message INSERT params."""
    from app.services.widget_audit import record_widget_turn

    captured: list[tuple[str, dict | None]] = []

    async def fake_execute(sql, params=None):
        captured.append((str(sql), params))
        result = MagicMock()
        result.first.return_value = ("conv-uuid-1", 0)
        return result

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=fake_execute)
    mock_db.commit = AsyncMock()

    # REQ-14: cross_org_session lookup yields org_id from the widgets table.
    lookup_row = MagicMock()
    lookup_row.first.return_value = (1,)
    lookup_db = AsyncMock()
    lookup_db.execute = AsyncMock(return_value=lookup_row)

    with (
        patch("app.services.widget_audit.cross_org_session") as mock_cross,
        patch("app.services.widget_audit.tenant_scoped_session") as mock_ctx,
    ):
        mock_cross.return_value.__aenter__ = AsyncMock(return_value=lookup_db)
        mock_cross.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        await record_widget_turn(
            widget_id="00000000-0000-0000-0000-000000000001",
            session_key="test-session-key",
            **kwargs,
        )

    return _message_insert_params(captured)


@pytest.mark.asyncio
async def test_record_widget_turn_stores_answer_signals_on_assistant_row():
    """answer_signals on an assistant turn reach the widget_messages INSERT."""
    params = await _record_turn(role="assistant", content="Answer body", answer_signals=SIGNALS)

    assert "answer_signals" in params, "answer_signals was not passed to the widget_messages INSERT"
    # JSONB is bound as text by the driver, exactly like `sources` here.
    assert json.loads(params["answer_signals"]) == SIGNALS


@pytest.mark.asyncio
async def test_record_widget_turn_ignores_answer_signals_on_user_row():
    """A visitor turn must never carry signals — the DB CHECK rejects those."""
    params = await _record_turn(role="user", content="Question body", answer_signals=SIGNALS)

    assert params.get("answer_signals") is None, f"answer_signals written on a user row: {params['answer_signals']!r}"


def test_widget_message_model_has_answer_signals_column():
    """The column is part of the ORM table and nullable (no backfill, old rows stay NULL)."""
    from app.models.widgets import WidgetMessage

    assert "answer_signals" in WidgetMessage.__table__.columns
    assert WidgetMessage.__table__.columns["answer_signals"].nullable is True


# ─── fase 0b: the completion pads must produce the signals ───────────────

WIDGET_UUID = "11111111-1111-1111-1111-111111111111"
ANSWER_TEXT = "Je reset het wachtwoord via Instellingen > Beveiliging."
SIGNAL_KEYS = {
    "top_score",
    "band",
    "gap_type",
    "sources_count",
    "refused",
    "broad_mode",
    "language",
    "model",
}


def _http_request_stub() -> MagicMock:
    req = MagicMock()
    req.headers = {}
    req.client = MagicMock(host="127.0.0.1")
    return req


def _chunk(reranker_score: float) -> dict:
    return {
        "chunk_id": "c1",
        "text": ANSWER_TEXT,
        "source_url": "https://example.com/reset",
        "reranker_score": reranker_score,
    }


def _mock_litellm(monkeypatch, model_text: str) -> None:
    """Stand in for the LiteLLM call on both the JSON and the SSE pad."""

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "model": "klai-primary",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": model_text}, "finish_reason": "stop"}
                ],
            }

        async def aiter_lines(self):
            payload = json.dumps({"choices": [{"index": 0, "delta": {"content": model_text}}]})
            yield f"data: {payload}"
            yield "data: [DONE]"

    class _StreamCtx:
        async def __aenter__(self):
            return _Resp()

        async def __aexit__(self, *_):
            return None

    class _Client:
        def __init__(self, *_, **__):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, *_, **__):
            return _Resp()

        def stream(self, *_, **__):
            return _StreamCtx()

    monkeypatch.setattr("app.services.partner_chat.httpx.AsyncClient", _Client)


async def _widget_chat(monkeypatch, *, model_text: str, chunks: list[dict], stream: bool):
    """POST /partner/v1/chat/completions as a widget caller with the audit
    write mocked; returns (record_widget_turn mock, what the client got)."""
    from helpers import FakeResult, make_partner_auth

    from app.api.partner import ChatCompletionsRequest, chat_completions

    _mock_litellm(monkeypatch, model_text)
    auth = make_partner_auth(kb_access={10: "read"})
    auth.key_id = "wgt_test_widget"
    auth.session_key = "test-session-key"
    request = ChatCompletionsRequest(
        messages=[{"role": "user", "content": "hoe reset ik mijn wachtwoord?"}],
        model="klai-primary",
        stream=stream,
        widget_turn_id="a1b2c3d4e5f60718",
    )
    db = AsyncMock()
    # Every widget lookup in the route (page context, widget uuid) reads this row.
    db.execute = AsyncMock(return_value=FakeResult(rows=[WIDGET_UUID]))
    with (
        # KB ids → slugs reads real KB rows; the FakeResult above only knows the
        # widget uuid, so resolve the slugs directly.
        patch("app.api.partner._resolve_kb_slugs", new=AsyncMock(return_value=["kb-test"])),
        patch("app.api.partner.retrieve_context", return_value=(chunks, "sys prompt", [], False)),
        patch("app.api.partner._widget_page_context_enabled", new=AsyncMock(return_value=False)),
        patch("app.api.partner._widget_support_mode_enabled", new=AsyncMock(return_value=False)),
        patch("app.api.partner.record_widget_turn", new=AsyncMock()) as record,
        patch("app.api.partner.write_retrieval_log", new=AsyncMock()),
    ):
        result = await chat_completions(request=request, http_request=_http_request_stub(), auth=auth, db=db)
        frames = [frame async for frame in result.body_iterator] if stream else []
        # Audit writes are fire-and-forget; one pass of the loop runs the task.
        await asyncio.sleep(0)
    return record, (frames if stream else result)


def _assistant_signals(record: AsyncMock) -> dict:
    """answer_signals of the assistant audit write (the user turn has none)."""
    assistant = [c.kwargs for c in record.await_args_list if c.kwargs.get("role") == "assistant"]
    assert assistant, "record_widget_turn never wrote an assistant turn"
    return assistant[-1]["answer_signals"]


def test_band_thresholds():
    """Band boundaries come from settings, not from literals at the call site."""
    from app.services.partner_chat import _answer_confidence_band

    assert _answer_confidence_band(0.71) == "high"
    assert _answer_confidence_band(0.45) == "medium"
    assert _answer_confidence_band(0.12) == "low"
    assert _answer_confidence_band(None) == "unknown"


def test_config_rejects_low_above_high():
    """A band ladder with low >= high has no middle band; refuse it at startup."""
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(answer_confidence_low_threshold=0.7, answer_confidence_high_threshold=0.6)


@pytest.mark.asyncio
async def test_non_streaming_assistant_turn_carries_answer_signals(monkeypatch):
    record, body = await _widget_chat(monkeypatch, model_text=ANSWER_TEXT, chunks=[_chunk(0.71)], stream=False)

    signals = _assistant_signals(record)
    assert set(signals) == SIGNAL_KEYS
    assert signals["band"] == "high"
    assert signals["top_score"] == pytest.approx(0.71)
    assert signals["sources_count"] == 1
    assert signals["refused"] is False
    assert signals["broad_mode"] is False
    # Audit-only data: the visitor's JSON must not reveal how sure we were,
    # not even nested inside message.
    assert "answer_signals" not in json.dumps(body)


@pytest.mark.asyncio
async def test_streaming_assistant_turn_carries_answer_signals(monkeypatch):
    record, frames = await _widget_chat(monkeypatch, model_text=ANSWER_TEXT, chunks=[_chunk(0.71)], stream=True)

    assert b"answer_signals" not in b"".join(frames), "answer_signals leaked into an SSE frame"
    signals = _assistant_signals(record)
    assert set(signals) == SIGNAL_KEYS
    assert signals["band"] == "high"
    assert signals["top_score"] == pytest.approx(0.71)
    assert signals["sources_count"] == 1
    assert signals["refused"] is False


@pytest.mark.asyncio
async def test_refusal_marks_refused_and_zero_sources(monkeypatch):
    """An answer the citation firewall could not ground is the fixed refusal:
    that is what `refused` means, and it leaves zero sources behind."""
    record, _body = await _widget_chat(monkeypatch, model_text="Ik ken het antwoord niet.", chunks=[], stream=False)

    signals = _assistant_signals(record)
    assert signals["refused"] is True
    assert signals["sources_count"] == 0
    # No chunks at all means no top_score, which is `unknown` rather than a band.
    assert signals["band"] == ("unknown" if signals["top_score"] is None else "low")


# ─── review fixes: the signals must describe the retrieval, not the citation list ───


def _fill(chunks: list[dict], *, query: str = "hoe reset ik mijn wachtwoord?", sources: list | None = None) -> dict:
    from app.services.partner_chat import _fill_answer_signals

    sink: dict = {}
    _fill_answer_signals(
        sink,
        decision={"reason": "test"},
        refused=False,
        chunks=chunks,
        sources=sources or [],
        model="klai-primary",
        query_text=query,
    )
    return sink


def test_broad_turn_scores_the_retrieval_that_triggered_it():
    """A broad-mode turn cites nothing, but its certainty is the weak retrieval
    that made it broad — so the signals are computed on those chunks, not on
    the deliberately emptied citation list."""
    sink = _fill([_chunk(0.18)], sources=[])

    assert sink["top_score"] == 0.18
    assert sink["band"] == "low"
    assert sink["gap_type"] == "soft"
    assert sink["sources_count"] == 0


def test_band_is_unknown_without_a_reranker_score():
    """Retrieval-api contract: only a cross-encoder score is certainty evidence.
    A dense-only fallback chunk still yields a top_score (gap-event parity) but
    bands as unknown, exactly like confidence_band on the LibreChat path."""
    sink = _fill([{"chunk_id": "c1", "text": "x", "score": 0.9, "reranker_score": None}])

    assert sink["top_score"] == 0.9
    assert sink["band"] == "unknown"


def test_language_is_the_visitor_question_language():
    """Spec §4.9: a gap in English knowledge must group as English even when
    the assistant answered in Dutch, so the language comes from the question."""
    sink = _fill([_chunk(0.7)], query="How do I reset my password for the desk phone?")

    assert sink["language"] == "en"
