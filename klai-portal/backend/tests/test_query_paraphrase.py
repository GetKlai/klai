"""The first support-mode question goes to retrieval with two paraphrases; nothing else does.

SPEC-RAG-ANSWER-JUDGES-001, logbook 2.33. The paraphrases are retrieval input
only: the visitor's own words stay the primary query, a follow-up turn keeps
using its history instead, and a failed paraphrase call leaves the request
exactly as it was.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import partner_chat, query_paraphrase

_QUESTION = "Goedemorgen, mijn vaste lijn en mobiele lijn gaan gelijk over tot voicemail."
_VARIANTS = [
    "Hoe schakel ik de gelijktijdige doorschakeling naar voicemail uit?",
    "Nummers niet tegelijk naar voicemail",
]
_WELCOME = {"role": "assistant", "content": "Hoi! Waar kan ik je mee helpen?"}


def _capture_retrieve(monkeypatch, captured: dict[str, Any]) -> None:
    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"chunks": [], "evidence_pack": {"items": [], "sources": []}, "confidence_band": "low"}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, url, json=None, headers=None):
            captured["body"] = json
            return _Resp()

    monkeypatch.setattr("app.services.partner_chat.httpx.AsyncClient", lambda timeout: _Client())
    monkeypatch.setattr(partner_chat, "_schedule_gap_event", lambda *a, **k: None)


def _settings() -> MagicMock:
    settings = MagicMock()
    settings.knowledge_retrieve_url = "http://retrieval-api:8040"
    settings.retrieval_api_internal_secret = "secret"
    settings.internal_secret = "fallback"
    return settings


async def _retrieve(messages: list[dict], settings: MagicMock, **overrides: Any):
    return await partner_chat.retrieve_context(
        org_id=42,
        zitadel_org_id="zit-org-1",
        kb_slugs=["support"],
        messages=messages,
        settings=settings,
        support_mode=True,
        **overrides,
    )


@pytest.mark.asyncio
async def test_the_first_question_travels_with_two_paraphrases(monkeypatch):
    captured: dict[str, Any] = {}
    _capture_retrieve(monkeypatch, captured)
    with patch.object(query_paraphrase, "paraphrase_first_question", AsyncMock(return_value=_VARIANTS)) as para:
        await _retrieve([{"role": "user", "content": _QUESTION}], _settings())

    para.assert_awaited_once()
    assert captured["body"]["query"] == _QUESTION, "the visitor's own words stay the primary query"
    assert captured["body"]["query_variants"] == _VARIANTS


@pytest.mark.asyncio
async def test_the_widget_welcome_line_does_not_make_the_first_question_a_follow_up(monkeypatch):
    """The browser widget seeds every conversation with its welcome line as an
    assistant message and sends it back with the first question. That line is
    not an answer: the visitor's first question still gets its paraphrases."""
    captured: dict[str, Any] = {}
    _capture_retrieve(monkeypatch, captured)
    messages = [_WELCOME, {"role": "user", "content": _QUESTION}]
    with patch.object(query_paraphrase, "paraphrase_first_question", AsyncMock(return_value=_VARIANTS)) as para:
        await _retrieve(messages, _settings())

    para.assert_awaited_once()
    assert captured["body"]["query_variants"] == _VARIANTS


@pytest.mark.asyncio
async def test_a_follow_up_keeps_its_history_and_gets_no_paraphrases(monkeypatch):
    captured: dict[str, Any] = {}
    _capture_retrieve(monkeypatch, captured)
    messages = [
        _WELCOME,
        {"role": "user", "content": _QUESTION},
        {"role": "assistant", "content": "Ga naar Belplan."},
        {"role": "user", "content": "en per collega?"},
    ]
    with patch.object(query_paraphrase, "paraphrase_first_question", AsyncMock(return_value=_VARIANTS)) as para:
        await _retrieve(messages, _settings())

    para.assert_not_awaited()
    assert captured["body"]["query_variants"] is None
    assert len(captured["body"]["conversation_history"]) == 3


@pytest.mark.asyncio
async def test_a_failed_paraphrase_call_leaves_the_request_as_it_was(monkeypatch):
    """The paraphrase step is retrieval input only; when the model is down the
    first question is searched exactly as before."""
    captured: dict[str, Any] = {}
    _capture_retrieve(monkeypatch, captured)
    with patch.object(query_paraphrase, "structured_judge_call", AsyncMock(return_value=None)):
        await _retrieve([{"role": "user", "content": _QUESTION}], _settings())

    assert captured["body"]["query_variants"] is None
    assert captured["body"]["query"] == _QUESTION


@pytest.mark.asyncio
async def test_a_paraphrase_equal_to_the_question_is_dropped():
    settings = MagicMock()
    settings.retrieval_paraphrase_model = "klai-medium"
    reply = query_paraphrase.QueryParaphrases(
        variants=[_QUESTION.upper(), "Vaste en mobiele lijn tegelijk naar voicemail", "  "]
    )
    with patch.object(query_paraphrase, "structured_judge_call", AsyncMock(return_value=reply)) as call:
        variants = await query_paraphrase.paraphrase_first_question(_QUESTION, settings)

    assert variants == ["Vaste en mobiele lijn tegelijk naar voicemail"]
    assert call.await_args is not None
    assert call.await_args.kwargs["model"] == "klai-medium", "not the answer model's quota"
