"""The internal chat's query rewrite, moved from deploy/litellm/klai_kb_query_rewrite.py
(one-chat-pipeline slice 4). Pins the three outcomes retrieval depends on: a
rewrite that drops the question's subject is reverted, an infrastructure
failure leaves coreference to retrieval-api, and a distilled pasted email loses
its per-incident numbers but keeps reusable codes. All fixtures synthetic.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from app.services import query_rewrite


class _Client:
    def __init__(self, reply: str | Exception) -> None:
        self.reply = reply

    def __call__(self, *_: Any, **__: Any) -> _Client:
        return self

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def post(self, *_: Any, **__: Any) -> MagicMock:
        if isinstance(self.reply, Exception):
            raise self.reply
        response = MagicMock()
        response.json.return_value = {"choices": [{"message": {"content": self.reply}}]}
        return response


async def _rewrite(monkeypatch, reply: str | Exception, query: str, *, pasted: bool = False):
    monkeypatch.setattr(query_rewrite.httpx, "AsyncClient", _Client(reply))
    settings = MagicMock(litellm_base_url="http://litellm.example.com", litellm_master_key="k")
    return await query_rewrite.rewrite_for_retrieval(
        query, [], zitadel_org_id="zorg-acme", kb_slugs=[], pasted_correspondence=pasted, settings=settings
    )


@pytest.mark.asyncio
async def test_rewrite_that_drops_the_subject_is_reverted_but_counts_as_decided(monkeypatch):
    result = await _rewrite(monkeypatch, "Yealink toestel instellen", "Wat weet je over de factuurrun?")

    assert result.query == "Wat weet je over de factuurrun?"
    assert result.coreference_resolved is True


@pytest.mark.asyncio
async def test_failed_rewrite_keeps_the_raw_query_and_leaves_coreference_to_retrieval(monkeypatch):
    result = await _rewrite(monkeypatch, httpx.ReadTimeout("slow"), "Hoe stel ik het in?")

    assert result.query == "Hoe stel ik het in?"
    assert result.coreference_resolved is False


@pytest.mark.asyncio
async def test_distilled_correspondence_drops_incident_numbers_and_keeps_error_codes(monkeypatch):
    result = await _rewrite(
        monkeypatch,
        "**SIP trunk** 451030015 registratie mislukt error 10060",
        "Trunk 451030015 registratie mislukt met error 10060, kun je helpen?",
        pasted=True,
    )

    assert result.query == "SIP trunk registratie mislukt error 10060"
