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
from structlog.testing import capture_logs

from app.services import partner_chat, turn_judge
from app.services.chat_profile import ChatProfile

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
        referral: str = "",
        question: str = "",
    ):
        self.model_text = model_text
        self.referral = referral
        self.question = question
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
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
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
        if schema == "clarify_question":
            return _json_reply({"question": self.question})
        if schema == "off_topic_referral":
            return _json_reply({"subject": self.referral})
        if schema == "query_paraphrase":
            # Retrieval input only (query_paraphrase.py); the judges under test
            # never see it, and it is not the answer request.
            return _json_reply({"variants": []})
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


@pytest.mark.parametrize("stream", [True, False])
async def test_a_widget_answer_reaches_the_visitor_without_dashes(stream):
    """The widget owner wants no dash as punctuation in any reply; the model
    writes them anyway, copying the prompt's own style. A range stays a range."""
    answer = (
        ANSWER_900.replace(".", " \u2014 kijk eerst of de factuur open staat.", 1)
        + " Bereikbaar 9\u201317 uur \u2013 ook op zaterdag."
    )
    litellm = _LiteLLM(model_text=answer, answer_judge=_answer_verdict("answered"))

    text, _, _ = await _answer(litellm, stream=stream, **_with_900_sources())

    assert "\u2014" not in text
    assert " \u2013 " not in text
    assert "9\u201317" in text
    assert ", kijk eerst of de factuur open staat" in text


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


async def test_the_decision_record_keeps_which_statement_the_check_flagged():
    """A reply with one flagged statement goes out unrepaired by design (a single
    flag was right 77% of the time, 2.5). To measure that again, and for a
    reviewer to see what the check doubted, the record keeps the statement itself,
    not only the count."""
    litellm = _LiteLLM(
        model_text=ANSWER_900 + " De incasso kun je altijd kosteloos terugdraaien.",
        grounding=_grounding("De incasso kun je altijd kosteloos terugdraaien.", supported=(ANSWER_900,)),
    )

    _, signals, _ = await _answer(litellm, stream=False, **_with_900_sources())

    assert signals["unsupported"] == 1
    assert signals["unsupported_statements"] == ["De incasso kun je altijd kosteloos terugdraaien."]


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


async def test_an_internal_strict_turn_keeps_the_unrepaired_answer_when_nothing_survives_repair():
    """The LiteLLM hook keeps the unrepaired answer on this outcome instead of
    emptying it into a refusal (kb_answer_repair_kept: "emptying an employee's
    answer is a bigger change than the measurement supports"). Internal chat
    must make the same call, unlike the widget above."""
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

    text, signals, extras = await _answer(
        litellm,
        stream=True,
        profile=ChatProfile(surface="internal", kb_mode="strict"),
        support_mode=False,
        **_with_900_sources(),
    )

    assert len(litellm.repair_requests) == 1
    # The internal-chat footer is appended after the composed answer (see
    # test_answer_footer.py); the answer itself is the unrepaired draft.
    assert text.startswith(draft)
    assert extras["sources"]
    assert signals["refused"] is False
    assert signals["repaired"] is False


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


async def test_an_uncited_dead_end_carries_the_appointment_button():
    """A reply with no source that does not answer leaves the visitor nowhere.

    Seen in a simulated conversation on 2026-09-18: the model wrote its own
    "dat staat niet in onze helpartikelen" without offering anything, the reply
    arrived bare, and the visitor spent two more turns discovering a person was
    reachable at all — they repeated the question, got the backend's refusal
    with the button, then had to ask how to book.
    """
    litellm = _LiteLLM(
        model_text="Dat staat niet in onze helpartikelen. Laat het gerust weten als je vastloopt.",
        answer_judge=_answer_verdict("not_answered"),
    )

    text, _, extras = await _answer(litellm, stream=True)

    assert text == "Dat staat niet in onze helpartikelen. Laat het gerust weten als je vastloopt."
    assert extras["escalation"] == [{"appointment": True}]


async def test_an_uncited_reply_that_answers_keeps_no_button():
    """The button means "this went nowhere"; on an answer it would read as one."""
    litellm = _LiteLLM(model_text="Graag gedaan!", answer_judge=_answer_verdict("answered"))

    _, _, extras = await _answer(litellm, stream=True)

    assert not extras.get("escalation")


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

    text, signals, extras = await _answer(litellm, stream=False, conversational=True)

    assert text == (reply if shown else REFUSAL_NL)
    assert signals["refused"] is not shown
    if shown:
        # A conversational turn is an answer by design, even when the light judge
        # calls it unanswered, so the dead-end button may not appear under it:
        # "graag gedaan" with an offer to book reads as help with nothing.
        assert not extras.get("escalation")


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


def _retrieval_reply(band: str = "low", items: list[dict] | None = None) -> httpx.Response:
    if items is None:
        items = [CHUNK_900 if band == "low" else {**CHUNK_900, "reranker_score": 0.95}]
    sources = [
        {"source_url": item["source_url"], "title": item["title"], "evidence_ids": [item["evidence_id"]]}
        for item in items
    ]
    return httpx.Response(200, json={"confidence_band": band, "evidence_pack": {"items": items, "sources": sources}})


async def _route_turn(
    monkeypatch,
    *,
    turn: dict,
    question: str = QUESTION_900,
    stream: bool = False,
    band: str = "low",
    referral: str = "",
    question_written: str = "",
    items: list[dict] | None = None,
    litellm: _LiteLLM | None = None,
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

    litellm = litellm or _LiteLLM(
        model_text=CLARIFYING_QUESTION,
        turn=turn,
        answer_judge=_answer_verdict("not_answered"),
        referral=referral,
        question=question_written,
    )
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
        router.post(f"{RETRIEVAL}/retrieve").mock(return_value=_retrieval_reply(band, items))
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


def _variant_items(score: float) -> list[dict]:
    """Two troubleshooters that differ only in the platform, both with a section on not being able to call."""
    return [
        {
            "chunk_id": f"v{i}",
            "evidence_id": f"evv{i}",
            "title": f"Alpha phone app for {platform} troubleshooter",
            "heading_path": "Troubleshooter > I can't call",
            "text": f"On {platform}, allow the microphone and restart the Alpha phone app.",
            "source_url": f"https://help.example.com/{platform.lower()}",
            "reranker_score": score - i / 10,
        }
        for i, platform in enumerate(("iPhone", "Android"))
    ]


VARIANT_QUESTION = "Do you call with the iPhone app or the Android app?"


@pytest.mark.parametrize("stream", [True, False])
async def test_strong_articles_on_one_topic_in_two_variants_hand_the_turn_one_question(monkeypatch, stream):
    """ "ik kan niet bellen met mijn apparaat" got an iPhone answer for a visitor
    who never named a device (logbook 2.44). When strong articles cover the same
    topic per platform, the turn is handed the question that picks the platform."""
    litellm, _, _ = await _route_turn(
        monkeypatch,
        turn=_turn_verdict(),
        question="I can't call",
        stream=stream,
        band="high",
        items=_variant_items(0.9),
        question_written=VARIANT_QUESTION,
    )

    assert VARIANT_QUESTION in _system_prompt_sent(litellm)
    writer_calls = [body for body in litellm.requests if _call_kind(body) == "clarify_question"]
    assert [body.get("metadata") for body in writer_calls] == [_delegated_with_tag("clarify_question")]
    assert "iPhone; Android" in writer_calls[0]["messages"][1]["content"]


async def test_weak_articles_get_the_weak_source_rule_even_when_they_differ_in_a_variant(monkeypatch):
    """A planned question used to switch the weak-source rule off, and weak
    articles are where most needless questions were asked (logbook 2.54)."""
    litellm, _, _ = await _route_turn(
        monkeypatch,
        turn=_turn_verdict(),
        question="I can't call",
        band="low",
        items=_variant_items(0.3),
        question_written=VARIANT_QUESTION,
    )

    prompt = _system_prompt_sent(litellm)
    assert "Retrieval found nothing that clearly matches" in prompt
    assert VARIANT_QUESTION not in prompt
    assert not [body for body in litellm.requests if _call_kind(body) == "clarify_question"]


async def test_the_decision_is_logged_without_any_text_of_the_turn(monkeypatch):
    with capture_logs() as logs:
        await _route_turn(
            monkeypatch,
            turn=_turn_verdict(),
            question="I can't call since this morning",
            band="high",
            items=_variant_items(0.9),
            question_written=VARIANT_QUESTION,
        )

    (decision,) = [entry for entry in logs if entry["event"] == "clarify_decision"]
    assert {key: decision[key] for key in ("fired", "reason", "axis", "documents", "options")} == {
        "fired": True,
        "reason": "asked",
        "axis": "device",
        "documents": 2,
        "options": 2,
    }
    rendered = repr(logs)
    for text in (VARIANT_QUESTION, "since this morning", "iPhone", "Android", "troubleshooter"):
        assert text not in rendered


@pytest.mark.parametrize(
    ("band", "told"),
    [("low", True), ("high", False)],
)
async def test_a_turn_whose_articles_all_score_below_the_bar_may_not_build_an_answer_from_them(monkeypatch, band, told):
    """Answers the owner called wrong sat on a best source around 0.37, and a
    question about outbound calling permissions was answered from the
    click-to-call article. With every article below the gap threshold the turn
    is told to answer only from one that really covers the question."""
    litellm, _, _ = await _route_turn(monkeypatch, turn=_turn_verdict(), band=band)

    assert ("Retrieval found nothing that clearly matches" in _system_prompt_sent(litellm)) is told


async def test_a_thank_you_with_weak_articles_is_not_told_to_say_nothing_was_found(monkeypatch):
    """A greeting or a thank-you often retrieves only weak articles; it still gets
    the short conversational reply, not "not in the help articles" and a button."""
    litellm, _, _ = await _route_turn(monkeypatch, turn=_turn_verdict(scope="conversation"), band="low")

    assert "Retrieval found nothing that clearly matches" not in _system_prompt_sent(litellm)


@pytest.mark.parametrize("stream", [True, False])
async def test_an_unanswered_reply_over_weak_articles_shows_no_source_card(monkeypatch, stream):
    """Measured on 27 real weak-source turns: 26 of 29 "not in the help articles"
    replies still carried the neighbouring article as a source card, which
    contradicts the text. The honest reply keeps its button, not the card."""
    _, _, extras = await _route_turn(monkeypatch, turn=_turn_verdict(), band="low", stream=stream)

    assert extras["sources"] == []
    assert extras["escalation"] == [{"appointment": True}]


# ─── Subjects this widget does not answer ───────────────────────────────


OFF_TOPIC_REPLY = "Deze assistent helpt bij het gebruik van Voys, niet bij prijzen of offertes."


async def _off_topic_turn(
    monkeypatch,
    *,
    topic: str,
    reply: str = OFF_TOPIC_REPLY,
    stream: bool = False,
    wants_human: bool = False,
    question: str = "Wat kost een 0800-nummer?",
    referral: str = "",
):
    from app.api import partner

    monkeypatch.setattr(
        partner,
        "_widget_off_topic",
        AsyncMock(return_value=("prijzen, tarieven, offertes, uitstel van betaling", reply)),
    )
    return await _route_turn(
        monkeypatch,
        turn=_turn_verdict(topic=topic, wants_human=wants_human),
        question=question,
        stream=stream,
        referral=referral,
    )


@pytest.mark.parametrize("stream", [True, False])
async def test_a_subject_the_widget_does_not_answer_gets_the_tenants_own_sentence(monkeypatch, stream):
    """Putting the same rule in the widget's base prompt landed it right 8 of 15
    times on 2026-09-17, once quoting a price from an article. Here no answer
    model writes; the referral call returns no subject, so the tenant's own
    sentence stands."""
    litellm, text, extras = await _off_topic_turn(monkeypatch, topic="not_handled", stream=stream)

    assert text == OFF_TOPIC_REPLY
    # The answer model was never asked, so it cannot quote a price.
    assert litellm.answer_requests == []
    # Both shapes carry the appointment button and no sources.
    assert extras["escalation"] == [{"appointment": True}]
    assert extras["sources"] == []


def _referral(subject: str) -> str:
    return f"Over {subject} kijkt een collega graag persoonlijk met je mee. Plan hieronder een afspraak, dan helpen we je verder."


@pytest.mark.parametrize("stream", [True, False])
async def test_the_referral_names_what_the_visitor_asked_about(monkeypatch, stream):
    """The tenant's fixed sentence lists every excluded subject, so a question
    about an invoice was told about prices, quotes and contracts. The referral
    names the visitor's own subject; no answer model is asked."""
    litellm, text, extras = await _off_topic_turn(
        monkeypatch, topic="not_handled", referral="je factuur", question="ik wil een factuur ontvangen", stream=stream
    )

    assert text == _referral("je factuur")
    assert litellm.answer_requests == []
    assert extras["escalation"] == [{"appointment": True}]


async def test_a_request_for_a_person_on_an_excluded_subject_gets_the_referral_not_an_answer(monkeypatch):
    """A request for a technical call got the sentence about prices and quotes.
    It gets a referral naming the request, and still no answer model: the
    human-request turn would generate with the articles in the prompt."""
    litellm, text, extras = await _off_topic_turn(
        monkeypatch,
        topic="not_handled",
        wants_human=True,
        referral="een call over een CRM koppeling",
        question="Kunnen we een call inplannen met jullie techniek over een CRM-koppeling?",
    )

    assert text == _referral("een call over een CRM koppeling")
    assert litellm.answer_requests == []
    assert extras["escalation"] == [{"appointment": True}]


async def test_a_subject_with_a_word_the_visitor_did_not_use_falls_back_to_the_tenants_sentence(monkeypatch):
    """The model may only name the subject in the visitor's words: "gratis" or
    a price it made up never reaches the sentence."""
    _, text, _ = await _off_topic_turn(monkeypatch, topic="not_handled", referral="een gratis 0800 nummer")

    assert text == OFF_TOPIC_REPLY


async def test_a_subject_that_is_not_a_plain_phrase_falls_back_to_the_tenants_sentence(monkeypatch):
    _, text, _ = await _off_topic_turn(monkeypatch, topic="not_handled", referral="[een 0800-nummer](//x.example)")

    assert text == OFF_TOPIC_REPLY


async def test_the_referral_speaks_to_the_visitor_not_as_the_visitor(monkeypatch):
    _, text, _ = await _off_topic_turn(
        monkeypatch,
        topic="not_handled",
        referral="uitgaand bellen met mijn mobiele nummer",
        question="ik wil graag uitgaand kunnen bellen met mijn mobiele nummer",
    )

    assert text == _referral("uitgaand bellen met je mobiele nummer")


async def test_a_referral_may_repeat_the_visitors_own_number(monkeypatch):
    _, text, _ = await _off_topic_turn(
        monkeypatch,
        topic="not_handled",
        referral="een offerte voor 25 gebruikers",
        question="25 gebruikers offerte graag",
    )

    assert text == _referral("een offerte voor 25 gebruikers")


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


# ─── Every LiteLLM call of a widget turn names the tenant for PII masking ─
#
# portal-api calls LiteLLM with the master key, which belongs to no tenant, so
# LiteLLM's PII enforcer only masks the visitor's text when the org travels in
# the body (klai_pii_enforce._delegated_org_id). One call without it is one
# place the visitor's personal data reaches the model provider unmasked.

DELEGATED = {"_klai_delegated_org_id": "zit-org-42"}

# Every kind's own LiteLLM spend tag (SPEC: every call is attributable to the
# feature that made it -- LiteLLM_SpendLogs.request_tags, from metadata.tags).
_TAG_FOR_KIND = {
    "turn_judge": "portal:turn-judge",
    "query_paraphrase": "portal:query-paraphrase",
    "answer_judge": "portal:answer-judge",
    "off_topic_referral": "portal:off-topic-referral",
    "clarify_question": "portal:clarify-question",
    "grounding_check": "portal:answer-grounding",
    "repair": "portal:answer-grounding",
    "answer": "portal:partner-chat-answer",
}


def _call_kind(body: dict) -> str:
    schema = (body.get("response_format") or {}).get("json_schema", {}).get("name")
    if schema:
        return schema
    if "You edit a reply" in body["messages"][0]["content"]:
        return "repair"
    return "answer"


def _metadata_per_call(litellm: _LiteLLM) -> list[tuple[str, Any]]:
    return [(_call_kind(body), body.get("metadata")) for body in litellm.requests]


def _delegated_with_tag(kind: str) -> dict:
    return {**DELEGATED, "tags": [_TAG_FOR_KIND[kind]]}


@pytest.mark.parametrize("stream", [True, False])
async def test_every_litellm_call_of_an_answered_widget_turn_names_the_tenant(monkeypatch, stream):
    litellm = _LiteLLM(
        model_text=ANSWER_900 + " Bel 020-7001234 voor een terugboeking.",
        grounding=_grounding("Bel 020-7001234 voor een terugboeking.", contradicted=True, supported=(ANSWER_900,)),
        repaired=ANSWER_900,
    )

    await _route_turn(monkeypatch, turn=_turn_verdict(), stream=stream, band="high", litellm=litellm)

    calls = _metadata_per_call(litellm)
    assert {kind for kind, _ in calls} == {
        "turn_judge",
        "query_paraphrase",
        "answer",
        "answer_judge",
        "grounding_check",
        "repair",
    }
    assert calls == [(kind, _delegated_with_tag(kind)) for kind, _ in calls]


async def test_the_off_topic_referral_call_names_the_tenant(monkeypatch):
    litellm, _, _ = await _off_topic_turn(monkeypatch, topic="not_handled", referral="je factuur")

    calls = _metadata_per_call(litellm)
    assert {kind for kind, _ in calls} == {"turn_judge", "query_paraphrase", "off_topic_referral"}
    assert calls == [(kind, _delegated_with_tag(kind)) for kind, _ in calls]
