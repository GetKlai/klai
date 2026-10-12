"""What one widget turn sends to retrieval, and what its record keeps.

A first question searches on the visitor's own words alone; a follow-up adds
the two queries of the conversation's case (case_file.py). The record of the
turn says what was searched, what came back and what the selection made of it.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import partner_chat

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
async def test_a_first_question_searches_on_its_own_words_and_a_follow_up_adds_the_cases_queries(monkeypatch):
    captured: dict[str, Any] = {}
    _capture_retrieve(monkeypatch, captured)
    await _retrieve([_WELCOME, {"role": "user", "content": _QUESTION}], _settings())
    assert captured["body"]["query"] == _QUESTION
    assert captured["body"]["query_variants"] is None

    follow_up = [
        _WELCOME,
        {"role": "user", "content": _QUESTION},
        {"role": "assistant", "content": "Ga naar Belplan."},
        {"role": "user", "content": "en per collega?"},
    ]
    await _retrieve(follow_up, _settings(), follow_up_variants=_VARIANTS)
    assert captured["body"]["query"] == "en per collega?"
    assert captured["body"]["query_variants"] == _VARIANTS, "the visitor's own words stay the primary query"
    assert len(captured["body"]["conversation_history"]) == 3


@pytest.mark.asyncio
async def test_the_turn_record_keeps_the_search_and_the_selection(monkeypatch):
    """A wrong reply used to be unreadable without a private replay: the record
    now says what was searched, what came back and what the selection made of it."""
    captured: dict[str, Any] = {}
    _capture_retrieve(monkeypatch, captured)
    chunk = {
        "chunk_id": "c1",
        "evidence_id": "ev1",
        "title": "Belplan",
        "text": "Klik op Belplan wijzigen.",
        "source_url": "https://help.example.com/belplan",
        "reranker_score": 0.9,
    }
    monkeypatch.setattr(
        "app.services.partner_chat.httpx.AsyncClient",
        lambda timeout: _ClientWith(
            {"chunks": [], "evidence_pack": {"items": [chunk], "sources": []}, "confidence_band": "high"}
        ),
    )
    from app.services.passage_selection import Evidence, PassageSelection

    selection = PassageSelection(
        need="belplan wijzigen",
        evidence=[Evidence(passage=1, quote="Klik op Belplan wijzigen.")],
        verdict="answers",
        question="",
    )
    signals: dict[str, Any] = {}
    follow_up = [
        {"role": "user", "content": "Hoe wijzig ik mijn belplan?"},
        {"role": "assistant", "content": "Ga naar Belplan."},
        {"role": "user", "content": _QUESTION},
    ]
    with patch.object(partner_chat, "select_passages", AsyncMock(return_value=selection)):
        await _retrieve(follow_up, _settings(), answer_signals=signals, follow_up_variants=_VARIANTS)

    assert signals["search"]["query"] == _QUESTION
    assert signals["search"]["variants"] == _VARIANTS
    assert signals["search"]["found"] == [{"title": "Belplan", "url": "https://help.example.com/belplan", "score": 0.9}]
    assert signals["selection"] == {
        "verdict": "answers",
        "evidence": [{"passage": 1, "quote": "Klik op Belplan wijzigen."}],
        "question": "",
    }


class _ClientWith:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def post(self, url, json=None, headers=None):
        class _Resp:
            def __init__(self, payload):
                self._payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self._payload

        return _Resp(self.payload)
