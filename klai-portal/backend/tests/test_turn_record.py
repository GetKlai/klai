"""What a follow-up widget turn sends to retrieval: the two queries of the conversation's case (case_file.py)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

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
        **overrides,
    )


@pytest.mark.asyncio
async def test_a_follow_up_adds_the_cases_queries_beside_its_own_words(monkeypatch):
    captured: dict[str, Any] = {}
    _capture_retrieve(monkeypatch, captured)
    follow_up = [
        _WELCOME,
        {"role": "user", "content": _QUESTION},
        {"role": "assistant", "content": "Ga naar Belplan."},
        {"role": "user", "content": "en per collega?"},
    ]
    await _retrieve(follow_up, _settings(), follow_up_variants=_VARIANTS)
    assert captured["body"]["query"] == "en per collega?"
    assert captured["body"]["max_sources"] == 8, "a selecting turn asks for a source per passage"
    assert captured["body"]["query_variants"] == _VARIANTS, "the visitor's own words stay the primary query"
    assert len(captured["body"]["conversation_history"]) == 3


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
