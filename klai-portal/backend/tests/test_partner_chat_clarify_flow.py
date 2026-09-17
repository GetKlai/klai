"""SPEC-RAG-CLARIFY-FLOW-001 REQ-2 and REQ-3 on the widget path, through the public entries.

Only HTTP is mocked: LiteLLM (answer model and every classifier) and
retrieval-api. The composer, the stripper, the classifier parsing and the
prompt assembly all run for real, because a helper-level test on this boundary
stayed green once already while the reported user path was broken.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx
from helpers import FakeKB, FakeResult, make_partner_auth
from klai_chat_prompts import CLARIFY_TURN_ADDENDUM, no_citable_sources_message

from app.services import partner_chat

LITELLM = "http://litellm:4000"
RETRIEVAL = "http://retrieval-api:8040"
CLARIFYING_QUESTION = "Gaat het om je factuur of om je abonnement?"
REFUSAL_NL = no_citable_sources_message("nl", helpdesk=True)
VISITOR = [{"role": "user", "content": "Ik heb een vraag over mijn rekening, hoe zit dat?"}]


def _settings() -> MagicMock:
    settings = MagicMock()
    settings.litellm_base_url = LITELLM
    settings.litellm_master_key = "key"
    settings.extraction_model = "klai-fast"
    return settings


def _classifier_reply(category: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"category": category})}}]})


class _LiteLLM:
    """One route for every LiteLLM call, told apart by the schema name each classifier sends."""

    def __init__(self, *, model_text: str, answer_claims: Any = "no_claims", turn_scope: str = "organisation"):
        self.model_text = model_text
        self.answer_claims = answer_claims
        self.turn_scope = turn_scope
        self.answer_requests: list[dict] = []
        self.claims_requests: list[dict] = []
        self.scope_requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        schema = (body.get("response_format") or {}).get("json_schema", {}).get("name")
        if schema == "answer_claims":
            self.claims_requests.append(body)
            if isinstance(self.answer_claims, Exception):
                raise self.answer_claims
            if isinstance(self.answer_claims, httpx.Response):
                return self.answer_claims
            return _classifier_reply(self.answer_claims)
        if schema == "escalation_classification":
            reply = {"wants_human": False, "sentiment": "neutral"}
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(reply)}}]})
        if schema == "turn_category":
            self.scope_requests.append(body)
            return _classifier_reply(self.turn_scope)
        self.answer_requests.append(body)
        if body.get("stream"):
            frame = json.dumps({"choices": [{"index": 0, "delta": {"content": self.model_text}}]})
            return httpx.Response(200, content=f"data: {frame}\n\ndata: [DONE]\n\n".encode())
        message = {"role": "assistant", "content": self.model_text}
        return httpx.Response(200, json={"choices": [{"index": 0, "message": message, "finish_reason": "stop"}]})


def _frames(chunks: list[bytes]) -> list[dict]:
    out = []
    for raw in chunks:
        for line in raw.decode().splitlines():
            payload = line.removeprefix("data: ").strip()
            if payload and payload != "[DONE]":
                out.append(json.loads(payload))
    return out


def _delta(frames: list[dict], key: str) -> list[Any]:
    return [c["delta"][key] for f in frames for c in f.get("choices") or [] if key in (c.get("delta") or {})]


async def _answer(litellm: _LiteLLM, *, stream: bool, clarify_flow: bool = True, **overrides) -> tuple[str, dict, dict]:
    """Run one widget turn with no citable source; return (visible text, signals, extras)."""
    signals: dict[str, Any] = {}
    kwargs: dict[str, Any] = {
        "messages": VISITOR,
        "model": "klai-primary",
        "temperature": 0.2,
        "system_prompt": "SUPPORT PROFILE",
        "settings": _settings(),
        "org_id": 42,
        "citation_chunks": [],
        "trusted_sources": [],
        "citation_output": "markers",
        "source_query": "rekening factuur abonnement kosten",
        "support_mode": True,
        "clarify_flow": clarify_flow,
        "answer_signals": signals,
        **overrides,
    }
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{LITELLM}/v1/chat/completions").mock(side_effect=litellm)
        if stream:
            frames = _frames([c async for c in partner_chat.chat_completion_streaming(**kwargs)])
            text = "".join(_delta(frames, "content"))
            extras = {"broad_mode": _delta(frames, "broad_mode"), "escalation": _delta(frames, "escalation")}
        else:
            message = (await partner_chat.chat_completion_non_streaming(**kwargs))["choices"][0]["message"]
            text = message["content"]
            extras = {"broad_mode": [message.get("broad_mode")], "escalation": [message.get("escalation")]}
    return text, signals, extras


# ─── REQ-2: decision 2, after generation ────────────────────────────────


@pytest.mark.parametrize("stream", [True, False])
async def test_clarifying_question_without_claims_reaches_the_visitor(stream):
    litellm = _LiteLLM(model_text=CLARIFYING_QUESTION, answer_claims="no_claims")

    text, signals, extras = await _answer(litellm, stream=stream)

    assert text == CLARIFYING_QUESTION
    assert signals["answer_claims"] == "no_claims"
    assert signals["refused"] is False
    # The classifier judged the reply to the visitor's own words, not the rewritten search query.
    classifier_input = litellm.claims_requests[0]["messages"][1]["content"]
    assert VISITOR[0]["content"] in classifier_input
    assert "rekening factuur abonnement kosten" not in classifier_input
    # The refusal's flags stay with the passed reply, as the spec requires.
    assert extras["broad_mode"] == ["offer"]
    assert extras["escalation"] == [{"appointment": True}]


@pytest.mark.parametrize("stream", [True, False])
async def test_reply_with_claims_keeps_the_fixed_refusal(stream):
    litellm = _LiteLLM(model_text="Een factuur kost bij ons 5 euro per maand.", answer_claims="claims")

    text, signals, _ = await _answer(litellm, stream=stream)

    assert text == REFUSAL_NL
    assert signals["answer_claims"] == "claims"
    assert signals["refused"] is True


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ReadTimeout("classifier too slow"),
        httpx.Response(500, json={"error": "upstream"}),
        httpx.Response(200, json={"choices": [{"message": {"content": "no_claims, I think"}}]}),
    ],
    ids=["timeout", "http_500", "garbage_json"],
)
async def test_classifier_failure_keeps_the_fixed_refusal(failure):
    litellm = _LiteLLM(model_text=CLARIFYING_QUESTION, answer_claims=failure)

    text, signals, _ = await _answer(litellm, stream=False)

    assert text == REFUSAL_NL
    assert signals["answer_claims"] == "classifier_failed"
    assert signals["refused"] is True


async def test_passed_reply_is_stripped_of_links_and_citations():
    litellm = _LiteLLM(
        model_text="Kijk eventueel op https://evil.example.com/phish of www.voys.nl/hulp [1]. " + CLARIFYING_QUESTION,
        answer_claims="no_claims",
    )

    text, _, _ = await _answer(litellm, stream=True)

    assert CLARIFYING_QUESTION in text
    for artifact in ("http", "evil.example", "voys.nl", "[1]"):
        assert artifact not in text
    # The classifier saw the stripped text, i.e. exactly what the visitor gets.
    assert "evil.example" not in litellm.claims_requests[0]["messages"][1]["content"]


@pytest.mark.parametrize("stream", [True, False])
async def test_switched_off_never_classifies_and_refuses_as_today(stream):
    litellm = _LiteLLM(model_text=CLARIFYING_QUESTION, answer_claims="no_claims")

    text, signals, _ = await _answer(litellm, stream=stream, clarify_flow=False)

    assert text == REFUSAL_NL
    assert litellm.claims_requests == []
    assert "answer_claims" not in signals


# ─── REQ-3: decision 1, before generation, through the route ────────────


def _retrieval_reply(band: str, chunk_text: str, reranker: float | None = None) -> httpx.Response:
    item = {
        "chunk_id": "c1",
        "evidence_id": "ev1",
        "title": "Nummer porteren",
        "text": chunk_text,
        "source_url": "https://help.example.com/porteren",
        "reranker_score": reranker if reranker is not None else (0.9 if band == "high" else 0.05),
    }
    source = {"source_url": item["source_url"], "title": item["title"], "evidence_ids": ["ev1"]}
    return httpx.Response(200, json={"confidence_band": band, "evidence_pack": {"items": [item], "sources": [source]}})


async def _route_turn(
    monkeypatch,
    *,
    question: str,
    band: str,
    chunk_text: str,
    clarify_unlocked: bool,
    turn_scope: str = "organisation",
    stream: bool = False,
    reranker: float | None = None,
    answer_claims: str = "no_claims",
) -> tuple[_LiteLLM, str]:
    """Drive the partner route end to end; return the LiteLLM recorder and the visitor-visible text."""
    from app.api import partner
    from app.api.partner import ChatCompletionsRequest, chat_completions

    for name, value in {
        "litellm_base_url": LITELLM,
        "litellm_master_key": "key",
        "extraction_model": "klai-fast",
        "knowledge_retrieve_url": RETRIEVAL,
        "retrieval_api_internal_secret": "secret",
    }.items():
        monkeypatch.setattr(partner.settings, name, value)

    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeResult(rows=[FakeKB(id=10, name="KB", slug="kb-a", org_id=42)]))
    auth = make_partner_auth(kb_access={10: "read"})
    auth.key_id = "wgt_901"
    request = ChatCompletionsRequest(messages=[{"role": "user", "content": question}], stream=stream)
    http_request = MagicMock(headers={}, client=MagicMock(host="127.0.0.1"))

    litellm = _LiteLLM(model_text=CLARIFYING_QUESTION, turn_scope=turn_scope, answer_claims=answer_claims)
    with (
        respx.mock(assert_all_called=False) as router,
        # Database readers and fire-and-forget audit/gap writes: not HTTP, not under test.
        patch("app.api.partner._widget_page_context_enabled", new=AsyncMock(return_value=False)),
        patch("app.api.partner._widget_support_mode_enabled", new=AsyncMock(return_value=True)),
        patch("app.api.partner._widget_tone_register", new=AsyncMock(return_value="restrained")),
        patch("app.api.partner._clarify_flow_enabled", new=AsyncMock(return_value=clarify_unlocked)),
        patch("app.api.partner.asyncio"),
        patch("app.api.partner.write_retrieval_log", new=AsyncMock()),
        patch("app.services.partner_chat._schedule_gap_event"),
    ):
        router.post(f"{LITELLM}/v1/chat/completions").mock(side_effect=litellm)
        router.post(f"{RETRIEVAL}/retrieve").mock(return_value=_retrieval_reply(band, chunk_text, reranker))
        response = await chat_completions(request=request, http_request=http_request, auth=auth, db=db)
        if stream:
            text = "".join(_delta(_frames([chunk async for chunk in response.body_iterator]), "content"))
        else:
            text = response["choices"][0]["message"]["content"]
    return litellm, text


def _system_prompt_sent(litellm: _LiteLLM) -> str:
    (answer_request,) = litellm.answer_requests
    return "\n".join(m["content"] for m in answer_request["messages"] if m["role"] == "system")


UNRELATED_CHUNK = "Je neemt je nummer mee door het porteringsformulier in te vullen."


@pytest.mark.parametrize("band", ["low", "unknown"])
async def test_weak_retrieval_without_direct_evidence_asks_a_clarifying_question(monkeypatch, band):
    litellm, _ = await _route_turn(
        monkeypatch, question="prijzen?", band=band, chunk_text=UNRELATED_CHUNK, clarify_unlocked=True
    )

    assert CLARIFY_TURN_ADDENDUM["external"] in _system_prompt_sent(litellm)


async def test_confident_retrieval_gets_no_clarify_instruction(monkeypatch):
    litellm, _ = await _route_turn(
        monkeypatch, question="prijzen?", band="high", chunk_text=UNRELATED_CHUNK, clarify_unlocked=True
    )

    assert CLARIFY_TURN_ADDENDUM["external"] not in _system_prompt_sent(litellm)


async def test_conversational_turn_gets_no_clarify_instruction(monkeypatch):
    litellm, _ = await _route_turn(
        monkeypatch,
        question="dankjewel!",
        band="low",
        chunk_text=UNRELATED_CHUNK,
        clarify_unlocked=True,
        turn_scope="conversation",
    )

    assert CLARIFY_TURN_ADDENDUM["external"] not in _system_prompt_sent(litellm)


async def test_switched_off_route_gets_no_clarify_instruction(monkeypatch):
    litellm, _ = await _route_turn(
        monkeypatch, question="prijzen?", band="low", chunk_text=UNRELATED_CHUNK, clarify_unlocked=False
    )

    assert CLARIFY_TURN_ADDENDUM["external"] not in _system_prompt_sent(litellm)
    assert litellm.claims_requests == []


async def test_the_switch_is_the_tenant_unlock_scoped_to_the_caller_org():
    from app.api.partner import _clarify_flow_enabled

    auth = make_partner_auth()
    unlocked, default = AsyncMock(), AsyncMock()
    unlocked.execute = AsyncMock(return_value=FakeResult(rows=[["widgets", "widget_clarify_flow"]]))
    default.execute = AsyncMock(return_value=FakeResult(rows=[["partner_api", "scribe", "widgets"]]))

    assert await _clarify_flow_enabled(auth, unlocked) is True
    assert await _clarify_flow_enabled(auth, default) is False
    query = str(unlocked.execute.call_args.args[0].compile(compile_kwargs={"literal_binds": True}))
    assert "FROM portal_orgs" in query
    assert f"WHERE portal_orgs.id = {auth.org_id}" in query


# ─── Review round: route-level decision 2, conversational gate, late scope ──


@pytest.mark.parametrize("stream", [True, False])
async def test_route_with_unlock_shows_a_clarifying_question_classified_no_claims(monkeypatch, stream):
    litellm, text = await _route_turn(
        monkeypatch,
        question="prijzen?",
        band="low",
        chunk_text=UNRELATED_CHUNK,
        clarify_unlocked=True,
        stream=stream,
    )

    assert text == CLARIFYING_QUESTION
    (claims_request,) = litellm.claims_requests
    # The article titles that were in the prompt reach the classifier.
    assert "- Nummer porteren" in claims_request["messages"][1]["content"]


@pytest.mark.parametrize(("category", "shown"), [("claims", False), ("no_claims", True)])
async def test_conversational_reply_goes_through_the_claims_gate(category, shown):
    litellm = _LiteLLM(model_text="Wij rekenen 5 euro." if not shown else "Graag gedaan!", answer_claims=category)

    text, signals, _ = await _answer(litellm, stream=False, conversational=True)

    assert len(litellm.claims_requests) == 1
    assert signals["answer_claims"] == category
    if shown:
        assert text == "Graag gedaan!"
        assert signals["refused"] is False
    else:
        assert text == REFUSAL_NL
        assert signals["refused"] is True


async def test_conversational_reply_is_not_classified_while_switched_off():
    litellm = _LiteLLM(model_text="Wij rekenen 5 euro.", answer_claims="claims")

    text, _, _ = await _answer(litellm, stream=True, clarify_flow=False, conversational=True)

    assert text == "Wij rekenen 5 euro."
    assert litellm.claims_requests == []


async def test_broad_mode_answer_is_never_classified():
    litellm = _LiteLLM(model_text="DECT is een standaard voor draadloze telefonie.", answer_claims="claims")

    text, _, _ = await _answer(litellm, stream=True, broad_mode=True)

    assert "DECT is een standaard" in text
    assert litellm.claims_requests == []


async def test_safety_blocked_turn_is_never_classified(monkeypatch):
    monkeypatch.setattr(partner_chat, "output_safety_violation", lambda text: "prompt_injection")
    litellm = _LiteLLM(model_text=CLARIFYING_QUESTION, answer_claims="no_claims")

    await _answer(litellm, stream=True)

    assert litellm.claims_requests == []


async def test_confident_reranker_with_low_band_still_classifies_scope_before_clarifying(monkeypatch):
    litellm, _ = await _route_turn(
        monkeypatch,
        question="dankjewel!",
        band="low",
        reranker=0.9,
        chunk_text=UNRELATED_CHUNK,
        clarify_unlocked=True,
        turn_scope="conversation",
    )

    assert len(litellm.scope_requests) == 1
    assert CLARIFY_TURN_ADDENDUM["external"] not in _system_prompt_sent(litellm)
