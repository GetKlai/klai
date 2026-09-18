"""SPEC-RAG-ANSWER-JUDGES-001 REQ-1 to REQ-4 on the widget path, through the public entries.

Only HTTP is mocked: LiteLLM (answer model and both judges) and retrieval-api.
The composer, the stripper, the judge parsing, the decision function and the
prompt assembly all run for real, because a helper-level test on this boundary
stayed green once already while the reported user path was broken.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx
from helpers import FakeKB, FakeResult, make_partner_auth
from klai_chat_prompts import no_citable_sources_message

from app.services import partner_chat, turn_judge

LITELLM = "http://litellm:4000"
RETRIEVAL = "http://retrieval-api:8040"
CLARIFYING_QUESTION = "Gaat het om je factuur of om je abonnement?"
REFUSAL_NL = no_citable_sources_message("nl", helpdesk=True)
VISITOR = [{"role": "user", "content": "Ik heb een vraag over mijn rekening, hoe zit dat?"}]

# Conversation #900, turn 1: the article shares "factuur" and "incasso" with the
# question, and the composer cites it, but it does not say how to reverse one.
QUESTION_900 = "Mijn factuur is betaald en ook geïncasseerd, hoe kan ik die incasso storneren?"
ANSWER_900 = "Je betaalt je factuur via automatische incasso rond de 25e van de maand."
CHUNK_900 = {
    "chunk_id": "c1",
    "evidence_id": "ev1",
    "title": "Factuur betalen",
    "text": "Je betaalt je factuur via automatische incasso. De incasso vindt plaats rond de 25e van de maand.",
    "source_url": "https://help.example.com/factuur",
    "reranker_score": 0.08,
}
# A ninth-article, 6000-character case: the answer model receives whole parent
# chunks, so a checker that clips would call their tail "not in the articles".
LONG_CHUNK = {
    "chunk_id": "c9",
    "evidence_id": "ev9",
    "title": "Automatische incasso instellen",
    "text": "Stap voor stap. " * 380 + "De incasso loopt op de laatste werkdag van de maand.",
    "source_url": "https://help.example.com/incasso",
    "reranker_score": 0.05,
}
SOURCES_900 = [
    {"label": "1", "title": "Factuur betalen", "url": "https://help.example.com/factuur", "evidence_ids": ["ev1"]}
]


def _settings() -> MagicMock:
    settings = MagicMock()
    settings.litellm_base_url = LITELLM
    settings.litellm_master_key = "key"
    settings.extraction_model = "klai-fast"
    settings.answer_grounding_model = "klai-medium"
    return settings


def _json_reply(payload: dict) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload)}}]})


def _answer_verdict(verdict: str = "answered", *, claims: bool = False) -> dict:
    return {"verdict": verdict, "grounding": "some_not_in_articles" if claims else "all_in_articles"}


def _grounding(*unsupported: str, supported: tuple[str, ...] = (), contradicted: bool = False) -> dict:
    """The statement-level check: every statement with the article text behind it."""
    support = "contradicted" if contradicted else "not_in_articles"
    return {
        "statements": [
            *({"statement": item, "evidence": "staat in het artikel", "support": "supported"} for item in supported),
            *({"statement": item, "evidence": "", "support": support} for item in unsupported),
        ]
    }


def _turn_verdict(**overrides: Any) -> dict:
    return {
        "topic": "handled",
        "scope": "organisation",
        "wants_human": False,
        "sentiment": "neutral",
        "clarity": "clear",
        "missing": "",
        **overrides,
    }


class _LiteLLM:
    """One route for every LiteLLM call, told apart by the schema name each judge sends."""

    def __init__(
        self,
        *,
        model_text: str,
        answer_judge: Any = None,
        turn: dict | None = None,
        grounding: Any = None,
        repaired: str = "",
    ):
        self.model_text = model_text
        self.answer_judge = answer_judge if answer_judge is not None else _answer_verdict()
        self.turn = turn or _turn_verdict()
        # The statement-level check decides grounding, so by default it mirrors
        # the light judge's label: a test that says claims=True gets one
        # unsupported statement, anything else a fully supported draft.
        if grounding is None:
            light = self.answer_judge if isinstance(self.answer_judge, dict) else {}
            grounding = (
                _grounding(model_text)
                if light.get("grounding") == "some_not_in_articles"
                else _grounding(supported=(model_text,))
            )
        self.grounding = grounding
        self.repaired = repaired
        self.answer_requests: list[dict] = []
        self.judge_requests: list[dict] = []
        self.turn_requests: list[dict] = []
        self.grounding_requests: list[dict] = []
        self.repair_requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        schema = (body.get("response_format") or {}).get("json_schema", {}).get("name")
        if schema == "answer_judge":
            self.judge_requests.append(body)
            if isinstance(self.answer_judge, Exception):
                raise self.answer_judge
            if isinstance(self.answer_judge, httpx.Response):
                return self.answer_judge
            return _json_reply(self.answer_judge)
        if schema == "turn_judge":
            self.turn_requests.append(body)
            return _json_reply(self.turn)
        if schema == "grounding_check":
            self.grounding_requests.append(body)
            if isinstance(self.grounding, Exception):
                raise self.grounding
            return _json_reply(self.grounding)
        if "You edit a reply" in (body.get("messages") or [{}])[0].get("content", ""):
            self.repair_requests.append(body)
            return httpx.Response(200, json={"choices": [{"message": {"content": self.repaired}}]})
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


async def _answer(litellm: _LiteLLM, *, stream: bool, **overrides) -> tuple[str, dict, dict]:
    """Run one widget turn; return (visible text, signals, extras). No citable source unless overridden."""
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
        "answer_signals": signals,
        **overrides,
    }
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{LITELLM}/v1/chat/completions").mock(side_effect=litellm)
        if stream:
            frames = _frames([c async for c in partner_chat.chat_completion_streaming(**kwargs)])
            text = "".join(_delta(frames, "content"))
            extras = {
                "broad_mode": _delta(frames, "broad_mode"),
                "escalation": _delta(frames, "escalation"),
                "sources": [s for batch in _delta(frames, "sources") for s in batch],
            }
        else:
            message = (await partner_chat.chat_completion_non_streaming(**kwargs))["choices"][0]["message"]
            text = message["content"]
            extras = {
                "broad_mode": [message["broad_mode"]] if "broad_mode" in message else [],
                "escalation": [message["escalation"]] if "escalation" in message else [],
                "sources": message.get("sources") or [],
            }
    return text, signals, extras


def _with_900_sources() -> dict[str, Any]:
    return {
        "messages": [{"role": "user", "content": QUESTION_900}],
        "citation_chunks": [CHUNK_900, LONG_CHUNK],
        "trusted_sources": SOURCES_900,
        "source_query": "factuur incasso storneren",
    }


# ─── The decision table, per acceptance case ────────────────────────────


@pytest.mark.parametrize("stream", [True, False])
@pytest.mark.parametrize("verdict", ["not_answered", "partial"])
async def test_a_judge_that_doubts_an_answer_with_sources_adds_the_button_and_never_removes_it(stream, verdict):
    # Replayed on nine real Voys questions on 2026-09-17, letting this verdict
    # remove answers with sources produced 7 "not found" out of 18 answers. Only
    # a statement the articles do not support changes the text, and then it is
    # repaired rather than removed (see the grounding tests below).
    litellm = _LiteLLM(model_text=ANSWER_900, answer_judge=_answer_verdict(verdict))

    text, signals, extras = await _answer(litellm, stream=stream, clarity="clear", **_with_900_sources())

    assert text == ANSWER_900
    assert [s["url"] for s in extras["sources"]] == ["https://help.example.com/factuur"]
    assert extras["escalation"] == [{"appointment": True}]
    assert signals["decision"] == "partial_answer"
    assert signals["refused"] is False
    # The judge read the visitor's own question and the article the model had.
    judge_input = litellm.judge_requests[0]["messages"][1]["content"]
    assert "Visitor (LATEST message): " + QUESTION_900 in judge_input
    assert "### Factuur betalen" in judge_input


@pytest.mark.parametrize("stream", [True, False])
async def test_answered_draft_with_sources_is_shown_with_its_sources(stream):
    litellm = _LiteLLM(model_text=ANSWER_900, answer_judge=_answer_verdict("answered"))

    text, signals, extras = await _answer(litellm, stream=stream, **_with_900_sources())

    assert text == ANSWER_900
    assert [s["url"] for s in extras["sources"]] == ["https://help.example.com/factuur"]
    assert extras["escalation"] == []
    assert signals["decision"] == "answer"


async def test_partial_answer_keeps_its_sources_and_gets_the_appointment_button():
    litellm = _LiteLLM(model_text=ANSWER_900, answer_judge=_answer_verdict("partial"))

    text, signals, extras = await _answer(litellm, stream=True, **_with_900_sources())

    assert text == ANSWER_900
    assert extras["sources"]
    assert extras["escalation"] == [{"appointment": True}]
    assert signals["decision"] == "partial_answer"


@pytest.mark.parametrize("stream", [True, False])
async def test_ambiguous_turn_shows_the_clarifying_question_without_buttons(stream):
    litellm = _LiteLLM(model_text=CLARIFYING_QUESTION, answer_judge=_answer_verdict("not_answered"))

    text, signals, extras = await _answer(litellm, stream=stream, clarity="ambiguous")

    assert text == CLARIFYING_QUESTION
    assert extras == {"broad_mode": [], "escalation": [], "sources": []}
    assert signals["decision"] == "clarifying_question"
    assert signals["refused"] is False


# ─── The statement-level grounding check and its repair ─────────────────


@pytest.mark.parametrize("stream", [True, False])
async def test_an_answer_with_an_unsupported_statement_is_repaired_not_refused(stream):
    """Measured on 150 real answers: editing took answers with an unsupported
    statement from 49% to 11% and cost no good answer, where refusing them cost
    seven answers out of eighteen in an earlier round."""
    litellm = _LiteLLM(
        model_text=ANSWER_900 + " Bel 020-7001234 voor een terugboeking.",
        grounding=_grounding("Bel 020-7001234 voor een terugboeking.", contradicted=True, supported=(ANSWER_900,)),
        repaired=ANSWER_900,
    )

    text, signals, extras = await _answer(litellm, stream=stream, **_with_900_sources())

    # The supported sentence survives whole; only the flagged one is gone.
    assert text == ANSWER_900
    assert "020-7001234" not in text
    assert [s["url"] for s in extras["sources"]] == ["https://help.example.com/factuur"]
    assert extras["escalation"] == [{"appointment": True}]
    assert signals["unsupported"] == 1
    assert signals["repaired"] is True
    # The check reads every article the model received, whole: support that sits
    # deep in a long article must not read as "not in the articles".
    check_input = litellm.grounding_requests[0]["messages"][1]["content"]
    assert CHUNK_900["text"] in check_input
    assert LONG_CHUNK["text"] in check_input
    assert LONG_CHUNK["text"][-40:] in check_input


async def test_a_single_flag_leaves_the_answer_alone():
    """One flag is right 77% of the time, two or a contradiction 92%.

    Repairing on a single flag made the original answer win 8 of 11 blind
    comparisons on real answers, because the checker also flags a sentence that
    only restates the visitor's situation.
    """
    litellm = _LiteLLM(
        model_text=ANSWER_900,
        grounding=_grounding("Je betaalt je factuur via automatische incasso.", supported=("rond de 25e",)),
        repaired="",
    )

    text, _, extras = await _answer(litellm, stream=True, **_with_900_sources())

    assert text == ANSWER_900
    assert extras["sources"]
    assert litellm.repair_requests == []


async def test_a_reply_that_is_entirely_unsupported_falls_back_to_the_refusal():
    draft = (
        "Je betaalt je factuur via automatische incasso. Bel 020-7001234 om te storneren. "
        "Het bedrag staat binnen 3 werkdagen terug."
    )
    litellm = _LiteLLM(
        model_text=draft,
        grounding=_grounding(
            "Bel 020-7001234 om te storneren.",
            "Het bedrag staat binnen 3 werkdagen terug.",
        ),
        repaired="NOTHING_LEFT",
    )

    text, signals, extras = await _answer(litellm, stream=True, **_with_900_sources())

    assert len(litellm.repair_requests) == 1
    assert text == REFUSAL_NL
    assert extras["sources"] == []
    assert signals["refused"] is True


@pytest.mark.parametrize("stream", [True, False])
async def test_a_repair_cannot_smuggle_a_link_past_the_stripper(stream):
    """The repair model returns free text, so it passes the same guards the
    composer's output passed. A prompt that forbids URLs is not a guarantee."""
    litellm = _LiteLLM(
        model_text=ANSWER_900 + " Bel 020-7001234 voor een terugboeking.",
        grounding=_grounding("Bel 020-7001234 voor een terugboeking.", contradicted=True, supported=(ANSWER_900,)),
        repaired=ANSWER_900 + " Zie https://evil.example.com/phish en [1].",
    )

    text, _, _ = await _answer(litellm, stream=stream, **_with_900_sources())

    assert "evil.example.com" not in text
    assert "[1]" not in text


async def test_an_empty_repair_keeps_the_answer_the_visitor_would_have_had():
    litellm = _LiteLLM(
        model_text=ANSWER_900 + " Bel 020-7001234 voor een terugboeking.",
        grounding=_grounding("Bel 020-7001234 voor een terugboeking.", contradicted=True, supported=(ANSWER_900,)),
        repaired="   ",
    )

    text, _, extras = await _answer(litellm, stream=True, **_with_900_sources())

    assert text.startswith(ANSWER_900)
    assert extras["sources"]


async def test_a_failed_grounding_check_leaves_the_answer_alone():
    """Fail direction: what the visitor got before this check existed."""
    litellm = _LiteLLM(model_text=ANSWER_900, grounding=httpx.ConnectError("boom"))

    text, signals, extras = await _answer(litellm, stream=True, **_with_900_sources())

    assert text == ANSWER_900
    assert extras["sources"]
    assert signals["judge_failed"] == ["grounding"]
    assert litellm.repair_requests == []


async def test_a_clarifying_question_is_not_repaired():
    litellm = _LiteLLM(
        model_text=CLARIFYING_QUESTION,
        answer_judge=_answer_verdict("not_answered"),
        grounding=_grounding("Gaat het om je factuur of om je abonnement?"),
    )

    text, _, _ = await _answer(litellm, stream=True, clarity="ambiguous")

    # An unsupported "statement" inside a question is the checker overreaching;
    # the decision table already refuses a question that carries a claim.
    assert text in (CLARIFYING_QUESTION, REFUSAL_NL)
    assert litellm.repair_requests == []


async def test_a_turn_wrongly_called_conversational_keeps_its_sources():
    """The composer used to skip the citation firewall on a conversational turn.

    Measured on 90 real Voys follow-ups on 2026-09-17: the class fired 11 times,
    at least 3 of them wrong, and one real question ("hoe kan ik kijken of er
    ergens een doorschakeling in zit?") came back as the fixed refusal because
    its sources were dropped.
    """
    litellm = _LiteLLM(model_text=ANSWER_900, answer_judge=_answer_verdict("answered"))

    text, signals, extras = await _answer(litellm, stream=True, conversational=True, **_with_900_sources())

    assert text == ANSWER_900
    assert [s["url"] for s in extras["sources"]] == ["https://help.example.com/factuur"]
    assert signals["decision"] == "answer"


@pytest.mark.parametrize("clarity", ["clear", "ambiguous"])
async def test_uncited_draft_without_claims_is_shown_as_before_the_judges(clarity):
    # The original claims rule: text without a source that states nothing about
    # the organisation reaches the visitor. Only an ambiguous turn whose draft
    # ends on a question is labelled a clarifying question.
    litellm = _LiteLLM(model_text="Dat kan ik niet vinden.", answer_judge=_answer_verdict("not_answered"))

    text, signals, _ = await _answer(litellm, stream=True, clarity=clarity)

    assert text == "Dat kan ik niet vinden."
    assert signals["decision"] == "answer"


async def test_ambiguous_turn_whose_question_carries_a_claim_is_refused():
    litellm = _LiteLLM(
        model_text="Bedoel je je abonnement van 5 euro per maand?",
        answer_judge=_answer_verdict("not_answered", claims=True),
    )

    text, _, _ = await _answer(litellm, stream=True, clarity="ambiguous")

    assert text == REFUSAL_NL


@pytest.mark.parametrize(("claims", "shown"), [(False, True), (True, False)])
async def test_uncited_answer_is_shown_only_without_unsupported_claims(claims, shown):
    litellm = _LiteLLM(model_text="Graag gedaan, fijne dag!", answer_judge=_answer_verdict("answered", claims=claims))

    text, signals, extras = await _answer(litellm, stream=True)

    assert text == ("Graag gedaan, fijne dag!" if shown else REFUSAL_NL)
    assert (signals["grounding"] == "some_not_in_articles") is claims
    if shown:
        # An answer is not a refusal, so no "broaden the search" offer under it.
        assert extras["broad_mode"] == []


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ReadTimeout("judge too slow"),
        httpx.Response(500, json={"error": "upstream"}),
        httpx.Response(200, json={"choices": [{"message": {"content": "answered, I think"}}]}),
    ],
    ids=["timeout", "http_500", "garbage_json"],
)
@pytest.mark.parametrize("with_sources", [True, False])
async def test_failed_answer_judge_shows_the_composed_answer_only_with_sources(failure, with_sources):
    litellm = _LiteLLM(model_text=ANSWER_900, answer_judge=failure)
    overrides = _with_900_sources() if with_sources else {}

    text, signals, extras = await _answer(litellm, stream=False, **overrides)

    assert text == (ANSWER_900 if with_sources else REFUSAL_NL)
    assert bool(extras["sources"]) is with_sources
    assert signals["judge_failed"] == ["answer"]
    assert "verdict" not in signals


async def test_escalation_keeps_its_reply_and_button_whatever_the_verdict():
    reply = "Je kunt met de knop hieronder een afspraak maken met een medewerker."
    litellm = _LiteLLM(model_text=reply, answer_judge=_answer_verdict("not_answered", claims=False))

    text, signals, extras = await _answer(litellm, stream=True, force_escalation=True, clarity="ambiguous")

    assert text == reply
    assert extras["escalation"] == [{"appointment": True}]
    assert signals["decision"] == "answer"


@pytest.mark.parametrize(("claims", "shown"), [(True, False), (False, True)])
async def test_conversational_reply_is_shown_only_without_unsupported_claims(claims, shown):
    reply = "Wij rekenen 5 euro." if claims else "Graag gedaan!"
    litellm = _LiteLLM(model_text=reply, answer_judge=_answer_verdict("not_answered", claims=claims))

    text, signals, _ = await _answer(litellm, stream=False, conversational=True)

    assert text == (reply if shown else REFUSAL_NL)
    assert signals["refused"] is not shown


async def test_passed_reply_is_stripped_of_links_before_the_judge_and_the_visitor_see_it():
    litellm = _LiteLLM(
        model_text="Kijk eventueel op https://evil.example.com/phish of www.voys.nl/hulp [1]. " + CLARIFYING_QUESTION,
        answer_judge=_answer_verdict("not_answered"),
    )

    text, _, _ = await _answer(litellm, stream=True, clarity="ambiguous")

    assert CLARIFYING_QUESTION in text
    for artifact in ("http", "evil.example", "voys.nl", "[1]"):
        assert artifact not in text
    assert "evil.example" not in litellm.judge_requests[0]["messages"][1]["content"]


@pytest.mark.parametrize(
    "overrides",
    [{"support_mode": False}, {"broad_mode": True}],
    ids=["outside_support_mode", "broad_mode_answer"],
)
async def test_answer_judge_does_not_run(overrides):
    litellm = _LiteLLM(model_text="DECT is een standaard voor draadloze telefonie.")

    _, signals, _ = await _answer(litellm, stream=True, **overrides)

    assert litellm.judge_requests == []
    assert "decision" not in signals


async def test_safety_blocked_turn_is_never_judged(monkeypatch):
    monkeypatch.setattr(partner_chat, "output_safety_violation", lambda text: "prompt_injection")
    litellm = _LiteLLM(model_text=CLARIFYING_QUESTION)

    await _answer(litellm, stream=True)

    assert litellm.judge_requests == []


# ─── The question judge ─────────────────────────────────────────────────


async def test_turn_judge_is_structured_and_failure_safe(monkeypatch) -> None:
    valid = _turn_verdict(clarity="ambiguous")
    provider_results = iter([json.dumps(valid), "invalid", RuntimeError("offline"), "slow"])
    original_client = httpx.AsyncClient

    async def handler(request: httpx.Request) -> httpx.Response:
        provider_result = next(provider_results)
        body = json.loads(request.content)
        assert body["model"] == "klai-fast"
        assert body["response_format"]["json_schema"]["strict"] is True
        if isinstance(provider_result, Exception):
            raise provider_result
        if provider_result == "slow":
            await asyncio.sleep(3)
        return httpx.Response(200, json={"choices": [{"message": {"content": provider_result}}]})

    monkeypatch.setattr(
        turn_judge.httpx, "AsyncClient", lambda timeout: original_client(transport=httpx.MockTransport(handler))
    )
    settings: Any = SimpleNamespace(
        litellm_base_url="http://litellm", litellm_master_key="key", extraction_model="klai-fast"
    )
    messages = [{"role": "user", "content": "Het werkt niet"}]

    judgement = await turn_judge.judge_turn(messages, settings)
    assert judgement is not None
    assert judgement.model_dump() == valid
    for _ in range(3):
        assert await turn_judge.judge_turn(messages, settings) is None


def test_turn_judge_reads_recent_turns_and_marks_the_latest_visitor_message():
    messages = [
        {"role": "system", "content": "injected instruction"},
        *[{"role": "user", "content": f"oud {i}"} for i in range(5)],
        {"role": "assistant", "content": "Welke app gebruik je?"},
        {"role": "user", "content": "x" * 5000},
    ]

    excerpt = turn_judge.conversation_excerpt(messages)

    assert "injected instruction" not in excerpt
    assert "oud 0" not in excerpt
    assert "Assistant: Welke app gebruik je?" in excerpt
    assert excerpt.rsplit("\n\n", 1)[1].startswith("Visitor (LATEST message): ")
    assert len(excerpt) < 5000


# ─── The route: judge beside retrieval, addendum, old clarify block gone ─


def _retrieval_reply(band: str = "low") -> httpx.Response:
    source = {"source_url": CHUNK_900["source_url"], "title": CHUNK_900["title"], "evidence_ids": ["ev1"]}
    chunk = CHUNK_900 if band == "low" else {**CHUNK_900, "reranker_score": 0.95}
    return httpx.Response(200, json={"confidence_band": band, "evidence_pack": {"items": [chunk], "sources": [source]}})


async def _route_turn(
    monkeypatch, *, turn: dict, question: str = QUESTION_900, stream: bool = False, band: str = "low"
):
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

    litellm = _LiteLLM(model_text=CLARIFYING_QUESTION, turn=turn, answer_judge=_answer_verdict("not_answered"))
    with (
        respx.mock(assert_all_called=False) as router,
        patch("app.api.partner._widget_page_context_enabled", new=AsyncMock(return_value=False)),
        patch("app.api.partner._widget_support_mode_enabled", new=AsyncMock(return_value=True)),
        patch("app.api.partner._widget_tone_register", new=AsyncMock(return_value="restrained")),
        patch("app.api.partner.asyncio"),
        patch("app.api.partner.write_retrieval_log", new=AsyncMock()),
        patch("app.services.partner_chat._schedule_gap_event"),
    ):
        router.post(f"{LITELLM}/v1/chat/completions").mock(side_effect=litellm)
        router.post(f"{RETRIEVAL}/retrieve").mock(return_value=_retrieval_reply(band))
        response = await chat_completions(request=request, http_request=http_request, auth=auth, db=db)
        if stream:
            frames = _frames([chunk async for chunk in response.body_iterator])
            text = "".join(_delta(frames, "content"))
            extras = {"escalation": _delta(frames, "escalation"), "sources": _delta(frames, "sources")}
        else:
            message = response["choices"][0]["message"]
            text = message["content"]
            extras = {
                "escalation": [message["escalation"]] if "escalation" in message else [],
                "sources": [message["sources"]] if message.get("sources") else [],
            }
    return litellm, text, extras


def _system_prompt_sent(litellm: _LiteLLM) -> str:
    (answer_request,) = litellm.answer_requests
    return "\n".join(m["content"] for m in answer_request["messages"] if m["role"] == "system")


@pytest.mark.parametrize("stream", [True, False])
async def test_route_ambiguous_turn_gets_no_ask_instruction_and_a_question_draft_is_shown(monkeypatch, stream):
    # Blind comparison on 50 real Voys first questions: the ask-instead-of-answer
    # instruction made answers worse in 19 of 23 turns, so it is gone.
    litellm, text, _ = await _route_turn(monkeypatch, turn=_turn_verdict(clarity="ambiguous"), stream=stream)

    assert "can mean different things" not in _system_prompt_sent(litellm)
    assert len(litellm.turn_requests) == 1
    assert text == CLARIFYING_QUESTION


# ─── Subjects this widget does not answer ───────────────────────────────


OFF_TOPIC_REPLY = "Deze assistent helpt bij het gebruik van Voys, niet bij prijzen of offertes."


async def _off_topic_turn(monkeypatch, *, topic: str, reply: str = OFF_TOPIC_REPLY, stream: bool = False):
    from app.api import partner

    monkeypatch.setattr(
        partner,
        "_widget_off_topic",
        AsyncMock(return_value=("prijzen, tarieven, offertes, uitstel van betaling", reply)),
    )
    return await _route_turn(
        monkeypatch, turn=_turn_verdict(topic=topic), question="Wat kost een 0800-nummer?", stream=stream
    )


@pytest.mark.parametrize("stream", [True, False])
async def test_a_subject_the_widget_does_not_answer_gets_the_tenants_own_sentence(monkeypatch, stream):
    """Putting the same rule in the widget's base prompt landed it right 8 of 15
    times on 2026-09-17, once quoting a price from an article. Here no model
    writes at all."""
    litellm, text, extras = await _off_topic_turn(monkeypatch, topic="not_handled", stream=stream)

    assert text == OFF_TOPIC_REPLY
    # The answer model was never asked, so it cannot quote a price.
    assert litellm.answer_requests == []
    # Both shapes carry the appointment button and no sources.
    assert extras["escalation"] == [{"appointment": True}]
    assert extras["sources"] == []


async def test_a_handled_subject_is_answered_as_usual(monkeypatch):
    litellm, text, _ = await _off_topic_turn(monkeypatch, topic="handled")

    assert text != OFF_TOPIC_REPLY
    assert litellm.answer_requests


async def test_without_a_configured_reply_nothing_changes(monkeypatch):
    litellm, text, _ = await _off_topic_turn(monkeypatch, topic="not_handled", reply="")

    assert text != OFF_TOPIC_REPLY
    assert litellm.answer_requests


async def test_the_judge_is_told_which_subjects_are_not_answered(monkeypatch):
    litellm, _, _ = await _off_topic_turn(monkeypatch, topic="handled")

    (judge_request,) = litellm.turn_requests
    assert "prijzen, tarieven, offertes" in judge_request["messages"][0]["content"]


async def test_turn_judge_runs_concurrently_with_retrieval():
    """Each side waits for the other to have started; run one after the other, this times out."""
    from app.api.partner import ChatCompletionsRequest, chat_completions

    retrieval_started, judge_started = asyncio.Event(), asyncio.Event()

    async def retrieve(**_kwargs):
        retrieval_started.set()
        await asyncio.wait_for(judge_started.wait(), 1)
        return [CHUNK_900], "SUPPORT PROFILE", [], False

    async def judge(_messages, _settings, **_kwargs):
        judge_started.set()
        await asyncio.wait_for(retrieval_started.wait(), 1)
        return turn_judge.TurnJudgement.model_validate(_turn_verdict())

    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeResult(rows=[FakeKB(id=10, name="KB", slug="kb-a", org_id=42)]))
    auth = make_partner_auth(kb_access={10: "read"})
    auth.key_id = "wgt_901"
    request = ChatCompletionsRequest(messages=[{"role": "user", "content": QUESTION_900}], stream=False)
    with (
        patch("app.api.partner.retrieve_context", new=retrieve),
        patch("app.api.partner.turn_judge.judge_turn", new=judge),
        patch("app.api.partner._widget_page_context_enabled", new=AsyncMock(return_value=False)),
        patch("app.api.partner._widget_support_mode_enabled", new=AsyncMock(return_value=True)),
        patch("app.api.partner._widget_tone_register", new=AsyncMock(return_value="restrained")),
        patch("app.api.partner.chat_completion_non_streaming", new=AsyncMock(return_value={"choices": []})) as chat,
        patch("app.api.partner.asyncio"),
        patch("app.api.partner.write_retrieval_log", new=AsyncMock()),
    ):
        await chat_completions(request=request, http_request=MagicMock(headers={}, client=None), auth=auth, db=db)

    timing = chat.call_args.kwargs["turn_timing"]
    assert {"retrieval_ms", "turn_judge_ms", "started_at"} <= timing.keys()
