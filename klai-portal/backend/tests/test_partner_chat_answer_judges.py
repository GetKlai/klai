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
from helpers import FakeKB, FakeResult, make_partner_auth, selection_quoting_first_passage
from klai_chat_prompts import (
    appointment_offer_sentence,
    check_unavailable_message,
    contact_reply,
    contact_reply_again,
    no_citable_sources_message,
)
from structlog.testing import capture_logs

from app.services import case_file, chat_safety, query_paraphrase, turn_judge, widget_write
from app.services.retrieval_context import KnowledgeTurn, kept_passages_of
from app.services.widget_reply import Reply, reply_body, reply_frames
from app.services.widget_write import write_reply

LITELLM = "http://litellm:4000"
RETRIEVAL = "http://retrieval-api:8040"
CLARIFYING_QUESTION = "Gaat het om je factuur of om je abonnement?"
REFUSAL_NL = no_citable_sources_message("nl", helpdesk=True)
OFFER_NL = appointment_offer_sentence("nl")
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


def _selection(verdict: str, *evidence: tuple[int, str], question: str = "") -> dict:
    """The selection step's reply: the verdict, and per passage the sentence it points at."""
    return {
        "need": "",
        "evidence": [{"passage": number, "quote": quote} for number, quote in evidence],
        "verdict": verdict,
        "question": question,
    }


def _case(
    wants: str = "knowledge",
    *queries: str,
    problem: str = "",
    out_of_scope: bool = False,
    wants_quote: str = "",
    out_of_scope_quote: str = "",
    language: str = "",
) -> dict:
    return {
        "problem": problem,
        "product": "",
        "product_label": "",
        "variant": "",
        "tried": [],
        "wants_quote": wants_quote,
        "wants": wants,
        "out_of_scope_quote": out_of_scope_quote,
        "out_of_scope": out_of_scope,
        "sentiment": "neutral",
        "language": language,
        "queries": list(queries),
    }


class _LiteLLM:
    """One route for every LiteLLM call, told apart by the schema name each judge sends."""

    def __init__(
        self,
        *,
        model_text: str,
        answer_judge: Any = None,
        grounding: Any = None,
        repaired: str = "",
        referral: str = "",
        question: str = "",
        selection: Any = None,
        case: Any = None,
    ):
        self.model_text = model_text
        # The case step (case_file.py): by default a knowledge question with no
        # search queries, which changes nothing about the turn.
        self.case = case or _case()
        self.case_requests: list[dict] = []
        self.retrieve_requests: list[dict] = []
        # The selection step (passage_selection.py). By default it quotes
        # passage 1, so the writer reads that passage with the brief.
        self.selection = selection
        self.referral = referral
        self.question = question
        self.answer_judge = answer_judge if answer_judge is not None else _answer_verdict()
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
        self.grounding_requests: list[dict] = []
        self.repair_requests: list[dict] = []
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        schema = (body.get("response_format") or {}).get("json_schema", {}).get("name")
        structured = self._structured(schema, body)
        if structured is not None:
            return structured
        if "You edit a reply" in (body.get("messages") or [{}])[0].get("content", ""):
            self.repair_requests.append(body)
            return httpx.Response(200, json={"choices": [{"message": {"content": self.repaired}}]})
        self.answer_requests.append(body)
        if body.get("stream"):
            frame = json.dumps({"choices": [{"index": 0, "delta": {"content": self.model_text}}]})
            return httpx.Response(200, content=f"data: {frame}\n\ndata: [DONE]\n\n".encode())
        message = {"role": "assistant", "content": self.model_text}
        return httpx.Response(200, json={"choices": [{"index": 0, "message": message, "finish_reason": "stop"}]})

    def _structured(self, schema: str | None, body: dict) -> httpx.Response | None:
        """The judges, told apart by the schema name each one sends; None for the answer and repair calls."""
        if schema == "answer_judge":
            self.judge_requests.append(body)
            if isinstance(self.answer_judge, Exception):
                raise self.answer_judge
            if isinstance(self.answer_judge, httpx.Response):
                return self.answer_judge
            return _json_reply(self.answer_judge)
        if schema == "clarify_question":
            return _json_reply({"question": self.question})
        if schema == "off_topic_referral":
            return _json_reply({"subject": self.referral})
        if schema == "case_update":
            self.case_requests.append(body)
            if isinstance(self.case, Exception):
                raise self.case
            return _json_reply(self.case)
        if schema == "passage_selection":
            if isinstance(self.selection, Exception):
                raise self.selection
            return _json_reply(selection_quoting_first_passage(body) if self.selection is None else self.selection)
        if schema == "query_paraphrase":
            # Retrieval input only (query_paraphrase.py); the judges under test
            # never see it, and it is not the answer request.
            return _json_reply({"variants": []})
        if schema == "grounding_check":
            self.grounding_requests.append(body)
            if isinstance(self.grounding, Exception):
                raise self.grounding
            return _json_reply(self.grounding)
        return None


def _written_reply() -> Reply:
    return Reply(text="", sources=[], signals={}, language=None, turn=KnowledgeTurn())


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
    """Run one widget turn; return (visible text, signals, extras). No citable source unless overridden.

    A widget or partner turn is written once and rendered as frames or as a body.
    """
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
        "source_query": "rekening factuur abonnement kosten",
        "answer_signals": signals,
        **overrides,
    }
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{LITELLM}/v1/chat/completions").mock(side_effect=litellm)
        reply = await write_reply(**kwargs)
        frames = _frames(reply_frames(reply))
        message = reply_body(reply, model=kwargs["model"])["choices"][0]["message"]
        if stream:
            text = "".join(_delta(frames, "content"))
            extras = {
                "broad_mode": _delta(frames, "broad_mode"),
                "escalation": _delta(frames, "escalation"),
                "sources": [s for batch in _delta(frames, "sources") for s in batch],
            }
        else:
            text = message["content"]
            extras = {
                "broad_mode": [message["broad_mode"]] if "broad_mode" in message else [],
                "escalation": [message["escalation"]] if "escalation" in message else [],
                "sources": message.get("sources") or [],
            }
        turn = kwargs.get("knowledge_turn")
        if turn is not None and turn.grounding_check is not None:
            # The check after the reply (step M) calls LiteLLM too: drained here, inside the mock.
            await turn.grounding_check
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

    text, signals, extras = await _answer(litellm, stream=stream, **_with_900_sources())

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


@pytest.mark.parametrize("contradicted", [False, True])
@pytest.mark.parametrize("stream", [True, False])
async def test_nothing_changes_a_widget_answer_after_it_is_written(stream, contradicted):
    """What the writer may read is decided before it writes (passage_selection.py).
    Afterwards the check only measures: editing a flagged reply was the largest
    cause of a bad answer in the owner's review, and a refusal on one
    "contradicted" threw away a correct procedure whose warning the check
    misread. The verdict is kept in the record."""
    draft = ANSWER_900 + " Storneren kan binnen acht weken. Het bedrag staat binnen 3 werkdagen terug."
    litellm = _LiteLLM(
        model_text=draft,
        grounding=_grounding(
            "Storneren kan binnen acht weken.",
            "Het bedrag staat binnen 3 werkdagen terug.",
            supported=(ANSWER_900,),
            contradicted=contradicted,
        ),
    )

    text, signals, extras = await _answer(litellm, stream=stream, **_with_900_sources())

    assert text == draft
    assert [s["url"] for s in extras["sources"]] == ["https://help.example.com/factuur"]
    assert signals["unsupported"] == 2
    assert litellm.repair_requests == []
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

    text, _, _ = await _answer(litellm, stream=True)

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


async def test_uncited_draft_without_claims_is_shown_as_before_the_judges():
    # The original claims rule: text without a source that states nothing about
    # the organisation reaches the visitor.
    litellm = _LiteLLM(model_text="Dat kan ik niet vinden.", answer_judge=_answer_verdict("not_answered"))

    text, signals, _ = await _answer(litellm, stream=True)

    assert text.startswith("Dat kan ik niet vinden.")
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

    # The button alone is not the offer: in the owner's review of real answers a
    # third of these replies never mentioned the appointment, and read as a wall.
    assert text == (
        "Dat staat niet in onze helpartikelen. Laat het gerust weten als je vastloopt. "
        "Wil je het zeker weten, plan dan een afspraak met een medewerker. Die helpt je persoonlijk verder."
    )
    assert extras["escalation"] == [{"appointment": True}]


async def test_a_dead_end_that_names_the_appointment_is_left_as_written():
    written = "Dat staat niet in onze helpartikelen. Plan gerust een afspraak met een collega."
    litellm = _LiteLLM(model_text=written, answer_judge=_answer_verdict("not_answered"))

    text, _, _ = await _answer(litellm, stream=True)

    assert text == written


@pytest.mark.parametrize("stream", [True, False])
async def test_the_record_says_what_the_visitor_was_shown(stream):
    """A review of stored answers could not see that the button was under a
    reply, what tone the turn judge heard, or that a planned question was never
    asked: none of it was stored."""
    litellm = _LiteLLM(model_text="Je vindt dit onder Beheer.", answer_judge=_answer_verdict("not_answered"))
    signals: dict[str, Any] = {"planned_question": True}

    await _answer(litellm, stream=stream, sentiment="negative", answer_signals=signals)

    assert signals["appointment"] is True
    assert signals["sentiment"] == "negative"
    assert signals["question_asked"] is False


async def test_an_uncited_reply_that_answers_keeps_no_button():
    """The button means "this went nowhere"; on an answer it would read as one."""
    litellm = _LiteLLM(model_text="Graag gedaan!", answer_judge=_answer_verdict("answered"))

    _, _, extras = await _answer(litellm, stream=True)

    assert not extras.get("escalation")


async def test_a_question_that_carries_a_claim_is_refused():
    litellm = _LiteLLM(
        model_text="Bedoel je je abonnement van 5 euro per maand?",
        answer_judge=_answer_verdict("not_answered", claims=True),
    )

    text, _, _ = await _answer(litellm, stream=True)

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

    text, signals, extras = await _answer(litellm, stream=True, force_escalation=True)

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

    text, _, _ = await _answer(litellm, stream=True)

    assert CLARIFYING_QUESTION in text
    for artifact in ("http", "evil.example", "voys.nl", "[1]"):
        assert artifact not in text
    assert "evil.example" not in litellm.judge_requests[0]["messages"][1]["content"]


async def test_answer_judge_does_not_run_on_a_broad_mode_answer():
    litellm = _LiteLLM(model_text="DECT is een standaard voor draadloze telefonie.")

    _, signals, _ = await _answer(litellm, stream=True, broad_mode=True)

    assert litellm.judge_requests == []
    assert "decision" not in signals


async def test_safety_blocked_turn_is_never_judged(monkeypatch):
    monkeypatch.setattr(widget_write, "output_safety_violation", lambda text: "prompt_injection")
    litellm = _LiteLLM(model_text=CLARIFYING_QUESTION)

    await _answer(litellm, stream=True)

    assert litellm.judge_requests == []


# ─── The question judge ─────────────────────────────────────────────────


async def test_the_case_call_is_structured_and_failure_safe(monkeypatch) -> None:
    valid = _case("knowledge", "webphone geluid", problem="geen geluid in de webphone")
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
            await asyncio.sleep(4)
        return httpx.Response(200, json={"choices": [{"message": {"content": provider_result}}]})

    monkeypatch.setattr(
        turn_judge.httpx, "AsyncClient", lambda timeout: original_client(transport=httpx.MockTransport(handler))
    )
    settings: Any = SimpleNamespace(
        litellm_base_url="http://litellm", litellm_master_key="key", extraction_model="klai-fast"
    )
    messages = [{"role": "user", "content": "Het werkt niet"}]

    case = await case_file.update_case(messages, None, settings)
    assert case is not None
    assert case.model_dump() == valid
    for _ in range(3):
        assert await case_file.update_case(messages, None, settings) is None


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
    # retrieval-api returns the raw chunks beside the pack; they carry what the
    # pack items do not (the knowledge-base slug, the product).
    return httpx.Response(
        200, json={"confidence_band": band, "chunks": items, "evidence_pack": {"items": items, "sources": sources}}
    )


async def _route_turn(
    monkeypatch,
    *,
    case: dict | None = None,
    question: str = QUESTION_900,
    stream: bool = False,
    band: str = "low",
    referral: str = "",
    question_written: str = "",
    items: list[dict] | None = None,
    litellm: _LiteLLM | None = None,
    messages: list[dict] | None = None,
    stored: case_file.StoredTurn | None = None,
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
    request = ChatCompletionsRequest(messages=messages or [{"role": "user", "content": question}], stream=stream)
    # A session token makes the turn a recorded widget turn, the only kind that reads a stored one.
    headers = {"authorization": "Bearer session-token"} if stored is not None else {}
    http_request = MagicMock(headers=headers, client=MagicMock(host="127.0.0.1"))

    litellm = litellm or _LiteLLM(
        model_text=CLARIFYING_QUESTION,
        case=case,
        answer_judge=_answer_verdict("not_answered"),
        referral=referral,
        question=question_written,
    )
    with (
        respx.mock(assert_all_called=False) as router,
        patch("app.api.partner.asyncio"),
        patch("app.services.widget_turn.write_retrieval_log", new=AsyncMock()),
        patch("app.api.partner.last_case", new=AsyncMock(return_value=stored)),
        patch("app.services.retrieval_context._schedule_gap_event"),
    ):
        router.post(f"{LITELLM}/v1/chat/completions").mock(side_effect=litellm)
        retrieve_route = router.post(f"{RETRIEVAL}/retrieve").mock(return_value=_retrieval_reply(band, items))
        response = await chat_completions(request=request, http_request=http_request, auth=auth, db=db)
        litellm.retrieve_requests = [json.loads(call.request.content) for call in retrieve_route.calls]
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
    litellm, text, _ = await _route_turn(monkeypatch, stream=stream)

    assert "can mean different things" not in _system_prompt_sent(litellm)
    assert len(litellm.case_requests) == 1
    assert text == CLARIFYING_QUESTION


async def test_a_thank_you_with_weak_articles_is_not_told_to_say_nothing_was_found(monkeypatch):
    """A greeting or a thank-you often retrieves only weak articles; it still gets
    the short conversational reply, not "not in the help articles" and a button."""
    litellm, _, _ = await _route_turn(monkeypatch, case=_case("chat"), band="low")

    assert "Retrieval found nothing that clearly matches" not in _system_prompt_sent(litellm)


# ─── The statement check after the reply (step M) ───────────────────────


@pytest.mark.parametrize("stream", [True, False])
async def test_a_cited_reply_from_chosen_passages_is_checked_after_it_went_out(stream):
    """The writer read only passages the selection chose and cited one: nothing
    changes such a reply after writing, so the statement check is measurement
    and runs after the reply, with the light judge alone on the path."""
    from app.services.retrieval_context import _CHOSEN_PASSAGE_KEY, KnowledgeTurn

    litellm = _LiteLLM(model_text=ANSWER_900, answer_judge=_answer_verdict("answered"))
    turn = KnowledgeTurn()
    chosen = {**_with_900_sources(), "citation_chunks": [{**CHUNK_900, _CHOSEN_PASSAGE_KEY: True}]}

    text, signals, _ = await _answer(litellm, stream=stream, knowledge_turn=turn, **chosen)

    assert text.startswith(ANSWER_900) and signals["decision"] == "answer"
    assert turn.grounding_check is not None, "the check runs after the reply"
    assert "judge_failed" not in signals
    await turn.grounding_check
    assert len(litellm.grounding_requests) == 1
    assert signals["grounding"] == "all_in_articles" and signals["unsupported"] == 0


@pytest.mark.parametrize("stream", [True, False])
async def test_an_uncited_reply_from_chosen_passages_keeps_the_check_on_the_path(stream):
    """Without a source the statements decide between showing and refusing, so the check waits."""
    from app.services.retrieval_context import _CHOSEN_PASSAGE_KEY, KnowledgeTurn

    litellm = _LiteLLM(model_text="Dat regel je onder Beheer.", answer_judge=_answer_verdict("not_answered"))
    turn = KnowledgeTurn()
    uncited = {**_with_900_sources(), "citation_chunks": [{**CHUNK_900, _CHOSEN_PASSAGE_KEY: True}]}

    _, signals, extras = await _answer(litellm, stream=stream, knowledge_turn=turn, **uncited)

    assert not extras.get("sources") or extras["sources"] == [[]]
    assert turn.grounding_check is None
    assert len(litellm.grounding_requests) == 1
    assert "unsupported" in signals


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
        "_widget_settings",
        AsyncMock(
            return_value=partner.WidgetSettings(
                off_topic_subjects="prijzen, tarieven, offertes, uitstel van betaling", off_topic_reply=reply
            )
        ),
    )
    return await _route_turn(
        monkeypatch,
        case=_case(
            "human" if wants_human else "knowledge",
            out_of_scope=topic == "not_handled",
            wants_quote=question if wants_human else "",
            out_of_scope_quote=question if topic == "not_handled" else "",
        ),
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


async def test_a_request_for_a_person_on_an_excluded_subject_gets_the_contact_reply_not_an_answer(monkeypatch):
    """A request for a technical call got the sentence about prices and quotes.
    The visitor asked how to reach a person, so the contact route wins from
    the referral; still no answer model, which would generate with the
    articles in the prompt."""
    litellm, text, extras = await _off_topic_turn(
        monkeypatch,
        topic="not_handled",
        wants_human=True,
        referral="een call over een CRM koppeling",
        question="Kunnen we een call inplannen met jullie techniek over een CRM-koppeling?",
    )

    assert text == contact_reply("nl")
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

    (case_request,) = litellm.case_requests
    assert "prijzen, tarieven, offertes" in case_request["messages"][0]["content"]


async def test_the_case_runs_concurrently_with_retrieval_on_a_first_turn():
    """Each side waits for the other to have started; run one after the other, this times out."""
    from app.api.partner import ChatCompletionsRequest, chat_completions

    retrieval_started, case_started = asyncio.Event(), asyncio.Event()

    async def retrieve(**_kwargs):
        retrieval_started.set()
        await asyncio.wait_for(case_started.wait(), 1)
        return [CHUNK_900], "SUPPORT PROFILE", [], False

    async def update_case(_messages, _previous, _settings, **_kwargs):
        case_started.set()
        await asyncio.wait_for(retrieval_started.wait(), 1)
        return case_file.Case.model_validate(_case())

    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeResult(rows=[FakeKB(id=10, name="KB", slug="kb-a", org_id=42)]))
    auth = make_partner_auth(kb_access={10: "read"})
    auth.key_id = "wgt_901"
    request = ChatCompletionsRequest(messages=[{"role": "user", "content": QUESTION_900}], stream=False)
    with (
        patch("app.services.widget_turn.retrieve_context", new=retrieve),
        patch("app.api.partner.case_file.update_case", new=update_case),
        patch("app.services.widget_turn.write_reply", new=AsyncMock(return_value=_written_reply())) as chat,
        patch("app.api.partner.asyncio"),
        patch("app.services.widget_turn.write_retrieval_log", new=AsyncMock()),
    ):
        await chat_completions(request=request, http_request=MagicMock(headers={}, client=None), auth=auth, db=db)

    timing = chat.call_args.kwargs["turn_timing"]
    assert {"retrieval_ms", "case_ms", "started_at"} <= timing.keys()


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
    "query_paraphrase": "portal:query-paraphrase",
    "answer_judge": "portal:answer-judge",
    "off_topic_referral": "portal:off-topic-referral",
    "passage_selection": "portal:passage-selection",
    "case_update": "portal:case-update",
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
    )

    await _route_turn(monkeypatch, stream=stream, band="high", litellm=litellm)

    calls = _metadata_per_call(litellm)
    assert {kind for kind, _ in calls} == {
        "query_paraphrase",
        "passage_selection",
        "case_update",
        "answer",
        "answer_judge",
        "grounding_check",
    }
    assert calls == [(kind, _delegated_with_tag(kind)) for kind, _ in calls]


async def test_the_off_topic_referral_call_names_the_tenant(monkeypatch):
    litellm, _, _ = await _off_topic_turn(monkeypatch, topic="not_handled", referral="je factuur")

    calls = _metadata_per_call(litellm)
    assert {kind for kind, _ in calls} == {
        "query_paraphrase",
        "passage_selection",
        "case_update",
        "off_topic_referral",
    }
    assert calls == [(kind, _delegated_with_tag(kind)) for kind, _ in calls]


# ─── The selection step: which passages answer, decided before writing ───


def _two_articles() -> list[dict]:
    return [
        {**CHUNK_900, "reranker_score": 0.95},
        {
            "chunk_id": "c2",
            "evidence_id": "ev2",
            "title": "Incasso storneren",
            "text": "Een incasso storneer je binnen acht weken via je eigen bank.",
            "source_url": "https://help.example.com/storneren",
            "reranker_score": 0.6,
        },
    ]


def _selected_turn(monkeypatch, selection: Any, *, stream: bool = False, case: dict | None = None):
    litellm = _LiteLLM(model_text=ANSWER_900, selection=selection, case=case)
    return _route_turn(monkeypatch, stream=stream, band="high", items=_two_articles(), litellm=litellm)


@pytest.mark.parametrize("stream", [True, False])
async def test_passages_on_the_subject_that_do_not_answer_get_the_refusal_and_no_written_reply(monkeypatch, stream):
    """The top article scores 0.95 because it shares the subject, and says
    nothing about the question. A model handed such passages writes a procedure
    out of them, so none is called: the visitor gets the honest "not found"
    with the appointment."""
    litellm, text, extras = await _selected_turn(monkeypatch, _selection("not_in_passages"), stream=stream)

    assert text == REFUSAL_NL
    assert extras["escalation"] == [{"appointment": True}]
    assert litellm.answer_requests == []


async def test_the_writer_reads_only_the_passages_that_answer(monkeypatch):
    litellm, _, _ = await _selected_turn(
        monkeypatch, _selection("answers", (2, "Een incasso storneer je binnen acht weken via je eigen bank."))
    )

    prompt = _system_prompt_sent(litellm)
    assert "storneer je binnen acht weken" in prompt
    assert CHUNK_900["text"] not in prompt
    # And it is told which sentence answers: with only the passage in hand the
    # writer still built a complete-looking procedure around it.
    assert "- Een incasso storneer je binnen acht weken via je eigen bank." in prompt


async def test_a_verdict_whose_quoted_sentence_is_not_in_the_passage_counts_as_not_found(monkeypatch):
    """Two one-line passages that only say the product exists were called an
    answer, and the writer invented the installation steps. The verdict has to
    point at the sentence; code looks it up."""
    litellm, text, _ = await _selected_turn(
        monkeypatch, _selection("answers", (1, "Open het portaal en kies Terugboeken onder Facturen."))
    )

    assert text == REFUSAL_NL
    assert litellm.answer_requests == []


@pytest.mark.parametrize("stream", [True, False])
async def test_steps_that_differ_per_variant_get_the_one_question_as_the_reply(monkeypatch, stream):
    question = "Gaat het om een zakelijke of een particuliere rekening?"
    litellm, text, extras = await _selected_turn(
        monkeypatch,
        _selection(
            "depends",
            (1, "Je betaalt je factuur via automatische incasso."),
            (2, "Een incasso storneer je binnen acht weken via je eigen bank."),
            question=question,
        ),
        stream=stream,
    )

    assert text == question
    assert extras["escalation"] == []
    assert litellm.answer_requests == []


async def test_a_visitor_who_asks_for_a_person_is_not_told_the_articles_lack_an_answer(monkeypatch):
    """A first turn: the case runs beside the search, and its want decides the route after it."""
    litellm, text, extras = await _selected_turn(
        monkeypatch, _selection("not_in_passages"), case=_case("human", wants_quote="hoe kan ik die incasso storneren")
    )

    assert text == contact_reply("nl")
    assert extras["escalation"] == [{"appointment": True}]
    assert litellm.answer_requests == []


async def test_a_selection_that_fails_twice_says_it_cannot_check_not_that_the_articles_lack_it(monkeypatch):
    """Six failed selections in the week of 14 to 21 October each handed the
    writer every passage, so without a verdict no writer answers. Nor is it
    "not found": on 2026-10-23 every not-found reply of the Voys widget came
    from a selection that failed on the model budget, and the articles had
    never been checked."""
    with capture_logs() as captured:
        litellm, text, extras = await _selected_turn(monkeypatch, httpx.ConnectError("boom"))

    assert text == check_unavailable_message("nl", helpdesk=True)
    assert text != REFUSAL_NL
    assert [e["selection_failed"] for e in captured if e["event"] == "partner_chat_not_in_passages"] == [True]
    assert extras["escalation"] == [{"appointment": True}]
    assert litellm.answer_requests == []
    selection_calls = [
        body
        for body in litellm.requests
        if (body.get("response_format") or {}).get("json_schema", {}).get("name") == "passage_selection"
    ]
    assert len(selection_calls) == 2


async def test_a_request_for_a_person_after_two_failed_selections_gets_the_contact_reply_not_a_written_one(
    monkeypatch,
):
    """The case failed too, so only the regex knows the visitor asked for a
    person; that exemption from "not found" used to send the turn to the
    writer with every passage."""
    litellm = _LiteLLM(model_text=ANSWER_900, selection=httpx.ConnectError("boom"), case=httpx.ConnectError("boom"))

    _, text, extras = await _route_turn(
        monkeypatch,
        question="Kan ik iemand spreken over het storneren van mijn incasso?",
        band="high",
        items=_two_articles(),
        litellm=litellm,
    )

    assert text == contact_reply("nl")
    assert extras["escalation"] == [{"appointment": True}]
    assert litellm.answer_requests == []


async def test_the_contact_reply_names_the_channels_the_widget_has(monkeypatch):
    """Four visitors asked for an e-mail address or did not want to call and got the appointment reply each time."""
    from app.api import partner

    monkeypatch.setattr(
        partner,
        "_widget_settings",
        AsyncMock(return_value=partner.WidgetSettings(contact_email="hallo@example.com", contact_phone="000-0000000")),
    )
    litellm, text, extras = await _selected_turn(
        monkeypatch, _selection("not_in_passages"), case=_case("human", wants_quote="hoe kan ik die incasso storneren")
    )

    assert text == f"{contact_reply('nl')} Mailen kan ook: hallo@example.com. Bellen kan ook: 000-0000000."
    assert extras["escalation"] == [{"appointment": True}]
    assert litellm.answer_requests == []


async def test_a_two_part_message_that_depends_on_a_variant_is_written_with_the_question_in_it(monkeypatch):
    """Sent as the whole reply, the variant question dropped the part the
    passages did answer."""
    question = "Gaat het om een zakelijke of een particuliere rekening?"
    litellm = _LiteLLM(
        model_text=ANSWER_900,
        selection=_selection(
            "depends", (2, "Een incasso storneer je binnen acht weken via je eigen bank."), question=question
        ),
    )

    _, text, _ = await _route_turn(
        monkeypatch,
        question="Hoe storneer ik een incasso? En kan ik mijn factuur ook per post krijgen?",
        band="high",
        items=_two_articles(),
        litellm=litellm,
    )

    prompt = _system_prompt_sent(litellm)
    assert "- Een incasso storneer je binnen acht weken via je eigen bank." in prompt
    assert f"For the part that depends on it, ask: {question}" in prompt
    assert text != question


async def test_a_widget_without_channels_gets_the_contact_reply_unchanged(monkeypatch):
    _, text, _ = await _selected_turn(
        monkeypatch, _selection("not_in_passages"), case=_case("human", wants_quote="hoe kan ik die incasso storneren")
    )

    assert text == (
        "Je kunt een afspraak maken met een medewerker, die helpt je persoonlijk verder. Gebruik daarvoor de knop "
        "onder dit bericht."
    )


async def test_a_knowledge_question_whose_selection_points_at_no_sentence_gets_the_not_found_reply(monkeypatch):
    """ "answers" with empty evidence is meant for a thank-you or a request for
    a person, which the case routes; on a knowledge question it handed the
    writer every passage."""
    litellm, text, extras = await _selected_turn(monkeypatch, _selection("answers"))

    assert text == REFUSAL_NL
    assert extras["escalation"] == [{"appointment": True}]
    assert litellm.answer_requests == []


async def test_a_frustrated_visitor_whose_passages_do_not_answer_gets_the_refusal_too(monkeypatch):
    """Frustration used to send the turn to the writer with every passage in
    the prompt, and the writer invented a cause. The honest "not found" already
    carries the appointment, which is what a frustrated visitor is offered."""
    litellm, text, extras = await _selected_turn(
        monkeypatch, _selection("not_in_passages"), case={**_case(), "sentiment": "negative"}
    )

    assert text == REFUSAL_NL
    assert extras["escalation"] == [{"appointment": True}]
    assert litellm.answer_requests == []


async def test_a_chosen_passage_stays_the_source_when_it_words_the_question_differently(monkeypatch):
    """The visitor says "belgegevens downloaden", the article "gespreksgegevens
    exporteren". A word-overlap check between question and source dropped the
    source, and the reply without a source became a refusal. The selection
    step already established that the passage answers the question."""
    article = {
        "chunk_id": "c7",
        "evidence_id": "ev7",
        "title": "Belkosten",
        "text": "Exporteer je gespreksgegevens met de knop Exporteren. Kies een periode van hooguit dertig dagen.",
        "source_url": "https://help.example.com/belkosten",
        "reranker_score": 0.03,
    }
    reply = "Kies een periode van hooguit dertig dagen en klik op Exporteren."
    litellm = _LiteLLM(
        model_text=reply,
        selection=_selection("answers", (1, "Kies een periode van hooguit dertig dagen.")),
    )

    _, text, extras = await _route_turn(
        monkeypatch,
        question="Mijn lijst met belgegevens downloaden blijft leeg",
        band="low",
        items=[article],
        litellm=litellm,
    )

    assert text == reply
    assert [s["url"] for group in extras["sources"] for s in group] == ["https://help.example.com/belkosten"]


async def test_a_question_that_is_not_one_plain_line_is_not_sent_to_the_visitor(monkeypatch):
    """The question goes out as the model wrote it, so it has to be one short
    line ending on a question mark: no link, no instruction. Otherwise the
    writer answers from the chosen passages."""
    litellm, text, _ = await _selected_turn(
        monkeypatch,
        _selection(
            "depends",
            (1, "Je betaalt je factuur via automatische incasso."),
            question="Kijk op https://evil.example.com voor je rekeningtype",
        ),
    )

    assert "evil.example.com" not in text
    assert len(litellm.answer_requests) == 1


async def test_the_writer_is_pointed_at_the_passages_own_words_not_the_models_copy(monkeypatch):
    """The model may change a word while copying. What the writer is told to
    build on is the line as it stands in the article."""
    litellm, _, _ = await _selected_turn(
        monkeypatch, _selection("answers", (2, "Een incasso storneer je binnen acht dagen via je eigen bank."))
    )

    prompt = _system_prompt_sent(litellm)
    assert "- Een incasso storneer je binnen acht weken via je eigen bank." in prompt
    assert "acht dagen" not in prompt


async def test_without_a_selection_a_reply_with_nothing_supported_is_not_shown():
    """Fail direction: when the selection step is out, the writer read every
    passage. The check then is the only net, and a reply it supports nowhere
    becomes the refusal."""
    draft = "Bel 020-7001234 om te storneren. Het bedrag staat binnen 3 werkdagen terug."
    litellm = _LiteLLM(
        model_text=draft,
        grounding=_grounding("Bel 020-7001234 om te storneren.", "Het bedrag staat binnen 3 werkdagen terug."),
    )

    text, signals, extras = await _answer(litellm, stream=True, **_with_900_sources())

    assert text == REFUSAL_NL
    assert extras["sources"] == []
    assert signals["refused"] is True


async def test_a_turn_the_turn_judge_calls_conversational_still_gets_the_refusal(monkeypatch):
    litellm, text, _ = await _selected_turn(monkeypatch, _selection("not_in_passages"), case=_case("chat"))

    assert text == REFUSAL_NL
    assert litellm.answer_requests == []


# ─── The case: what the visitor needs now, kept across turns ─────────────


_FOLLOW_UP = [
    {"role": "user", "content": "Ik bel via de app en de klant ziet een anonieme beller. Hoe los ik dit op?"},
    {"role": "assistant", "content": "Gebruik je de Android-app of de iPhone-app?"},
    {"role": "user", "content": "Android"},
]


async def _follow_up_turn(monkeypatch, case: Any, **overrides):
    litellm = _LiteLLM(model_text=ANSWER_900, case=case)
    return await _route_turn(
        monkeypatch,
        band="high",
        items=_two_articles(),
        litellm=litellm,
        messages=_FOLLOW_UP,
        **overrides,
    )


def _retrieve_body(litellm: _LiteLLM) -> dict:
    (body,) = litellm.retrieve_requests
    return body


async def test_a_follow_up_is_searched_with_the_case_s_queries_beside_the_visitor_s_words(monkeypatch):
    """ "Android" alone finds the Android article and nothing about the anonymous
    caller. The case carries the problem; its two queries run as extra passes
    and the visitor's own word stays the primary query."""
    litellm, _, _ = await _follow_up_turn(
        monkeypatch,
        _case("knowledge", "Android-app anonieme beller nummerweergave", "Android-app uitgaand nummer tonen"),
    )

    body = _retrieve_body(litellm)
    assert body["query"] == "Android"
    assert body["query_variants"] == ["Android-app anonieme beller nummerweergave", "Android-app uitgaand nummer tonen"]


@pytest.mark.parametrize(("label", "expected"), [("orbit", "Orbit"), ("Nimbus", "")])
async def test_the_case_carries_a_product_only_when_it_is_a_known_label(label, expected):
    """Knowledge-base plan, step 7: a label the org's chunks do not carry would
    prefer nothing in the search and mislead the selection."""
    answer = case_file.Case.model_validate({**_case(), "product_label": label})
    with patch.object(case_file, "structured_judge_call", AsyncMock(return_value=answer)):
        case = await case_file.update_case(_FOLLOW_UP, None, _settings(), products=["Orbit", "Zephyr"])

    assert case is not None
    assert case.product_label == expected


def test_a_first_question_names_a_product_only_by_a_known_label_in_its_own_words():
    products = ["Orbit", "Orbit Desk"]

    assert case_file.named_product("Doorschakelen op de Orbit Desk?", products) == "Orbit Desk"
    assert case_file.named_product("Werkt orbit op Android?", products) == "Orbit"
    assert case_file.named_product("Een orbital telefoon", products) == ""


def test_a_single_known_label_is_never_the_question_s_product():
    """A list of one is not a choice: with one labelled knowledge base beside
    unlabelled ones the case named that label for nearly every question and the
    search preferred it over the right answers (plan §6, 24 October 2026)."""
    assert case_file._known_product("Orbit", ["Orbit"]) == ""
    assert case_file._known_product("Orbit", ["Orbit", "Zephyr"]) == "Orbit"
    assert case_file.named_product("Doorschakelen op de Orbit?", ["Orbit"]) == ""


@pytest.mark.parametrize(("products", "expected"), [(["Orbit", "Zephyr"], "Orbit"), (["Orbit"], "")])
async def test_a_follow_up_about_a_known_product_prefers_it_in_the_search_and_tells_the_selection(
    monkeypatch, products, expected
):
    """A lookalike of another product reads like the answer; the selection is
    told which product the question is about and which product each passage is.
    With a single known label nothing is preferred and the selection is told nothing."""
    items = [
        {**CHUNK_900, "reranker_score": 0.95, "product": "zephyr"},
        {**_two_articles()[1], "product": "orbit"},
    ]
    litellm = _LiteLLM(model_text=ANSWER_900, case={**_case("knowledge"), "product_label": "orbit"})
    with patch(
        "app.services.widget_turn.knowledge_ingest_client.get_products",
        AsyncMock(return_value=products),
    ):
        litellm, _, _ = await _route_turn(monkeypatch, band="high", items=items, litellm=litellm, messages=_FOLLOW_UP)

    assert _retrieve_body(litellm).get("product", "") == expected
    assert "\n".join(f"- {product}" for product in products) in litellm.case_requests[0]["messages"][0]["content"]
    (selection,) = [
        body
        for body in litellm.requests
        if (body.get("response_format") or {}).get("json_schema", {}).get("name") == "passage_selection"
    ]
    content = selection["messages"][-1]["content"]
    assert ("The question is about the product Orbit." in content) == bool(expected)
    assert ("[1] Factuur betalen (product: zephyr)" in content) == bool(expected)


async def test_the_next_turn_s_case_starts_from_the_stored_one():
    stored = _case("knowledge", "Android-app anonieme beller", problem="De klant ziet een anonieme beller.")
    seen: dict[str, Any] = {}

    async def fake_call(**kwargs):
        seen.update(kwargs)
        return None

    with patch.object(case_file, "structured_judge_call", fake_call):
        await case_file.update_case(_FOLLOW_UP, stored, _settings())

    content = seen["user_content"]
    assert "De klant ziet een anonieme beller." in content
    assert content.index("Case after the previous turn") < content.index("Conversation:")


async def test_a_visitor_who_wants_contact_is_not_told_the_articles_lack_an_answer(monkeypatch):
    """ "ik wil jullie mailen" got the not-found text: neither the phrase detector
    nor the turn judge called it a request for a person. The case does."""
    litellm = _LiteLLM(
        model_text=ANSWER_900,
        case=_case("contact", wants_quote="ik wil jullie mailen"),
        selection=_selection("not_in_passages"),
    )
    messages = [*_FOLLOW_UP[:2], {"role": "user", "content": "ik wil jullie mailen"}]
    litellm, text, extras = await _route_turn(
        monkeypatch, band="high", items=_two_articles(), litellm=litellm, messages=messages
    )

    assert text == contact_reply("nl")
    assert extras["escalation"] == [{"appointment": True}]
    assert litellm.answer_requests == []


async def test_a_first_question_keeps_its_paraphrases_whatever_the_case_suggests(monkeypatch):
    litellm = _LiteLLM(model_text=ANSWER_900, case=_case("knowledge", "zoekvraag uit de zaak"))
    with patch.object(
        query_paraphrase, "paraphrase_first_question", AsyncMock(return_value=["parafrase een", "parafrase twee"])
    ):
        litellm, _, _ = await _route_turn(monkeypatch, band="high", litellm=litellm)

    assert _retrieve_body(litellm)["query_variants"] == ["parafrase een", "parafrase twee"]


async def test_a_failed_case_leaves_the_turn_as_it_was(monkeypatch):
    litellm, text, _ = await _follow_up_turn(monkeypatch, httpx.ConnectError("boom"))

    assert _retrieve_body(litellm)["query_variants"] is None
    assert text == ANSWER_900


def test_a_stored_case_counts_only_when_it_belongs_to_this_conversation_s_last_answer():
    """One session token serves every conversation the widget starts in it, so
    the stored case travels with the text of the turn it was stored with."""
    stored_case = _case("knowledge", "zoekvraag", problem="Een ander probleem.")

    other = case_file.StoredTurn(stored_case, "Een antwoord uit gesprek A.", [], None)
    this = case_file.StoredTurn(stored_case, _FOLLOW_UP[1]["content"], [], None)

    assert case_file.stored_case_for(_FOLLOW_UP, other) is None
    assert case_file.stored_case_for(_FOLLOW_UP, this) == this
    assert case_file.stored_case_for(_FOLLOW_UP, None) is None


async def test_a_follow_up_that_asks_for_contact_is_answered_before_any_search(monkeypatch):
    litellm = _LiteLLM(model_text=ANSWER_900, case=_case("contact", wants_quote="ik wil jullie mailen"))
    messages = [*_FOLLOW_UP[:2], {"role": "user", "content": "ik wil jullie mailen"}]
    litellm, text, _ = await _route_turn(
        monkeypatch, band="high", items=_two_articles(), litellm=litellm, messages=messages
    )

    assert text == contact_reply("nl")
    assert litellm.retrieve_requests == []


# ─── The case quotes its routes, remembers chosen passages, keeps one language ───


_ABROAD = "Ik wil ook naar Duitse nummers kunnen bellen vanuit de app."


@pytest.mark.parametrize(
    ("message", "contact"),
    [(_ABROAD, False), ("Ik wil ook naar Duitse nummers bellen, kan ik iemand van jullie spreken?", True)],
)
async def test_a_contact_wish_counts_only_when_its_quote_stands_in_the_latest_message(monkeypatch, message, contact):
    """A message about calling numbers abroad was read as a request for a person
    and got the contact reply instead of an answer. The case must quote the
    visitor's words; a quote the message does not carry makes a knowledge turn."""
    litellm = _LiteLLM(model_text=ANSWER_900, case=_case("human", wants_quote="kan ik iemand van jullie spreken"))
    messages = [*_FOLLOW_UP[:2], {"role": "user", "content": message}]
    litellm, text, _ = await _route_turn(
        monkeypatch, band="high", items=_two_articles(), litellm=litellm, messages=messages
    )

    assert (text == contact_reply("nl")) is contact
    assert bool(litellm.answer_requests) is not contact


async def test_a_yes_to_an_offered_appointment_keeps_the_contact_route(monkeypatch):
    """ "ja graag" to an offered appointment is the contact wish, and those words are its quote."""
    offer = "Wil je een afspraak maken met een medewerker?"
    messages = [
        {"role": "user", "content": "Kan ik een afspraak maken met iemand van jullie?"},
        {"role": "assistant", "content": offer},
        {"role": "user", "content": "ja graag"},
    ]
    litellm = _LiteLLM(model_text=ANSWER_900, case=_case("appointment", wants_quote="ja graag"))
    litellm, text, _ = await _route_turn(
        monkeypatch, band="high", items=_two_articles(), litellm=litellm, messages=messages
    )

    assert text == contact_reply("nl")
    assert litellm.answer_requests == []


@pytest.mark.parametrize(
    ("message", "field", "quote"),
    [
        ("wat zijn jullie prijzen voor 5 gebruikers", "out_of_scope", "prijzen"),
        ("ik wil graag een medewerker spreken", "wants", "medewerker"),
        ("Ik wil jullie mailen", "wants", "jullie mailen"),
    ],
)
async def test_a_quote_of_one_or_two_words_holds_its_route_when_it_stands_as_a_phrase(message, field, quote):
    answer = (
        _case(out_of_scope=True, out_of_scope_quote=quote)
        if field == "out_of_scope"
        else _case("human", wants_quote=quote)
    )
    with patch.object(
        case_file, "structured_judge_call", AsyncMock(return_value=case_file.Case.model_validate(answer))
    ):
        case = await case_file.update_case([{"role": "user", "content": message}], None, _settings())

    assert case is not None
    assert getattr(case, field) == answer[field]


_KEPT = {
    "chunk_id": "c7",
    "kb_slug": "kb-a",
    "title": "Uitgaand nummer in de app",
    "source_url": "https://help.example.com/uitgaand-nummer",
    "text": "Je kiest het uitgaande nummer in de app via Instellingen en dan Nummerweergave.",
}


def _selection_passages(litellm: _LiteLLM) -> str:
    (selection_request,) = [
        body
        for body in litellm.requests
        if body.get("response_format", {}).get("json_schema", {}).get("name") == "passage_selection"
    ]
    return selection_request["messages"][1]["content"].split("Passages:\n", 1)[1]


async def _kept_turn(monkeypatch, kept: dict, items: list[dict]):
    stored = case_file.StoredTurn(_case(), _FOLLOW_UP[1]["content"], [kept], "answer")
    litellm = _LiteLLM(model_text=_KEPT["text"], selection=_selection("answers", (1, _KEPT["text"])))
    return await _route_turn(monkeypatch, band="high", items=items, litellm=litellm, messages=_FOLLOW_UP, stored=stored)


async def test_a_follow_up_whose_search_lost_the_chosen_passage_still_offers_it_to_the_selection(monkeypatch):
    """The article that answered turn 1 was gone from the search in turn 4,
    and that turn said "not found". The passage the selection chose is kept
    with the turn and offered again, and the reply written from it cites it."""
    litellm, _, extras = await _kept_turn(monkeypatch, _KEPT, _two_articles())

    passages = _selection_passages(litellm)
    assert passages.startswith(f"[1] {_KEPT['title']}\n{_KEPT['text']}")
    assert "[3] Incasso storneren" in passages
    assert [source["url"] for batch in extras["sources"] for source in batch] == [_KEPT["source_url"]]


async def test_a_follow_up_whose_search_finds_nothing_is_written_from_the_kept_passage(monkeypatch):
    litellm, text, extras = await _kept_turn(monkeypatch, _KEPT, [])

    assert _selection_passages(litellm).startswith(f"[1] {_KEPT['title']}")
    assert litellm.answer_requests
    assert text.startswith(_KEPT["text"])
    assert [source["url"] for batch in extras["sources"] for source in batch] == [_KEPT["source_url"]]


async def test_a_kept_passage_from_a_knowledge_base_out_of_scope_is_not_offered(monkeypatch):
    litellm, _, _ = await _kept_turn(monkeypatch, {**_KEPT, "kb_slug": "kb-other"}, _two_articles())

    assert _KEPT["text"] not in _selection_passages(litellm)


def test_the_kept_passages_are_a_rolling_window_of_three():
    def passage(n: int) -> dict:
        return {**_KEPT, "chunk_id": f"c{n}", "text": f"Passage {n}."}

    kept = kept_passages_of([passage(2)], [passage(1), passage(2), passage(3), passage(4)])

    assert [item["chunk_id"] for item in kept] == ["c2", "c1", "c3"]


async def test_a_short_follow_up_in_a_dutch_conversation_gets_the_dutch_not_found_text(monkeypatch):
    """Detected per message, a two-word follow-up got an English refusal in a
    Dutch conversation; the case judges the language on the whole conversation."""
    messages = [
        {"role": "user", "content": "Hoe stel ik een wachtrij in voor mijn team?"},
        {"role": "assistant", "content": "Dat doe je in de belplanner."},
        {"role": "user", "content": "Desk phone setup?"},
    ]
    litellm = _LiteLLM(model_text=ANSWER_900, case=_case(language="nl"), selection=_selection("not_in_passages"))
    _, text, _ = await _route_turn(monkeypatch, band="high", items=_two_articles(), litellm=litellm, messages=messages)

    assert text == REFUSAL_NL


async def test_a_second_contact_request_in_a_row_gets_the_again_text(monkeypatch):
    """The contact reply went out four times in a row, word for word, to a
    visitor who had said they did not want to call."""
    first = [{"role": "user", "content": "Kan ik iemand van jullie spreken?"}]
    case = _case("human", wants_quote="kan ik iemand van jullie spreken")
    _, first_text, _ = await _route_turn(
        monkeypatch, litellm=_LiteLLM(model_text=ANSWER_900, case=case), messages=first
    )
    second = [
        *first,
        {"role": "assistant", "content": first_text},
        {"role": "user", "content": "Ik wil niet bellen, kan ik iemand van jullie spreken?"},
    ]
    stored = case_file.StoredTurn(case, first_text, [], "human_request")
    _, second_text, extras = await _route_turn(
        monkeypatch, litellm=_LiteLLM(model_text=ANSWER_900, case=case), messages=second, stored=stored
    )

    assert first_text == contact_reply("nl")
    assert second_text == contact_reply_again("nl")
    assert extras["escalation"] == [{"appointment": True}]


# ─── The provenance gate: addresses and numbers only from the passages ───


def _article_with(text: str) -> dict:
    return {
        "chunk_id": "c3",
        "evidence_id": "ev3",
        "title": "Klik-en-bel koppeling",
        "text": text,
        "source_url": "https://help.example.com/klik-en-bel",
        "reranker_score": 0.9,
    }


async def _gated_turn(monkeypatch, reply: str, article_text: str):
    article = _article_with(article_text)
    litellm = _LiteLLM(model_text=reply, selection=_selection("answers", (1, article_text[:60])))
    return await _route_turn(
        monkeypatch,
        question="Wat is het adres van de koppeling?",
        band="high",
        items=[article],
        litellm=litellm,
    )


async def test_an_address_that_stands_in_the_passage_reaches_the_visitor_and_an_invented_one_does_not(monkeypatch):
    """A visitor asked twice for the API address and got an empty line: the
    reply stripper removed every URL, the one from the article included."""
    _, text, _ = await _gated_turn(
        monkeypatch,
        "Het adres is https://api.example.com/clicktodial/ en de uitleg staat op https://evil.example.com/phish.",
        "Stuur de aanvraag naar https://api.example.com/clicktodial/ met je token.",
    )

    assert "https://api.example.com/clicktodial/" in text
    assert "evil.example.com" not in text


async def test_a_phone_number_that_is_not_in_the_passage_takes_its_sentence_with_it(monkeypatch):
    """The widget gave a Belgian number from another article three times and
    an invented head-office number once; the check flagged the sentence and
    nothing acted."""
    _, text, _ = await _gated_turn(
        monkeypatch,
        "Vul het formaat +31507009999 in. Lukt dat niet, bel dan 03 303 4020.",
        "Gebruik het formaat +31507009999, zonder spaties.",
    )

    assert "+31507009999" in text
    assert "303 4020" not in text
    assert "Lukt dat niet" not in text


def test_the_contact_gate_reads_dotted_numbers_keeps_the_next_sentence_and_drops_an_orphan_marker():
    text = (
        "1. Bel 03.303.40.20. gebruik daarna de handleiding.\n"
        "2. Vul het formaat +31 (0)50 700 9999 in.\n"
        "Tussen 01-09-2026 - 30-09-2026 geldt de actie."
    )
    passage = [{"text": "Gebruik het formaat 050 700 9999 zonder spaties."}]

    gated = chat_safety.without_unsourced_contact_details(text, passage)

    assert "303" not in gated
    assert "gebruik daarna de handleiding." in gated
    assert "+31 (0)50 700 9999" in gated
    assert "01-09-2026 - 30-09-2026" in gated
    assert "\n1.\n" not in f"\n{gated}\n"


async def test_the_judges_cannot_hand_the_visitor_the_ungated_draft(monkeypatch):
    """The judges may return the draft instead of the composed text; that text
    passes the same gate."""
    reply = "Bel ons op 03 303 4020. Dan kijken we mee."
    litellm = _LiteLLM(model_text=reply, answer_judge=_answer_verdict("answered"), grounding=httpx.ConnectError("x"))

    text, _, _ = await _answer(litellm, stream=False)

    assert "303 4020" not in text
