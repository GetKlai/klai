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
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx
from helpers import selection_quoting_first_passage
from klai_chat_prompts import check_unavailable_message, no_citable_sources_message

from app.api.partner_dependencies import PartnerAuthContext
from app.services.case_file import StoredTurn
from app.services.chat_profile import ChatProfile
from app.services.chat_safety import safety_refusal_message

LITELLM = "http://litellm.example.com"
RETRIEVAL = "http://retrieval.example.com"
INTERNAL_KEY = {"chat": True, "internal_chat": True}
STRICT_REFUSAL = no_citable_sources_message("nl", suggest_open_mode=True)
NOT_IN_PASSAGES = {
    "need": "verlof aanvragen",
    "parts": [{"ask": "Hoe vraag ik verlof aan?", "evidence": []}],
    "must_ask_variant": False,
    "question": "",
}

ARTICLE = "Je vraagt verlof aan in het HR-portaal onder Verlof. Je leidinggevende keurt de aanvraag binnen twee werkdagen goed."
ANSWER = "Je vraagt verlof aan in het HR-portaal onder Verlof. Je leidinggevende keurt de aanvraag goed."
# Shares no content word with ARTICLE, so the composer cites nothing.
UNRELATED = "Parkeren kost vier euro per dag bij de hoofdingang."
QUESTION = "Hoe vraag ik verlof aan?"
# A LibreChat conversation id (a UUID), synthetic.
CONVERSATION = "0f6c2a1e-5b7d-4c3a-9e2f-1a2b3c4d5e6f"


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
        grounding: dict | None = None,
        repaired: str = "",
        selection: dict | str | None = None,
        case: dict | None = None,
    ) -> None:
        self.answer = answer
        self.question = question
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
    cases: dict | None = None,
    searchable: tuple[str, ...] = ("handboek",),
    conversation_id: str | None = CONVERSATION,
    request_body: Callable[[list[dict]], dict] | None = None,
    models_from_settings: bool = False,
) -> _Turn:
    """``request_body`` builds the body a caller sent from the messages, instead of the default one;
    ``models_from_settings`` leaves the settings' model names as the caller set them (meetlat's measurement
    patches) instead of the production ones."""
    from app.api import partner
    from app.api.partner import ChatCompletionsRequest, KnowledgeOptions, chat_completions
    from app.services import answer_compose, retrieval_context, widget_turn

    for name, value in {
        "litellm_base_url": LITELLM,
        "litellm_master_key": "key",
        "knowledge_retrieve_url": RETRIEVAL,
        "retrieval_api_internal_secret": "secret",
        **({} if models_from_settings else {"extraction_model": "klai-fast", "answer_grounding_model": "klai-medium"}),
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
    # internal_chat_cases in memory, shared by the turns of one conversation.
    cases = {} if cases is None else cases

    async def last_internal_case(*, org_id: int, user_id: str, conversation_id: str) -> StoredTurn | None:
        return cases.get((org_id, user_id, conversation_id))

    async def keep_internal_case(
        *, org_id: int, user_id: str, conversation_id: str, case: dict | None, kept_passages: list[dict]
    ) -> None:
        # As widget_audit.keep_internal_case: a turn with nothing to keep clears the row.
        if case is None and not kept_passages:
            cases.pop((org_id, user_id, conversation_id), None)
            return
        cases[(org_id, user_id, conversation_id)] = StoredTurn(case, "", kept_passages, None)

    monkeypatch.setattr(partner, "last_internal_case", last_internal_case)
    monkeypatch.setattr(partner, "keep_internal_case", keep_internal_case)
    # The knowledge bases the employee may search now (access.get_accessible_kb_slugs).
    monkeypatch.setattr(partner, "get_accessible_kb_slugs", AsyncMock(return_value=list(searchable)))

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
    messages = [*history, {"role": "user", "content": question}]
    request = (
        partner._parse_knowledge_chat_request(request_body(messages))
        if request_body is not None
        else ChatCompletionsRequest(
            messages=messages,
            stream=stream,
            knowledge=KnowledgeOptions(include_sources=include_sources),
            web_search=web_search,
        )
    )
    # LibreChat's "Klai AI" endpoint sends the conversation id as a header (deploy/librechat/librechat.yaml).
    headers = {"x-klai-conversation-id": conversation_id} if conversation_id is not None else {}
    http_request = MagicMock(headers=headers, client=MagicMock(host="127.0.0.1"))

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
    assert llm.of("answer") == [] and llm.of("grounding_check") == []


@pytest.mark.asyncio
async def test_a_strict_refusal_without_evidence_leaves_a_log_line(monkeypatch):
    """On 2026-10-22 J02 failed five runs on "Strict answered TCP/IP" and the
    Strict turns showed up in the logs only as the absence of a writer: the
    refusal itself logged nothing, so proving it took a trace of what was missing."""
    from structlog.testing import capture_logs

    with capture_logs() as logs:
        turn = await _turn(
            monkeypatch,
            _LiteLLM(),
            profile=_internal(),
            retrieval={"evidence_pack": {"items": [], "sources": []}, "confidence_band": "unknown"},
        )

    assert turn.text == STRICT_REFUSAL
    assert [log["reason"] for log in logs if log["event"] == "partner_chat_strict_refusal"] == ["no_evidence"]


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
    assert llm.of("answer") == [] and llm.of("grounding_check") == []
    selection = turn.records[0]["selection"]
    assert selection["verdict"] == "not_in_passages" and "evidence" not in selection and "question" not in selection, (
        "the internal record keeps the verdict and passage numbers, never text"
    )


@pytest.mark.asyncio
async def test_strict_knowledge_turn_whose_selection_chose_nothing_is_refused_before_the_writer(monkeypatch):
    """A selection that points at no sentence used to hand the writer every
    passage, with only the check after writing as a net. On a Strict
    knowledge question it is now the fixed refusal, and no writer runs."""
    llm = _LiteLLM(
        UNRELATED + " Parkeren is gratis op zondag.",
        grounding=_unsupported(UNRELATED, "Parkeren is gratis op zondag."),
        selection={"need": "verlof aanvragen", "parts": [], "must_ask_variant": False, "question": ""},
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
async def test_strict_turn_whose_selection_fails_twice_says_it_cannot_check_without_a_writer(monkeypatch):
    """No verdict means the knowledge base was never checked, so not "not found" either."""
    llm = _LiteLLM(selection="")

    turn = await _turn(
        monkeypatch,
        llm,
        profile=_internal(),
        retrieval={"evidence_pack": _pack(), "confidence_band": "high"},
    )

    assert turn.text == check_unavailable_message("nl", helpdesk=False)
    assert len(llm.of("passage_selection")) == 2
    assert llm.of("answer") == []


@pytest.mark.parametrize("selection", [NOT_IN_PASSAGES, ""], ids=["not_in_passages", "failed_twice"])
@pytest.mark.asyncio
async def test_open_turn_whose_selection_chose_nothing_keeps_the_passages(monkeypatch, selection):
    """Withholding the passages after the selection chose none lost a third of
    the knowledge-base answers on the internal meetlat, so the passages stay."""
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


@pytest.mark.asyncio
async def test_an_internal_record_names_the_retrieval_failure_behind_the_refusal(monkeypatch):
    """A retrieval-api restart turned into an ordinary-looking Strict refusal,
    so a measurement round scored the outage as a correct refusal."""
    turn = await _turn(
        monkeypatch,
        _LiteLLM(UNRELATED),
        profile=_internal(kb_mode="strict"),
        retrieval_response=httpx.Response(503, text="restarting"),
        stream=False,
    )

    assert "tijdelijk niet bereikbaar" in turn.text.lower()
    (record,) = turn.records
    assert record["retrieval_failure"] == "HTTP 503"


@pytest.mark.asyncio
async def test_an_internal_record_carries_the_selection_verdict_without_its_quotes(monkeypatch):
    """The internal meetlat tunes the selection on its verdict, which the
    record never carried: it was skipped whole because quotes are text."""
    llm = _LiteLLM()

    turn = await _turn(
        monkeypatch,
        llm,
        profile=_internal(kb_mode="open"),
        retrieval={"evidence_pack": _pack(), "confidence_band": "high"},
        stream=True,
    )

    (record,) = turn.records
    assert record["selection"] == {"verdict": "answers", "passages": [1]}
    assert "quote" not in str(record["selection"]) and "question" not in record["selection"]


@pytest.mark.asyncio
async def test_an_internal_turn_runs_the_selection_in_the_internal_words(monkeypatch):
    """The widget's selection rule ("how to do it") called 13 of 48 internal
    turns not in the passages while the owner expected an answer; the
    internal chat gets its own words for the same step."""
    from app.services.passage_selection import SELECTION_SYSTEM_PROMPT, SELECTION_SYSTEM_PROMPT_INTERNAL

    llm = _LiteLLM()

    await _turn(
        monkeypatch,
        llm,
        profile=_internal(kb_mode="open"),
        retrieval={"evidence_pack": _pack(), "confidence_band": "high"},
        stream=True,
    )

    (selection,) = llm.of("passage_selection")
    system = next(m["content"] for m in selection["messages"] if m["role"] == "system")
    assert system == SELECTION_SYSTEM_PROMPT_INTERNAL and system != SELECTION_SYSTEM_PROMPT
    assert "rewrite, review, translate or shorten" in system

    widget_llm = _LiteLLM()
    await _turn(
        monkeypatch,
        widget_llm,
        profile=ChatProfile(surface="partner", kb_mode="strict", kb_slugs=("handboek",)),
        retrieval={"evidence_pack": _pack(), "confidence_band": "high"},
        key_id="key-partner",
    )
    (widget_selection,) = widget_llm.of("passage_selection")
    assert next(m["content"] for m in widget_selection["messages"] if m["role"] == "system") == SELECTION_SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_an_internal_rewrite_request_keeps_the_passages(monkeypatch):
    """ "Answers with empty evidence" is the selection's word for a request
    that needs no passage (rewrite text already in the conversation); the
    writer still sees the passages, and no brief of chosen lines."""
    llm = _LiteLLM(
        selection={"need": "een mail van het vorige antwoord", "parts": [], "must_ask_variant": False, "question": ""}
    )

    await _turn(
        monkeypatch,
        llm,
        profile=_internal(kb_mode="open"),
        retrieval={"evidence_pack": _pack(), "confidence_band": "high"},
        stream=True,
    )

    prompt = _system_prompt(llm)
    assert ARTICLE in prompt and "[This turn]" not in prompt


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
    assert llm.of("grounding_check") == []


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
            "parts": [
                {
                    "ask": "hoe zet hij dat aan",
                    "evidence": [{"passage": 1, "quote": IPHONE_STEP}, {"passage": 2, "quote": ANDROID_STEP}],
                }
            ],
            "must_ask_variant": True,
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


# --- The case and its passages, kept across an internal conversation ------------

KEPT_ARTICLE = "Een verlofaanvraag die na twee werkdagen niet is goedgekeurd, gaat vanzelf door naar HR."
LEAVE_CASE = {
    "problem": "verlof aanvragen en niemand keurt het goed",
    "product": "HR-portaal",
    "variant": "",
    "tried": [],
    "wants_quote": "",
    "wants": "knowledge",
    "out_of_scope_quote": "",
    "out_of_scope": False,
    "sentiment": "neutral",
    "language": "nl",
    "queries": ["verlof aanvragen HR-portaal goedkeuring", "verlof goedkeuring leidinggevende reageert niet"],
}


def _one_article(text: str, chunk_id: str, title: str) -> dict:
    """A retrieve response with one article; the knowledge base comes from the raw chunks, as retrieval-api sends it."""
    item = {
        "chunk_id": chunk_id,
        "evidence_id": f"ev-{chunk_id}",
        "text": text,
        "title": title,
        "source_url": f"https://kb.example.com/{chunk_id}",
        "reranker_score": 0.82,
        "final_score": 0.82,
    }
    return {
        "evidence_pack": {"items": [item], "sources": [{"url": item["source_url"], "title": title}]},
        "chunks": [{"chunk_id": chunk_id, "kb_slug": "handboek"}],
        "confidence_band": "high",
    }


async def _two_internal_turns(
    monkeypatch,
    found_next: dict | None = None,
    *,
    profile: ChatProfile | None = None,
    searchable_next: tuple[str, ...] = ("handboek",),
    conversation_next: str | None = CONVERSATION,
    cases: dict | None = None,
) -> tuple[_Turn, _Turn, _LiteLLM]:
    """Turn 1 chooses KEPT_ARTICLE; turn 2's search finds only an unrelated article, or ``found_next``."""
    cases = {} if cases is None else cases
    profile = profile or _internal()
    first = await _turn(
        monkeypatch,
        _LiteLLM(case=LEAVE_CASE),
        profile=profile,
        retrieval=_one_article(KEPT_ARTICLE, "c-leave", "Verlof goedkeuren"),
        cases=cases,
    )
    llm = _LiteLLM(case=LEAVE_CASE)
    second = await _turn(
        monkeypatch,
        llm,
        profile=profile,
        retrieval=_one_article(UNRELATED, "c-parking", "Parkeren") if found_next is None else found_next,
        history=({"role": "user", "content": QUESTION}, {"role": "assistant", "content": first.text}),
        question="En als mijn leidinggevende niet reageert?",
        cases=cases,
        searchable=searchable_next,
        conversation_id=conversation_next,
    )
    return first, second, llm


@pytest.mark.asyncio
async def test_an_internal_follow_up_offers_the_passage_the_previous_turn_chose(monkeypatch):
    """The widget's case memory on the internal chat: the passage turn 1 chose
    is offered to turn 2's selection and writer, though turn 2's search lost it,
    and turn 2 reads its case on the case turn 1 stored."""
    _, _, llm = await _two_internal_turns(monkeypatch)

    (selection,) = llm.of("passage_selection")
    passages = selection["messages"][1]["content"].split("Passages:\n", 1)[1]
    assert passages.startswith(f"[1] Verlof goedkeuren\n{KEPT_ARTICLE}")
    assert KEPT_ARTICLE in _system_prompt(llm)
    (case_call,) = llm.of("case_update")
    assert LEAVE_CASE["problem"] in case_call["messages"][1]["content"]


@pytest.mark.asyncio
async def test_an_internal_record_says_whether_the_case_was_remembered_without_its_text(monkeypatch):
    first, second, _ = await _two_internal_turns(monkeypatch)

    assert first.records[0]["case_remembered"] is False
    (record,) = second.records
    assert record["case_remembered"] is True
    recorded = json.dumps(record, ensure_ascii=False)
    assert KEPT_ARTICLE not in recorded
    assert LEAVE_CASE["problem"] not in recorded


@pytest.mark.asyncio
async def test_a_strict_follow_up_whose_search_finds_nothing_is_written_from_the_kept_passage(monkeypatch):
    """Strict refused every turn whose search came back empty before the kept
    passages were looked at; the widget offers them in that case too."""
    _, second, llm = await _two_internal_turns(monkeypatch, {"evidence_pack": {"items": [], "sources": []}})

    assert second.text != STRICT_REFUSAL
    assert KEPT_ARTICLE in _system_prompt(llm)


@pytest.mark.asyncio
async def test_a_kept_passage_from_a_knowledge_base_the_employee_can_no_longer_search_is_not_offered(monkeypatch):
    """An employee searching every org knowledge base has no slug list, so the
    scope check on the kept passages let everything through: a passage from a
    knowledge base the employee lost access to came back on the next turn."""
    _, _, llm = await _two_internal_turns(monkeypatch, profile=_internal(kb_slugs=None), searchable_next=())

    (selection,) = llm.of("passage_selection")
    assert KEPT_ARTICLE not in selection["messages"][1]["content"]


@pytest.mark.parametrize("header", [None, "{{LIBRECHAT_BODY_CONVERSATIONID}}"])
@pytest.mark.asyncio
async def test_a_follow_up_without_a_conversation_id_reads_no_case_and_says_so(monkeypatch, header):
    """A tenant whose LibreChat config predates the header, or a placeholder
    LibreChat left unresolved, gets no memory: never a case guessed from the
    messages, never one shared by every conversation of the employee."""
    from structlog.testing import capture_logs

    with capture_logs() as logs:
        _, second, _ = await _two_internal_turns(monkeypatch, conversation_next=header)

    (record,) = second.records
    assert record["case_remembered"] is False
    assert [entry["event"] for entry in logs].count("internal_chat_conversation_id_missing") == 1


@pytest.mark.asyncio
async def test_two_conversations_of_one_employee_with_the_same_opener_keep_their_own_case(monkeypatch):
    other = "6b1d0c9e-2f4a-4e8b-a7c5-9d3e2f1a0b4c"
    cases: dict = {}

    _, second, _ = await _two_internal_turns(monkeypatch, conversation_next=other, cases=cases)

    (record,) = second.records
    assert record["case_remembered"] is False
    assert {key[2] for key in cases} == {CONVERSATION, other}


@pytest.mark.asyncio
async def test_a_regenerated_follow_up_reads_its_conversation_case_and_overwrites_it(monkeypatch):
    """LibreChat resends the same history with the same id: the turn reads the
    case its first answer stored and stores the new one in its place."""
    cases: dict = {}
    await _two_internal_turns(monkeypatch, cases=cases)
    regenerated_case = {**LEAVE_CASE, "problem": "verlof aanvragen, opnieuw gevraagd"}
    llm = _LiteLLM(case=regenerated_case)

    again = await _turn(
        monkeypatch,
        llm,
        profile=_internal(),
        retrieval=_one_article(UNRELATED, "c-parking", "Parkeren"),
        history=({"role": "user", "content": QUESTION}, {"role": "assistant", "content": "Eerder antwoord."}),
        question="En als mijn leidinggevende niet reageert?",
        cases=cases,
    )

    (record,) = again.records
    assert record["case_remembered"] is True
    (case_call,) = llm.of("case_update")
    assert LEAVE_CASE["problem"] in case_call["messages"][1]["content"]
    (stored,) = cases.values()
    assert stored.case["problem"] == regenerated_case["problem"]


@pytest.mark.asyncio
async def test_a_follow_up_after_a_turn_that_kept_nothing_reads_no_case(monkeypatch):
    """With the conversation id as the key, a turn that stored nothing left the
    case of the turn before it in place, so the next follow-up read a case two
    turns old; the widget never reuses an older case."""
    cases: dict = {}
    nothing_found = {"evidence_pack": {"items": [], "sources": []}}
    history: tuple[dict, ...] = ()
    for llm, question in (
        (_LiteLLM(case=LEAVE_CASE), QUESTION),
        # The case call fails and the search keeps no passage: nothing to keep.
        (_LiteLLM(), "En als mijn leidinggevende niet reageert?"),
    ):
        turn = await _turn(
            monkeypatch, llm, profile=_internal(), retrieval=nothing_found, question=question, history=history,
            cases=cases,
        )  # fmt: skip
        history = (*history, {"role": "user", "content": question}, {"role": "assistant", "content": turn.text})

    third = await _turn(
        monkeypatch,
        _LiteLLM(case=LEAVE_CASE),
        profile=_internal(),
        retrieval=nothing_found,
        question="Wie keurt het dan goed?",
        history=history,
        cases=cases,
    )

    (record,) = third.records
    assert record["case_remembered"] is False


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


@pytest.mark.asyncio
async def test_the_footer_warning_and_the_record_read_the_same_answer_check(monkeypatch):
    """The footer and the record used to be fed by separate checks; one AnswerCheck now feeds both."""
    from app.services import widget_write
    from app.services.answer_check import AnswerCheck, Statement

    check = AnswerCheck([Statement("201234567", "phone", None), Statement("EUR 10", "amount", ARTICLE)])
    calls = []
    monkeypatch.setattr(widget_write, "check_hard_facts", lambda *args: calls.append(args) or check)

    turn = await _turn(
        monkeypatch,
        _LiteLLM(ANSWER),
        profile=_internal(kb_mode="open"),
        retrieval={"evidence_pack": _pack(), "confidence_band": "high"},
        stream=True,
    )

    assert len(calls) == 1
    assert "- Let op: telefoonnummer in dit antwoord staat niet in de kennisbank." in turn.text
    (record,) = turn.records
    assert record["hard_facts_outside_kb"] == {"phone": 1}


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


class _UpstreamBody(_LiteLLM):
    """The answer call as LiteLLM really returns it: id, created, fingerprint, usage and fields of its own."""

    def __init__(self, extra: dict) -> None:
        super().__init__()
        self.extra = extra

    def __call__(self, request: httpx.Request) -> httpx.Response:
        response = super().__call__(request)
        if self._kind(json.loads(request.content)) != "answer":
            return response
        return httpx.Response(200, json={**self.extra, **response.json()})


_UPSTREAM = {
    "id": "chatcmpl-upstream",
    "object": "chat.completion",
    "created": 1761120000,
    "model": "mistral-small-2603",
    "system_fingerprint": "fp_example",
    "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
    "service_tier": "default",
}


@pytest.mark.parametrize("kb_mode", ["open", "general"])
@pytest.mark.parametrize("upstream", [_UPSTREAM, {}], ids=["full", "bare"])
@pytest.mark.asyncio
async def test_a_held_open_or_general_body_keeps_every_top_level_field_litellm_sent(monkeypatch, kb_mode, upstream):
    """The removed live writer returned LiteLLM's own response; the one writer must not drop `created` from it."""
    turn = await _turn(
        monkeypatch,
        _UpstreamBody(upstream),
        profile=_internal(kb_mode=kb_mode),
        retrieval={"evidence_pack": _pack(), "confidence_band": "high"},
    )

    top_level = {key: value for key, value in turn.sent.items() if key != "choices"}
    if upstream:
        assert top_level == _UPSTREAM
    else:
        assert top_level.keys() == {"id", "object", "model", "created"}
        assert isinstance(top_level["created"], int)


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
    assert kinds >= {"case_update", "passage_selection", "answer", "grounding_check"}
    assert all(body["metadata"]["_klai_delegated_org_id"] == "zorg-acme" for body in internal.calls)
    assert {"passage_selection", "answer", "grounding_check"} <= {_LiteLLM._kind(b) for b in widget.calls}
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
    "case_remembered": False,
    "band": "high",
    "multi_question": False,
    "selection": {"verdict": "answers", "passages": [1]},
    "judge_failed": ["case"],
    "grounding": "all_in_articles",
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
