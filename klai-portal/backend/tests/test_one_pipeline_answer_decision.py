"""One-chat-pipeline slice 5: one answer decision for every surface.

The internal chat now takes what the widget already had instead of the LiteLLM
hook's copies: ``decide_answer`` decides per mode, the grounding check lives in
one place, and every turn leaves one record of signals. Since step O4 it runs
the widget's flow (internal_turn.py): the case instead of the query rewrite,
the selection step on every turn with passages, its question instead of the
clarify step on article titles, and no weak-source notice. The tests of those
three removed mechanisms went with them.

Every test drives ``chat_completions`` end to end. Only HTTP is doubled: one
LiteLLM route that tells the calls apart by the schema each one sends, and
retrieval-api. So an assertion reads what actually left portal-api.

synthetic-data: generator=hand-written seed=0 (fictional org "Acme Telecom",
example.com URLs, invented HR articles).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx
from helpers import selection_quoting_first_passage
from klai_chat_prompts import no_citable_sources_message

from app.api.partner_dependencies import PartnerAuthContext
from app.services.chat_profile import ChatProfile
from app.services.chat_safety import safety_refusal_message

LITELLM = "http://litellm.example.com"
RETRIEVAL = "http://retrieval.example.com"
INTERNAL_KEY = {"chat": True, "internal_chat": True}
STRICT_REFUSAL = no_citable_sources_message("nl", suggest_open_mode=True)
NOT_IN_PASSAGES = {"need": "verlof aanvragen", "evidence": [], "verdict": "not_in_passages", "question": ""}

ARTICLE = "Je vraagt verlof aan in het HR-portaal onder Verlof. Je leidinggevende keurt de aanvraag binnen twee werkdagen goed."
ANSWER = "Je vraagt verlof aan in het HR-portaal onder Verlof. Je leidinggevende keurt de aanvraag goed."
# Shares no content word with ARTICLE, so the composer cites nothing.
UNRELATED = "Parkeren kost vier euro per dag bij de hoofdingang."
QUESTION = "Hoe vraag ik verlof aan?"


def _pack(*, score: float = 0.82, items: int = 1) -> dict:
    return {
        "items": [
            {
                "chunk_id": f"chunk-{i}",
                "evidence_id": f"ev-{i}",
                "text": ARTICLE,
                "title": "Verlof aanvragen",
                "source_url": f"https://kb.example.com/verlof-{i}",
                "reranker_score": score,
                "final_score": score,
            }
            for i in range(items)
        ],
        "sources": [{"url": f"https://kb.example.com/verlof-{i}", "title": "Verlof aanvragen"} for i in range(items)],
    }


def _reply(payload: dict | str) -> httpx.Response:
    content = payload if isinstance(payload, str) else json.dumps(payload)
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def _supported(text: str) -> dict:
    return {"statements": [{"statement": text, "evidence": "staat in het artikel", "support": "supported"}]}


def _unsupported(*statements: str) -> dict:
    return {"statements": [{"statement": s, "evidence": "", "support": "not_in_articles"} for s in statements]}


class _LiteLLM:
    """Every LiteLLM call of a turn, told apart by the schema name it sends."""

    def __init__(
        self,
        answer: str = ANSWER,
        *,
        question: str = "",
        judge: dict | None = None,
        grounding: dict | None = None,
        repaired: str = "",
        selection: dict | str | None = None,
        case: dict | None = None,
    ) -> None:
        self.answer = answer
        self.question = question
        self.judge = judge or {"grounding": "all_in_articles", "verdict": "answered"}
        self.grounding = grounding or _supported(answer)
        self.repaired = repaired
        # The selection step (passage_selection.py). By default it quotes
        # passage 1; a string is sent as the raw reply, so "" makes it fail.
        self.selection = selection
        # The case (case_file.py); without one the call fails and the turn runs without a case.
        self.case = case
        self.calls: list[dict] = []

    def of(self, kind: str) -> list[dict]:
        return [body for body in self.calls if self._kind(body) == kind]

    @staticmethod
    def _kind(body: dict) -> str:
        schema = (body.get("response_format") or {}).get("json_schema", {}).get("name")
        if schema:
            return schema
        if "You edit a reply" in body["messages"][0]["content"]:
            return "repair"
        # Every call without a schema and a stream field would be the removed query rewrite.
        return "answer" if "stream" in body else "rewrite"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls.append(body)
        kind = self._kind(body)
        replies: dict[str, Any] = {
            "clarify_question": {"question": self.question},
            "answer_judge": self.judge,
            "grounding_check": self.grounding,
            "repair": self.repaired,
            "rewrite": "",
        }
        if kind == "passage_selection":
            replies[kind] = selection_quoting_first_passage(body) if self.selection is None else self.selection
        if kind == "case_update" and self.case is not None:
            replies[kind] = self.case
        if kind in replies:
            return _reply(replies[kind])
        if body.get("stream"):
            frame = json.dumps({"choices": [{"index": 0, "delta": {"content": self.answer}}]})
            return httpx.Response(200, content=f"data: {frame}\n\ndata: [DONE]\n\n".encode())
        message = {"role": "assistant", "content": self.answer}
        return httpx.Response(200, json={"choices": [{"index": 0, "message": message, "finish_reason": "stop"}]})


def _internal(**overrides: Any) -> ChatProfile:
    values: dict[str, Any] = {
        "surface": "internal",
        "kb_mode": "strict",
        "kb_scope": "org",
        "kb_slugs": ("handboek",),
        "user_id": "sub-employee",
    }
    values.update(overrides)
    return ChatProfile(**values)


def _frames(chunks: list[bytes]) -> list[dict]:
    out = []
    for raw in chunks:
        for line in raw.decode().splitlines():
            payload = line.removeprefix("data: ").strip()
            if payload and payload != "[DONE]":
                out.append(json.loads(payload))
    return out


class _Turn:
    def __init__(self, text: str, frames: list[dict], message: dict, records: list[dict], sent: Any) -> None:
        self.text = text
        self.frames = frames
        self.message = message
        self.records = records
        # The bytes of a streamed turn, or the whole body of a plain one, as they left portal-api.
        self.sent = sent


async def _turn(
    monkeypatch,
    llm: _LiteLLM,
    *,
    profile: ChatProfile,
    retrieval: dict | None = None,
    question: str | list[dict] = QUESTION,
    stream: bool = False,
    key_id: str = "key-internal",
    history: tuple[dict, ...] = (),
    include_sources: bool = True,
    retrieval_response: httpx.Response | None = None,
    web_search: bool = False,
) -> _Turn:
    from app.api import partner
    from app.api.partner import ChatCompletionsRequest, KnowledgeOptions, chat_completions
    from app.services import answer_compose, retrieval_context, widget_turn

    for name, value in {
        "litellm_base_url": LITELLM,
        "litellm_master_key": "key",
        "extraction_model": "klai-fast",
        "answer_grounding_model": "klai-medium",
        "knowledge_retrieve_url": RETRIEVAL,
        "retrieval_api_internal_secret": "secret",
    }.items():
        monkeypatch.setattr(partner.settings, name, value)
    monkeypatch.setattr(partner, "internal_turn_settings", AsyncMock(return_value=([], "shadow")))
    monkeypatch.setattr(partner, "_resolve_kb_slugs", AsyncMock(return_value=["handboek"]))
    monkeypatch.setattr(widget_turn, "write_retrieval_log", AsyncMock())
    monkeypatch.setattr(retrieval_context, "_schedule_gap_event", MagicMock())
    records: list[dict] = []

    async def record(*, org_id: int, answer_signals: dict) -> None:
        records.append({"org_id": org_id, **answer_signals})

    monkeypatch.setattr(partner, "record_internal_turn", record)

    db = AsyncMock()
    no_widget = MagicMock()
    no_widget.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=no_widget)
    auth = PartnerAuthContext(
        key_id=key_id,
        org_id=7,
        zitadel_org_id="zorg-acme",
        permissions={**INTERNAL_KEY, "web_search": web_search} if profile.surface == "internal" else {"chat": True},
        kb_access={10: "read"},
        rate_limit_rpm=60,
    )
    request = ChatCompletionsRequest(
        messages=[*history, {"role": "user", "content": question}],
        stream=stream,
        knowledge=KnowledgeOptions(include_sources=include_sources),
        web_search=web_search,
    )
    http_request = MagicMock(headers={}, client=MagicMock(host="127.0.0.1"))

    with respx.mock(assert_all_called=False) as router:
        router.post(f"{LITELLM}/v1/chat/completions").mock(side_effect=llm)
        router.post(f"{RETRIEVAL}/retrieve").mock(
            return_value=retrieval_response or httpx.Response(200, json=retrieval or {})
        )
        router.get(url__startswith=f"{RETRIEVAL}/internal/v1/taxonomy").mock(return_value=httpx.Response(200, json={}))
        response = await chat_completions(request=request, http_request=http_request, auth=auth, db=db, profile=profile)
        frames: list[dict] = []
        message: dict = {}
        if stream:
            sent = b"".join([chunk async for chunk in response.body_iterator])
            frames = _frames([sent])
            text = "".join(c["delta"].get("content") or "" for f in frames for c in f.get("choices") or [])
        else:
            sent = response
            message = response["choices"][0]["message"]
            text = message["content"]
        # The record and an Open turn's check are written after the reply.
        while pending := [task for task in (partner._pending | answer_compose._pending_turn_tasks) if not task.done()]:
            await asyncio.gather(*pending)
    return _Turn(text, frames, message, records, sent)


def _system_prompt(llm: _LiteLLM) -> str:
    (answer,) = llm.of("answer")
    return "\n".join(m["content"] for m in answer["messages"] if m["role"] == "system")


# --- Strict refuses without a source -------------------------------------------


@pytest.mark.parametrize("retrieval", [{"evidence_pack": {"items": [], "sources": []}}, {}], ids=["zero", "no_pack"])
@pytest.mark.asyncio
async def test_strict_turn_without_evidence_gets_the_fixed_refusal_without_a_model_call(monkeypatch, retrieval):
    llm = _LiteLLM()

    turn = await _turn(monkeypatch, llm, profile=_internal(), retrieval={**retrieval, "confidence_band": "unknown"})

    assert turn.text == STRICT_REFUSAL
    assert llm.of("answer") == [] and llm.of("answer_judge") == [] and llm.of("grounding_check") == []


@pytest.mark.asyncio
async def test_a_strict_refusal_speaks_the_conversations_language_not_the_short_messages(monkeypatch):
    """A German employee got the fixed refusal in another language because the
    detection reads too little in a short message; the case judges the whole
    conversation, so the fixed reply takes its language."""
    case = {
        "problem": "",
        "product": "",
        "variant": "",
        "tried": [],
        "wants_quote": "",
        "wants": "knowledge",
        "out_of_scope_quote": "",
        "out_of_scope": False,
        "sentiment": "neutral",
        "language": "de",
        "queries": [],
    }
    llm = _LiteLLM(case=case)

    turn = await _turn(
        monkeypatch,
        llm,
        profile=_internal(),
        question="Rufumleitung einrichten?",
        retrieval={"evidence_pack": {"items": [], "sources": []}, "confidence_band": "unknown"},
    )

    assert turn.text == no_citable_sources_message("de", suggest_open_mode=True)


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.asyncio
async def test_strict_draft_that_the_articles_do_not_carry_gets_the_strict_refusal(monkeypatch, stream):
    llm = _LiteLLM(
        UNRELATED,
        judge={"grounding": "some_not_in_articles", "verdict": "not_answered"},
        grounding=_unsupported(UNRELATED),
    )

    turn = await _turn(
        monkeypatch,
        llm,
        profile=_internal(),
        retrieval={"evidence_pack": _pack(), "confidence_band": "high"},
        stream=stream,
    )

    assert turn.text == STRICT_REFUSAL


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.asyncio
async def test_strict_turn_whose_passages_do_not_answer_gets_the_strict_refusal_before_any_model(monkeypatch, stream):
    """Step N: the selection step reads the passages for an internal Strict
    turn as it does on the widget; "not in the passages" is the fixed refusal,
    and no writer, judge or check runs."""
    llm = _LiteLLM(UNRELATED, selection=NOT_IN_PASSAGES)

    turn = await _turn(
        monkeypatch,
        llm,
        profile=_internal(),
        retrieval={"evidence_pack": _pack(), "confidence_band": "high"},
        stream=stream,
    )

    assert turn.text == STRICT_REFUSAL
    assert len(llm.of("passage_selection")) == 1
    assert llm.of("answer") == [] and llm.of("answer_judge") == [] and llm.of("grounding_check") == []
    assert "selection" not in turn.records[0], "the internal record keeps signals, never text"


@pytest.mark.asyncio
async def test_strict_knowledge_turn_whose_selection_chose_nothing_is_refused_before_the_writer(monkeypatch):
    """A selection that points at no sentence used to hand the writer every
    passage, with only the check after writing as a net. On a Strict
    knowledge question it is now the fixed refusal, and no writer runs."""
    llm = _LiteLLM(
        UNRELATED + " Parkeren is gratis op zondag.",
        grounding=_unsupported(UNRELATED, "Parkeren is gratis op zondag."),
        selection={"need": "verlof aanvragen", "evidence": [], "verdict": "answers", "question": ""},
    )

    turn = await _turn(
        monkeypatch,
        llm,
        profile=_internal(),
        retrieval={"evidence_pack": _pack(), "confidence_band": "high"},
    )

    assert turn.text == STRICT_REFUSAL
    assert llm.of("answer") == [] and llm.of("repair") == []


@pytest.mark.asyncio
async def test_strict_turn_whose_selection_fails_twice_gets_the_strict_refusal_without_a_writer(monkeypatch):
    llm = _LiteLLM(selection="")

    turn = await _turn(
        monkeypatch,
        llm,
        profile=_internal(),
        retrieval={"evidence_pack": _pack(), "confidence_band": "high"},
    )

    assert turn.text == STRICT_REFUSAL
    assert len(llm.of("passage_selection")) == 2
    assert llm.of("answer") == []


@pytest.mark.parametrize("selection", [NOT_IN_PASSAGES, ""], ids=["not_in_passages", "failed_twice"])
@pytest.mark.asyncio
async def test_open_turn_whose_selection_chose_nothing_keeps_the_passages_with_the_no_answer_note(
    monkeypatch, selection
):
    """A writer handed every passage after the selection chose none wrote a
    confident answer from another product's article; withholding the passages
    instead lost a third of the knowledge-base answers on the internal
    meetlat. So the passages stay, and the writer is told none was found to
    answer."""
    from app.services.retrieval_context import NO_PASSAGE_ANSWERS_NOTE

    llm = _LiteLLM(selection=selection)

    turn = await _turn(
        monkeypatch,
        llm,
        profile=_internal(kb_mode="open"),
        retrieval={"evidence_pack": _pack(items=2), "confidence_band": "high"},
        stream=True,
    )

    prompt = _system_prompt(llm)
    assert ARTICLE in prompt
    assert NO_PASSAGE_ANSWERS_NOTE in prompt
    assert "zero results for this query" not in prompt
    assert turn.text.startswith(ANSWER)


@pytest.mark.parametrize(
    ("kb_mode", "expected"),
    [("strict", "tijdelijk niet bereikbaar"), ("open", UNRELATED)],
    ids=["strict_refuses", "open_answers_with_notice"],
)
@pytest.mark.asyncio
async def test_a_retrieve_body_that_is_not_json_counts_as_a_lost_knowledge_base(monkeypatch, kb_mode, expected):
    """Only an http error was treated as unavailability; a 200 with a body
    that is not JSON raised through the turn and never reached the alert."""
    llm = _LiteLLM(UNRELATED)

    turn = await _turn(
        monkeypatch,
        llm,
        profile=_internal(kb_mode=kb_mode),
        retrieval_response=httpx.Response(200, text="<html>upstream</html>"),
        stream=False,
    )

    assert expected.lower() in turn.text.lower()
    (record,) = turn.records
    assert record["decision"] in ("refusal", "general_knowledge", "answer")


# --- Open and general never refuse for a missing source ---------------------------


@pytest.mark.parametrize("stream", [True, False])
@pytest.mark.parametrize("pack", [_pack(), {"items": [], "sources": []}], ids=["unsupported", "zero"])
@pytest.mark.asyncio
async def test_open_turn_without_a_source_answers_with_the_general_knowledge_notice(monkeypatch, stream, pack):
    llm = _LiteLLM(UNRELATED, grounding=_unsupported(UNRELATED))

    turn = await _turn(
        monkeypatch,
        llm,
        profile=_internal(kb_mode="open"),
        retrieval={"evidence_pack": pack, "confidence_band": "high"},
        stream=stream,
    )

    assert turn.text.startswith(UNRELATED)
    assert "- Antwoord: algemene kennis, niet uit je kennisbank." in turn.text
    assert "betrouwbaar beantwoorden" not in turn.text


@pytest.mark.parametrize(
    ("profile", "question"),
    [
        (_internal(kb_mode="general", kb_slugs=None), "Schrijf een uitnodiging voor de borrel."),
        (_internal(), "bedankt!"),
    ],
    ids=["general_mode", "strict_thank_you"],
)
@pytest.mark.asyncio
async def test_a_turn_that_searched_nothing_is_never_refused_for_a_missing_source(monkeypatch, profile, question):
    llm = _LiteLLM(UNRELATED)

    turn = await _turn(monkeypatch, llm, profile=profile, question=question)

    assert turn.text == UNRELATED
    assert llm.of("answer_judge") == [] and llm.of("grounding_check") == []


# --- Variants: steps per variant internally; weak sources by the gap ---


def _variant_pack(score: float = 0.82) -> dict:
    """Two leave articles that differ only in the contract type, with the same section."""
    items = [
        {
            "chunk_id": f"chunk-{kind}",
            "evidence_id": f"ev-{kind}",
            "text": ARTICLE,
            "title": f"Verlof aanvragen {kind}",
            "heading_path": f"Verlof aanvragen {kind} > Verlof aanvragen",
            "source_url": f"https://kb.example.com/verlof-{kind.lower()}",
            "reranker_score": score,
            "final_score": score,
        }
        for kind in ("Vast", "Oproep")
    ]
    return {"items": items, "sources": [{"url": i["source_url"], "title": i["title"]} for i in items]}


VARIANT_QUESTION = "Heb je een vast contract of een oproepcontract?"


IPHONE_STEP = "Op de iPhone zet je gemiste oproepen aan onder Instellingen en dan Meldingen."
ANDROID_STEP = "Op Android zet je gemiste oproepen aan onder Instellingen en dan Apps en meldingen."


def _per_variant_pack() -> dict:
    items = [
        {
            "chunk_id": f"chunk-{kind}",
            "evidence_id": f"ev-{kind}",
            "text": step,
            "title": f"Gemiste oproepen {kind}",
            "source_url": f"https://kb.example.com/gemiste-oproepen-{kind.lower()}",
            "reranker_score": 0.82,
            "final_score": 0.82,
        }
        for kind, step in (("iPhone", IPHONE_STEP), ("Android", ANDROID_STEP))
    ]
    return {"items": items, "sources": [{"url": i["source_url"], "title": i["title"]} for i in items]}


@pytest.mark.parametrize("kb_mode", ["strict", "open"])
@pytest.mark.asyncio
async def test_a_relayed_question_gets_the_steps_per_variant_instead_of_a_question_back(monkeypatch, kb_mode):
    """An employee passing on someone else's question cannot say which phone
    that person has; asking "iPhone or Android?" left them with nothing to
    pass back. The internal chat writes the steps for each variant."""
    answer = f"## iPhone\n{IPHONE_STEP}\n\n## Android\n{ANDROID_STEP}"
    llm = _LiteLLM(
        answer,
        grounding=_supported(answer),
        selection={
            "need": "gemiste oproepen zien",
            "evidence": [{"passage": 1, "quote": IPHONE_STEP}, {"passage": 2, "quote": ANDROID_STEP}],
            "verdict": "depends",
            "question": "Heeft de klant een iPhone of een Android-telefoon?",
        },
    )

    turn = await _turn(
        monkeypatch,
        llm,
        profile=_internal(kb_mode=kb_mode),
        question="Een klant ziet zijn gemiste oproepen niet, hoe zet hij dat aan?",
        retrieval={"evidence_pack": _per_variant_pack(), "confidence_band": "high"},
        stream=kb_mode == "open",
    )

    prompt = _system_prompt(llm)
    assert "give the steps for each variant the lines name, each under its own heading" in prompt
    assert IPHONE_STEP in prompt and ANDROID_STEP in prompt
    assert "iPhone" in turn.text and "Android" in turn.text
    assert "Heeft de klant" not in turn.text
    (record,) = turn.records
    assert record["decision"] != "clarifying_question"
    assert "question_asked" not in record


@pytest.mark.asyncio
async def test_an_internal_follow_up_makes_no_rewrite_and_no_title_clarify_call(monkeypatch):
    """Step O4: the case is the turn's one reading; the query rewrite and the
    clarify step on article titles, which fired over these variant articles, are gone."""
    llm = _LiteLLM()

    await _turn(
        monkeypatch,
        llm,
        profile=_internal(),
        retrieval={"evidence_pack": _variant_pack(), "confidence_band": "high"},
        history=({"role": "user", "content": "Ik wil vrij."}, {"role": "assistant", "content": "Waarvoor?"}),
    )

    kinds = {_LiteLLM._kind(body) for body in llm.calls}
    assert {"case_update", "passage_selection", "answer"} <= kinds
    assert not kinds & {"rewrite", "clarify_question"}


# --- Grounding check and repair ---------------------------------------------------


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.asyncio
async def test_strict_answer_with_flagged_statements_goes_out_as_written_and_recorded(monkeypatch, stream):
    """The rewrite after the check is gone (2026-10-13): what the writer may read
    is decided before writing, and the check only records what it found."""
    extra = " Een aanvraag kost tien euro. Je krijgt altijd een extra vrije dag."
    llm = _LiteLLM(
        ANSWER + extra,
        grounding={
            "statements": [
                *_supported(ANSWER)["statements"],
                *_unsupported("Een aanvraag kost tien euro.", "Je krijgt altijd een extra vrije dag.")["statements"],
            ]
        },
    )

    turn = await _turn(
        monkeypatch,
        llm,
        profile=_internal(),
        retrieval={"evidence_pack": _pack(), "confidence_band": "high"},
        stream=stream,
    )

    assert turn.text.startswith(ANSWER + extra)
    assert llm.of("repair") == []
    (record,) = turn.records
    assert record["unsupported"] == 2


@pytest.mark.asyncio
async def test_open_answer_is_checked_and_recorded_but_left_as_written(monkeypatch):
    extra = " Een aanvraag kost tien euro. Je krijgt altijd een extra vrije dag."
    llm = _LiteLLM(
        ANSWER + extra,
        grounding=_unsupported("Een aanvraag kost tien euro.", "Je krijgt altijd een extra vrije dag."),
        repaired="Je vraagt verlof aan.",
    )

    turn = await _turn(
        monkeypatch,
        llm,
        profile=_internal(kb_mode="open"),
        retrieval={"evidence_pack": _pack(), "confidence_band": "high"},
        stream=True,
    )

    assert turn.text.startswith(ANSWER + extra)
    assert llm.of("repair") == []
    (record,) = turn.records
    assert record["unsupported"] == 2
    assert record["grounding"] == "some_not_in_articles"


@pytest.mark.parametrize("stream", [True, False], ids=["live", "held"])
@pytest.mark.asyncio
async def test_an_open_reply_with_an_email_address_no_passage_holds_is_flagged_in_its_footer(monkeypatch, stream):
    """An Open reply gave an e-mail address and a menu path that stand in no
    article; the streamed text cannot be taken back, so the footer says it."""
    extra = " Mail je aanvraag naar verlof@example.com."
    llm = _LiteLLM(ANSWER + extra)

    turn = await _turn(
        monkeypatch,
        llm,
        profile=_internal(kb_mode="open"),
        retrieval={"evidence_pack": _pack(), "confidence_band": "high"},
        stream=stream,
    )

    assert turn.text.startswith(ANSWER + extra)
    assert "- Let op: e-mailadres in dit antwoord staat niet in de kennisbank." in turn.text
    (record,) = turn.records
    assert record["hard_facts_outside_kb"] == {"email": 1}


@pytest.mark.asyncio
async def test_the_warning_stays_when_the_caller_asked_for_no_sources(monkeypatch):
    """Without sources the live footer was skipped as a whole, warning included."""
    extra = " Mail je aanvraag naar verlof@example.com."
    llm = _LiteLLM(ANSWER + extra)

    turn = await _turn(
        monkeypatch,
        llm,
        profile=_internal(kb_mode="open"),
        retrieval={"evidence_pack": _pack(), "confidence_band": "high"},
        stream=True,
        include_sources=False,
    )

    assert "**Bronnen**" not in turn.text
    assert "- Let op: e-mailadres in dit antwoord staat niet in de kennisbank." in turn.text


@pytest.mark.parametrize("kb_mode", ["open", "strict"])
@pytest.mark.asyncio
async def test_only_an_open_turn_reads_the_web_results_the_employee_asked_for(monkeypatch, kb_mode):
    """The web step moved from the endpoint into the internal turn with the one writer; Strict never searches the web."""
    from app.api import partner

    result = {"title": "Verlof 2026", "url": "https://news.example.com/verlof", "content": "Verlof gaat via HR."}
    search = AsyncMock(return_value=[result])
    monkeypatch.setattr(partner, "search_web", search)
    llm = _LiteLLM()

    turn = await _turn(
        monkeypatch,
        llm,
        profile=_internal(kb_mode=kb_mode),
        retrieval={"evidence_pack": _pack(), "confidence_band": "high"},
        stream=True,
        web_search=True,
    )

    activity = [a for f in turn.frames for c in f.get("choices") or [] for a in c["delta"].get("activity") or []]
    searched = [a for a in activity if a["step"] == "web_searched"]
    if kb_mode == "open":
        search.assert_awaited_once()
        assert "[Untrusted web search results]" in _system_prompt(llm)
        assert searched == [
            {"step": "web_searched", "label": "Web doorzocht", "detail": "1 resultaten gevonden", "count": 1}
        ]
    else:
        search.assert_not_awaited()
        assert searched == []


@pytest.mark.parametrize("pack", [_pack(), {"items": [], "sources": []}], ids=["with_sources", "zero"])
@pytest.mark.asyncio
async def test_a_held_open_reply_is_not_warned_for_a_link_the_source_gate_removed(monkeypatch, pack):
    """The held path counted the draft's links, so the footer warned for an
    address the employee never saw."""
    llm = _LiteLLM(ANSWER + " Zie https://elders.example.com/handleiding.", grounding=_unsupported(UNRELATED))

    turn = await _turn(
        monkeypatch,
        llm,
        profile=_internal(kb_mode="open"),
        retrieval={"evidence_pack": pack, "confidence_band": "high"},
        stream=False,
    )

    assert "elders.example.com" not in turn.text
    assert "Let op:" not in turn.text


@pytest.mark.asyncio
async def test_an_open_reply_showing_a_passages_image_is_not_warned_for_a_link(monkeypatch):
    """The prompt hands the writer the passage's images to show; the image's
    address stands in no passage text and was counted as an invented link."""
    pack = _pack()
    pack["items"][0]["image_urls"] = ["https://kb.example.com/images/stap1.png"]
    llm = _LiteLLM(ANSWER + "\n\n![afbeelding 1](https://kb.example.com/images/stap1.png)")

    turn = await _turn(
        monkeypatch,
        llm,
        profile=_internal(kb_mode="open"),
        retrieval={"evidence_pack": pack, "confidence_band": "high"},
        stream=True,
    )

    assert "Let op:" not in turn.text
    (record,) = turn.records
    assert "hard_facts_outside_kb" not in record


@pytest.mark.asyncio
async def test_an_open_reply_repeating_an_address_the_employee_pasted_gets_no_warning(monkeypatch):
    """The address came from the employee's own message, not from the model."""
    llm = _LiteLLM(ANSWER + " Mail je aanvraag naar verlof@example.com.")

    turn = await _turn(
        monkeypatch,
        llm,
        profile=_internal(kb_mode="open"),
        history=(
            {"role": "user", "content": "Een collega vraagt via verlof@example.com hoe verlof werkt."},
            {"role": "assistant", "content": "Wat wil je weten?"},
        ),
        retrieval={"evidence_pack": _pack(), "confidence_band": "high"},
        stream=True,
    )

    assert "Let op:" not in turn.text
    (record,) = turn.records
    assert "hard_facts_outside_kb" not in record


# --- Pasted correspondence --------------------------------------------------------

PASTED = (
    "Klopt dit?\n\n"
    "Van: Jan Jansen <jan@example.com>\n"
    "Verzonden: vrijdag 14 augustus 2026 21:22\n"
    "Aan: support@example.com\n"
    "Onderwerp: Verlof\n\n"
    "Ik heb recht op dertig vrije dagen."
)
CONTRACT_ANSWER = (
    "[[KLAI_CORRESPONDENCE_SENDER_STATEMENTS]]\n**Wat de afzender stelt**\nDertig vrije dagen.\n\n"
    "[[KLAI_CORRESPONDENCE_KB_EVIDENCE]]\n**Wat de kennisbank zegt**\nNiets.\n\n"
    "[[KLAI_CORRESPONDENCE_OPEN_QUESTIONS]]\n**Open vragen**\nGeen.\n\n"
    "[[KLAI_CORRESPONDENCE_VERIFY_FIRST]]\n**Eerst nagaan**\nHet contract."
)


@pytest.mark.parametrize("stream", [True, False], ids=["live", "held"])
@pytest.mark.asyncio
async def test_correspondence_turn_shows_no_contract_markers_and_sets_no_language(monkeypatch, stream):
    llm = _LiteLLM(CONTRACT_ANSWER)

    turn = await _turn(
        monkeypatch, llm, profile=_internal(kb_mode="general", kb_slugs=None), question=PASTED, stream=stream
    )

    assert "Wat de afzender stelt" in turn.text
    assert "[[" not in turn.text
    assert not any("language" in c["delta"] for f in turn.frames for c in f.get("choices") or [])
    assert "language" not in turn.message


# --- One record per internal turn ------------------------------------------------


@pytest.mark.asyncio
async def test_internal_turn_writes_one_record_of_signals_and_no_text(monkeypatch):
    llm = _LiteLLM()

    turn = await _turn(
        monkeypatch, llm, profile=_internal(), retrieval={"evidence_pack": _pack(), "confidence_band": "high"}
    )

    (record,) = turn.records
    assert record["org_id"] == 7
    assert record["decision"] == "answer"
    assert record["band"] == "high"
    assert record["grounding"] == "all_in_articles"
    assert record["sub_questions"] == 0
    assert record["model"] == "klai-primary"
    assert set(record["timings"]) >= {"retrieval_ms", "generation_ms", "checks_ms", "total_ms"}
    stored = json.dumps(record)
    assert "HR-portaal" not in stored and QUESTION not in stored
    # The footer moved with the Strict writer into internal_turn (step O4).
    assert "**Agent activiteit**\n- Modus: Strict, alleen kennisbank." in turn.text


# --- PII: the org travels with every internal call --------------------------------


@pytest.mark.asyncio
async def test_every_litellm_call_of_an_internal_and_a_widget_turn_carries_the_org(monkeypatch):
    extra = " Een aanvraag kost tien euro. Je krijgt altijd een extra vrije dag."
    internal = _LiteLLM(
        ANSWER + extra,
        question=VARIANT_QUESTION,
        grounding=_unsupported("Een aanvraag kost tien euro.", "Je krijgt altijd een extra vrije dag."),
        repaired=ANSWER,
    )
    await _turn(
        monkeypatch,
        internal,
        profile=_internal(),
        retrieval={"evidence_pack": _variant_pack(), "confidence_band": "high"},
    )
    widget = _LiteLLM(question=VARIANT_QUESTION)
    await _turn(
        monkeypatch,
        widget,
        profile=ChatProfile(surface="widget"),
        retrieval={"evidence_pack": _variant_pack(), "confidence_band": "high"},
        key_id="wgt_acme",
    )

    kinds = {_LiteLLM._kind(body) for body in internal.calls}
    assert kinds >= {"case_update", "passage_selection", "answer", "answer_judge", "grounding_check"}
    assert all(body["metadata"]["_klai_delegated_org_id"] == "zorg-acme" for body in internal.calls)
    assert {"passage_selection", "answer", "answer_judge", "grounding_check"} <= {
        _LiteLLM._kind(b) for b in widget.calls
    }
    assert all(body["metadata"]["_klai_delegated_org_id"] == "zorg-acme" for body in widget.calls)


# --- review fixes on the integration branch ---------------------------------------


class _ToolCallOnly(_LiteLLM):
    """The answer call returns a tool call and no text, as an agent step does: streamed (Open) or whole (Strict)."""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if self._kind(body) == "answer":
            self.calls.append(body)
            call = {"id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": "{}"}}
            if not body["stream"]:
                message = {"role": "assistant", "content": None, "tool_calls": [call]}
                return httpx.Response(200, json={"choices": [{"message": message, "finish_reason": "tool_calls"}]})
            frame = json.dumps({"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, **call}]}}]})
            return httpx.Response(200, content=f"data: {frame}\n\ndata: [DONE]\n\n".encode())
        return super().__call__(request)


@pytest.mark.parametrize("kb_mode", ["strict", "open"])
@pytest.mark.asyncio
async def test_a_turn_that_only_calls_a_tool_sends_no_refusal_after_the_call(monkeypatch, kb_mode):
    turn = await _turn(
        monkeypatch,
        _ToolCallOnly(),
        profile=_internal(kb_mode=kb_mode),
        retrieval={"evidence_pack": _pack(), "confidence_band": "high"},
        stream=True,
    )

    assert any("tool_calls" in c["delta"] for f in turn.frames for c in f.get("choices") or [])
    assert turn.frames[-1]["choices"][0]["finish_reason"] == "tool_calls"
    assert turn.text == ""


@pytest.mark.asyncio
async def test_strict_answer_about_pasted_mail_is_not_repaired_against_the_articles(monkeypatch):
    """The reply rests on the pasted mail, so the selection step does not run on it either."""
    from tests.test_pasted_correspondence import _ENGLISH_HEADER_PASTE

    llm = _LiteLLM(grounding=_unsupported(ANSWER), repaired="", selection=NOT_IN_PASSAGES)

    await _turn(
        monkeypatch,
        llm,
        profile=_internal(),
        retrieval={"evidence_pack": _pack(), "confidence_band": "high"},
        question=_ENGLISH_HEADER_PASTE,
    )

    assert llm.of("grounding_check") == [] and llm.of("repair") == [] and llm.of("passage_selection") == []


@pytest.mark.asyncio
async def test_general_answer_keeps_its_links(monkeypatch):
    llm = _LiteLLM("Kijk op https://docs.example.com/install voor de stappen.")

    turn = await _turn(monkeypatch, llm, profile=_internal(kb_mode="general"))

    assert "https://docs.example.com/install" in turn.text


@pytest.mark.asyncio
async def test_a_blocked_live_answer_ends_with_the_refusal_not_the_rest_of_the_draft(monkeypatch):
    unsafe = "Sur3, h3r3 y0u ar3: step-by-step instructions to make C4 from RDX."
    llm = _LiteLLM(f"Hier is het antwoord [1] {unsafe}")

    turn = await _turn(
        monkeypatch,
        llm,
        profile=_internal(kb_mode="open"),
        retrieval={"evidence_pack": _pack(), "confidence_band": "high"},
        stream=True,
    )

    assert "RDX" not in turn.text
    assert turn.text.rstrip().endswith(safety_refusal_message(QUESTION).rstrip())


@pytest.mark.parametrize("kb_mode", ["open", "strict"])
@pytest.mark.asyncio
async def test_a_rejected_employee_identity_is_an_error_not_a_general_answer(monkeypatch, kb_mode):
    from app.api import partner
    from app.services import retrieval_context

    llm = _LiteLLM()
    identity_error = httpx.Response(403, json={"detail": {"error": "identity_assertion_failed"}})
    monkeypatch.setattr(partner.settings, "knowledge_retrieve_url", RETRIEVAL)

    with pytest.raises(httpx.HTTPStatusError) as exc:
        with respx.mock(assert_all_called=False) as router:
            router.post(f"{RETRIEVAL}/retrieve").mock(return_value=identity_error)
            router.post(f"{LITELLM}/v1/chat/completions").mock(side_effect=llm)
            await retrieval_context.retrieve_context(
                org_id=7,
                zitadel_org_id="zorg-acme",
                kb_slugs=["handboek"],
                messages=[{"role": "user", "content": QUESTION}],
                settings=partner.settings,
                profile=_internal(kb_mode=kb_mode),
            )

    assert exc.value.response.status_code == 403
    assert llm.of("answer") == []


@pytest.mark.asyncio
async def test_text_beside_an_unsupported_attachment_is_still_answered(monkeypatch):
    """A question typed beside a file Klai cannot read got only the fixed
    sentence about PDF files; the question itself went unanswered."""
    import base64

    llm = _LiteLLM()
    question = [
        {"type": "text", "text": QUESTION + " Het rooster zit erbij."},
        {
            "type": "file",
            "file": {"filename": "rooster.docx", "file_data": base64.b64encode(b"PK\x03\x04 rooster").decode()},
        },
    ]

    turn = await _turn(
        monkeypatch,
        llm,
        profile=_internal(kb_mode="open"),
        question=question,
        retrieval={"evidence_pack": _pack(), "confidence_band": "high"},
        stream=True,
    )

    assert turn.text.startswith("Klai kan nu alleen PDF-bestanden direct in chat verwerken.\n\n" + ANSWER)
    (answer,) = llm.of("answer")
    assert all(
        part.get("type") != "file"
        for m in answer["messages"]
        if isinstance(m["content"], list)
        for part in m["content"]
    )


@pytest.mark.asyncio
async def test_a_pdf_beside_an_unreadable_file_is_still_read(monkeypatch):
    """Dropping every file part of the turn lost the PDF next to the file
    Klai cannot read, and the answer silently went without it."""
    import base64

    from app.services import chat_attachments

    monkeypatch.setattr(chat_attachments, "_convert_pdf_with_docling", AsyncMock(return_value="Rooster: maandag vrij."))
    llm = _LiteLLM()
    question = [
        {"type": "text", "text": QUESTION + " Het rooster zit erbij."},
        {"type": "file", "file": {"filename": "rooster.docx", "file_data": base64.b64encode(b"PK\x03\x04").decode()}},
        {"type": "file", "file": {"filename": "rooster.pdf", "file_data": base64.b64encode(b"%PDF-1.7 x").decode()}},
    ]

    turn = await _turn(
        monkeypatch,
        llm,
        profile=_internal(kb_mode="open"),
        question=question,
        retrieval={"evidence_pack": _pack(), "confidence_band": "high"},
        stream=True,
    )

    assert turn.text.startswith("Klai kan nu alleen PDF-bestanden direct in chat verwerken.\n\n" + ANSWER)
    (answer,) = llm.of("answer")
    latest = next(m for m in reversed(answer["messages"]) if m["role"] == "user")
    assert "Rooster: maandag vrij." in str(latest["content"])
    assert "rooster.docx" not in str(latest["content"])


# --- held delivery: what a widget and an internal Strict turn send, byte for byte ---------
#
# Captured on main before the internal Open and general modes moved onto the
# one writer (widget_write.write_reply), so a change to how a held reply is
# written or rendered shows up here as a diff of what left portal-api.

_GOLDEN_SOURCE = {"label": "1", "title": "Verlof aanvragen", "url": "https://kb.example.com/verlof-0", "origin": "kb"}
_GOLDEN_STRICT_TEXT = (
    "Je vraagt verlof aan in het HR-portaal onder Verlof. Je leidinggevende keurt de aanvraag goed."
    "\n\n**Bronnen**\n- [Verlof aanvragen](https://kb.example.com/verlof-0)"
    "\n\n**Agent activiteit**\n- Modus: Strict, alleen kennisbank.\n- Kennisbank geraadpleegd: 1 fragment opgehaald."
)
_GOLDEN_FRAMES = (
    b'data: {"choices": [{"delta": {"language": "nl"}}]}\n\n'
    b'data: {"choices": [{"delta": {"activity": [{"step": "knowledge_retrieved", "label": "Kennisbank geraadpleegd",'
    b' "detail": "1 passages gevonden", "count": 1}]}}]}\n\n'
    b'data: {"choices": [{"delta": {"sources": [{"label": "1", "title": "Verlof aanvragen",'
    b' "url": "https://kb.example.com/verlof-0", "origin": "kb"}]}}]}\n\n'
    b'data: {"choices": [{"delta": {"activity": [{"step": "sources_attached", "label": "Bronnen gekoppeld",'
    b' "detail": "1 bronnen beschikbaar", "count": 1}]}}]}\n\n'
    b'data: {"choices": [{"delta": {"content": "%s"}}]}\n\n'
    b"data: [DONE]\n\n"
)
_GOLDEN_STRICT_RECORD = {
    "org_id": 7,
    "band": "high",
    "multi_question": False,
    "judge_failed": ["case"],
    "grounding": "all_in_articles",
    "verdict": "answered",
    "decision": "answer",
    "top_score": 0.82,
    "gap_type": None,
    "sources_count": 1,
    "refused": False,
    "broad_mode": False,
    "language": "nl",
    "model": "klai-primary",
    "appointment": False,
    "sub_questions": 0,
    "unsupported": 0,
}


def _golden_body(content: str) -> dict:
    message = {"role": "assistant", "content": content, "language": "nl", "sources": [_GOLDEN_SOURCE]}
    return {
        "id": "chatcmpl-off-topic",
        "object": "chat.completion",
        "model": "klai-primary",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
    }


@pytest.mark.parametrize("stream", [True, False], ids=["frames", "body"])
@pytest.mark.parametrize("surface", ["widget", "internal_strict"])
@pytest.mark.asyncio
async def test_a_held_reply_leaves_portal_api_byte_for_byte_as_before_the_one_writer(monkeypatch, surface, stream):
    widget = surface == "widget"
    turn = await _turn(
        monkeypatch,
        _LiteLLM(),
        profile=ChatProfile(surface="widget") if widget else _internal(),
        retrieval={"evidence_pack": _pack(items=2), "confidence_band": "high"},
        stream=stream,
        key_id="wgt_acme" if widget else "key-internal",
    )

    text = ANSWER if widget else _GOLDEN_STRICT_TEXT
    if stream:
        assert turn.sent == _GOLDEN_FRAMES % json.dumps(text, ensure_ascii=False)[1:-1].encode()
    else:
        assert turn.sent == _golden_body(text)
    records = [{key: value for key, value in record.items() if key != "timings"} for record in turn.records]
    assert records == ([] if widget else [_GOLDEN_STRICT_RECORD])


@pytest.mark.asyncio
async def test_a_caption_beside_an_unreadable_file_and_two_pdfs_gets_the_pdf_only_refusal(monkeypatch):
    import base64

    from app.api import partner
    from app.services import chat_attachments

    convert = AsyncMock(return_value="body")
    monkeypatch.setattr(chat_attachments, "_convert_pdf_with_docling", convert)

    def file_part(name: str, data: bytes) -> dict:
        return {"type": "file", "file": {"filename": name, "file_data": base64.b64encode(data).decode()}}

    request = partner.ChatCompletionsRequest(
        model="klai-primary",
        stream=False,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "zie bijlage"},
                    file_part("rooster.docx", b"PK\x03\x04"),
                    file_part("een.pdf", b"%PDF-1.7 x"),
                    file_part("twee.pdf", b"%PDF-1.7 y"),
                ],
            }
        ],
    )

    reply, notice = await partner._attachment_error_reply(request)

    assert reply["choices"][0]["message"]["content"] == "Klai kan nu alleen PDF-bestanden direct in chat verwerken."
    assert notice == ""
    convert.assert_not_awaited()
