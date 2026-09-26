"""Background and utility LiteLLM calls must not escalate to klai-medium.

deploy/litellm/config.yaml routes a saturated klai-fast or klai-primary to
klai-medium. That fallback exists for interactive chat latency; a judge,
rewrite, summary or triage call that silently lands on klai-medium pays its
price. Each call below sends ``fallbacks: []`` to opt out per request.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from pydantic import BaseModel


class _CaptureClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.bodies: list[dict] = []

    def __call__(self, *_: Any, **__: Any) -> _CaptureClient:
        return self

    async def __aenter__(self) -> _CaptureClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def post(self, *_: Any, json: dict, **__: Any) -> MagicMock:
        self.bodies.append(json)
        response = MagicMock()
        response.json.return_value = {"choices": [{"message": {"content": self.content}}]}
        return response


def _settings() -> MagicMock:
    return MagicMock(
        litellm_base_url="http://litellm.example.com", litellm_master_key="k", extraction_model="klai-fast"
    )


@pytest.mark.asyncio
async def test_structured_judge_call_sends_no_fallbacks(monkeypatch) -> None:
    from app.services import turn_judge

    class _Verdict(BaseModel):
        ok: bool

    client = _CaptureClient('{"ok": true}')
    monkeypatch.setattr(httpx, "AsyncClient", client)

    result = await turn_judge.structured_judge_call(
        name="probe", system_prompt="s", user_content="u", schema=_Verdict, timeout_seconds=1.0, settings=_settings()
    )

    assert result == _Verdict(ok=True)
    assert client.bodies[0]["fallbacks"] == []


@pytest.mark.asyncio
async def test_query_rewrite_sends_no_fallbacks(monkeypatch) -> None:
    from app.services import query_rewrite

    client = _CaptureClient("Hoe stel ik voicemail in?")
    monkeypatch.setattr(httpx, "AsyncClient", client)

    await query_rewrite.rewrite_for_retrieval(
        "Hoe stel ik voicemail in?",
        [],
        zitadel_org_id="zorg-acme",
        kb_slugs=[],
        pasted_correspondence=False,
        settings=_settings(),
    )

    assert client.bodies[0]["fallbacks"] == []


@pytest.mark.asyncio
async def test_summarizer_sends_no_fallbacks(monkeypatch) -> None:
    from app.services import summarizer

    client = _CaptureClient("summary")
    monkeypatch.setattr(httpx, "AsyncClient", client)

    await summarizer._call_llm("system", "user", "klai-fast")

    assert client.bodies[0]["fallbacks"] == []


@pytest.mark.asyncio
async def test_feedback_triage_sends_no_fallbacks(monkeypatch) -> None:
    from app.klai_feedback import triage

    client = _CaptureClient("{}")
    monkeypatch.setattr(httpx, "AsyncClient", client)

    await triage._call_triage_llm(model="klai-fast", user="u")

    assert client.bodies[0]["fallbacks"] == []
