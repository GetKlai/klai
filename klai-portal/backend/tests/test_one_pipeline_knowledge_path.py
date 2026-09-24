"""One-chat-pipeline slice 4: the knowledge path does for the internal
profile what the LiteLLM hook does today (modes, scope with identity,
retrieval parameters, sub-question fan-out, query rewrite, trivial gate,
Strict-risk model choice), while a widget single-question turn stays
byte-identical.

Every test drives ``chat_completions`` end to end with one HTTP double that
records each POST by URL, so an assertion reads what actually left portal-api:
the ``/retrieve`` body and the messages sent to LiteLLM.

synthetic-data: generator=hand-written seed=0 (fictional org "Acme Telecom",
example.com URLs, invented KB slugs).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.partner_dependencies import PartnerAuthContext
from app.services.chat_profile import ChatProfile

GOLDEN_PATH = Path(__file__).parent / "fixtures" / "widget_knowledge_golden.json"
ANSWER = "Je reset je wachtwoord via de inlogpagina."


def _evidence_pack(n_items: int = 1, *, score: float = 0.82) -> dict:
    return {
        "items": [
            {
                "chunk_id": f"chunk-{i}",
                "evidence_id": f"ev-{i}",
                "text": "Je reset je wachtwoord via de inlogpagina onder 'Wachtwoord vergeten'.",
                "title": "Wachtwoord resetten",
                "source_url": f"https://help.example.com/wachtwoord-{i}",
                "reranker_score": score,
                "final_score": score,
            }
            for i in range(n_items)
        ],
        "sources": [
            {"url": f"https://help.example.com/wachtwoord-{i}", "title": "Wachtwoord resetten"} for i in range(n_items)
        ],
    }


class _Resp:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _Recorder:
    """httpx.AsyncClient double: records POST bodies by URL suffix."""

    def __init__(self, retrieve_payload: dict | Exception, model_text: str = ANSWER) -> None:
        self.retrieve_payload = retrieve_payload
        self.model_text = model_text
        self.retrieve_bodies: list[dict] = []
        self.model_bodies: list[dict] = []

    def factory(self, *_: Any, **__: Any) -> _Recorder:
        return self

    async def __aenter__(self) -> _Recorder:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def post(self, url: str, *, json: dict, headers: dict | None = None) -> _Resp:
        if url.endswith("/retrieve"):
            self.retrieve_bodies.append(json)
            if isinstance(self.retrieve_payload, Exception):
                raise self.retrieve_payload
            return _Resp(self.retrieve_payload)
        self.model_bodies.append(json)
        return _Resp(
            {
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "model": json["model"],
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": self.model_text}, "finish_reason": "stop"}
                ],
            }
        )


def _auth(*, key_id: str, permissions: dict | None = None, kb_access: dict | None = None) -> PartnerAuthContext:
    return PartnerAuthContext(
        key_id=key_id,
        org_id=7,
        zitadel_org_id="zorg-acme",
        permissions=permissions or {"chat": True},
        kb_access=kb_access if kb_access is not None else {10: "read"},
        rate_limit_rpm=60,
    )


@pytest.fixture
def pipeline(monkeypatch):
    """Patch everything around chat_completions that is not the knowledge path."""
    import app.api.partner as partner
    import app.services.partner_chat as partner_chat

    monkeypatch.setattr(partner.settings, "knowledge_retrieve_url", "http://retrieval.example.com")
    monkeypatch.setattr(partner.settings, "litellm_base_url", "http://litellm.example.com")
    monkeypatch.setattr(partner, "_resolve_kb_slugs", AsyncMock(return_value=["handboek"]))
    monkeypatch.setattr(partner, "_widget_system_prompt", AsyncMock(return_value=None))
    monkeypatch.setattr(partner, "_widget_page_context_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr(partner, "_widget_support_mode_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr(partner, "write_retrieval_log", AsyncMock())
    monkeypatch.setattr(partner_chat, "_schedule_gap_event", MagicMock())

    def run(recorder: _Recorder):
        monkeypatch.setattr(partner_chat.httpx, "AsyncClient", recorder.factory)
        return recorder

    return run


async def _chat(
    auth: PartnerAuthContext,
    profile: ChatProfile,
    messages: list[dict],
    *,
    model: str = "klai-primary",
) -> Any:
    from app.api.partner import ChatCompletionsRequest, chat_completions

    db = AsyncMock()
    widget_row = MagicMock()
    widget_row.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=widget_row)
    request = ChatCompletionsRequest(messages=messages, model=model, stream=False)
    return await chat_completions(request=request, http_request=MagicMock(), auth=auth, db=db, profile=profile)


def _system_prompt(recorder: _Recorder) -> str:
    return recorder.model_bodies[-1]["messages"][0]["content"]


# --- widget stays byte-identical ------------------------------------------------


@pytest.mark.asyncio
async def test_widget_single_question_retrieve_body_and_prompt_match_the_golden(pipeline):
    recorder = pipeline(_Recorder({"evidence_pack": _evidence_pack(), "confidence_band": "high"}))

    await _chat(
        _auth(key_id="wgt_golden"),
        ChatProfile(surface="widget"),
        [{"role": "user", "content": "Hoe reset ik mijn wachtwoord?"}],
    )

    golden = json.loads(GOLDEN_PATH.read_text())
    assert recorder.retrieve_bodies == [golden["retrieve_body"]]
    assert _system_prompt(recorder) == golden["system_prompt"]
