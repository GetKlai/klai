"""One-chat-pipeline slice 5: one answer decision for every surface.

The internal chat now takes what the widget already had instead of the LiteLLM
hook's copies: ``decide_answer`` decides per mode, the answer plan asks the one
question, the weak-source rule replaces the band, the grounding check and its
repair live in one place, and every turn leaves one record of signals.

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
from klai_chat_prompts import CLARIFY_TURN_ADDENDUM, no_citable_sources_message

from app.api.partner_dependencies import PartnerAuthContext
from app.services.chat_profile import ChatProfile
from app.services.partner_chat import safety_refusal_message

LITELLM = "http://litellm.example.com"
RETRIEVAL = "http://retrieval.example.com"
INTERNAL_KEY = {"chat": True, "internal_chat": True}
STRICT_REFUSAL = no_citable_sources_message("nl", suggest_open_mode=True)

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
        plan: dict | None = None,
        judge: dict | None = None,
        grounding: dict | None = None,
        repaired: str = "",
    ) -> None:
        self.answer = answer
        self.plan = plan or {"route": "direct", "question": "", "options": []}
        self.judge = judge or {"grounding": "all_in_articles", "verdict": "answered"}
        self.grounding = grounding or _supported(answer)
        self.repaired = repaired
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
        return "answer" if "stream" in body else "rewrite"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls.append(body)
        kind = self._kind(body)
        replies: dict[str, Any] = {
            "answer_plan": self.plan,
            "answer_judge": self.judge,
            "grounding_check": self.grounding,
            "repair": self.repaired,
            "rewrite": "",
            "turn_judge": {
                "topic": "handled",
                "scope": "organisation",
                "wants_human": False,
                "sentiment": "neutral",
                "clarity": "clear",
                "missing": "",
            },
            "query_paraphrase": {"variants": []},
        }
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
    values.setdefault("stream_live", values["kb_mode"] in ("open", "general"))
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
    def __init__(self, text: str, frames: list[dict], message: dict, records: list[dict]) -> None:
        self.text = text
        self.frames = frames
        self.message = message
        self.records = records


async def _turn(
    monkeypatch,
    llm: _LiteLLM,
    *,
    profile: ChatProfile,
    retrieval: dict | None = None,
    question: str = QUESTION,
    stream: bool = False,
    key_id: str = "key-internal",
    support_mode: bool = False,
) -> _Turn:
    from app.api import partner
    from app.api.partner import ChatCompletionsRequest, chat_completions
    from app.services import partner_chat

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
    monkeypatch.setattr(partner, "_widget_system_prompt", AsyncMock(return_value=None))
    monkeypatch.setattr(partner, "_widget_page_context_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr(partner, "_widget_support_mode_enabled", AsyncMock(return_value=support_mode))
    monkeypatch.setattr(partner, "_widget_tone_register", AsyncMock(return_value="restrained"))
    monkeypatch.setattr(partner, "write_retrieval_log", AsyncMock())
    monkeypatch.setattr(partner_chat, "_schedule_gap_event", MagicMock())
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
        permissions=INTERNAL_KEY if profile.surface == "internal" else {"chat": True},
        kb_access={10: "read"},
        rate_limit_rpm=60,
    )
    request = ChatCompletionsRequest(messages=[{"role": "user", "content": question}], stream=stream)
    http_request = MagicMock(headers={}, client=MagicMock(host="127.0.0.1"))

    with respx.mock(assert_all_called=False) as router:
        router.post(f"{LITELLM}/v1/chat/completions").mock(side_effect=llm)
        router.post(f"{RETRIEVAL}/retrieve").mock(return_value=httpx.Response(200, json=retrieval or {}))
        router.get(url__startswith=f"{RETRIEVAL}/internal/v1/taxonomy").mock(return_value=httpx.Response(200, json={}))
        response = await chat_completions(request=request, http_request=http_request, auth=auth, db=db, profile=profile)
        frames: list[dict] = []
        message: dict = {}
        if stream:
            frames = _frames([chunk async for chunk in response.body_iterator])
            text = "".join(c["delta"].get("content") or "" for f in frames for c in f.get("choices") or [])
        else:
            message = response["choices"][0]["message"]
            text = message["content"]
        # The record and an Open turn's check are written after the reply.
        while pending := [task for task in (partner._pending | partner_chat._pending_turn_tasks) if not task.done()]:
            await asyncio.gather(*pending)
    return _Turn(text, frames, message, records)


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


# --- Clarify through the answer plan; weak sources by the gap, not the band --------


@pytest.mark.asyncio
async def test_low_band_ambiguous_internal_question_gets_the_plan_question_not_a_clarify_instruction(monkeypatch):
    plan = {
        "route": "choose",
        "question": "Gaat het om verlof aanvragen of om je leidinggevende?",
        "options": ["verlof aanvragen", "leidinggevende"],
    }
    llm = _LiteLLM(plan=plan)

    await _turn(
        monkeypatch,
        llm,
        profile=_internal(),
        retrieval={"evidence_pack": _pack(score=0.2), "confidence_band": "low"},
        question="verlof?",
    )

    prompt = _system_prompt(llm)
    assert plan["question"] in prompt
    assert CLARIFY_TURN_ADDENDUM["internal"].strip() not in prompt
    assert "clarifying question" not in prompt


@pytest.mark.parametrize(("band", "score", "told"), [("low", 0.9, False), ("high", 0.2, True)])
@pytest.mark.asyncio
async def test_internal_weak_source_rule_follows_the_article_scores_not_the_band(monkeypatch, band, score, told):
    llm = _LiteLLM()

    await _turn(
        monkeypatch, llm, profile=_internal(), retrieval={"evidence_pack": _pack(score=score), "confidence_band": band}
    )

    assert ("Retrieval found nothing that clearly matches" in _system_prompt(llm)) is told


# --- Grounding check and repair ---------------------------------------------------


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.asyncio
async def test_strict_answer_above_the_repair_threshold_is_repaired(monkeypatch, stream):
    extra = " Een aanvraag kost tien euro. Je krijgt altijd een extra vrije dag."
    repaired = "Je vraagt verlof aan in het HR-portaal onder Verlof."
    llm = _LiteLLM(
        ANSWER + extra,
        grounding=_unsupported("Een aanvraag kost tien euro.", "Je krijgt altijd een extra vrije dag."),
        repaired=repaired,
    )

    turn = await _turn(
        monkeypatch,
        llm,
        profile=_internal(),
        retrieval={"evidence_pack": _pack(), "confidence_band": "high"},
        stream=stream,
    )

    assert turn.text.startswith(repaired + "\n\n**Bronnen**")
    assert "tien euro" not in turn.text


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


# --- PII: the org travels with every internal call --------------------------------


@pytest.mark.asyncio
async def test_every_litellm_call_of_an_internal_and_a_widget_turn_carries_the_org(monkeypatch):
    extra = " Een aanvraag kost tien euro. Je krijgt altijd een extra vrije dag."
    plan = {"route": "choose", "question": "Gaat het om verlof aanvragen of om je leidinggevende?", "options": ["x"]}
    internal = _LiteLLM(
        ANSWER + extra,
        plan=plan,
        grounding=_unsupported("Een aanvraag kost tien euro.", "Je krijgt altijd een extra vrije dag."),
        repaired=ANSWER,
    )
    await _turn(
        monkeypatch, internal, profile=_internal(), retrieval={"evidence_pack": _pack(), "confidence_band": "high"}
    )
    widget = _LiteLLM(plan=plan)
    await _turn(
        monkeypatch,
        widget,
        profile=ChatProfile(surface="widget"),
        retrieval={"evidence_pack": _pack(), "confidence_band": "high"},
        key_id="wgt_acme",
        support_mode=True,
    )

    kinds = {_LiteLLM._kind(body) for body in internal.calls}
    assert kinds >= {"rewrite", "answer_plan", "answer", "answer_judge", "grounding_check", "repair"}
    assert all(body["metadata"]["_klai_delegated_org_id"] == "zorg-acme" for body in internal.calls)
    assert {"answer_plan", "answer", "answer_judge", "grounding_check"} <= {_LiteLLM._kind(b) for b in widget.calls}
    assert all(body["metadata"]["_klai_delegated_org_id"] == "zorg-acme" for body in widget.calls)


# --- review fixes on the integration branch ---------------------------------------


class _ToolCallOnly(_LiteLLM):
    """The answer call streams a tool call and no text, as an agent step does."""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if self._kind(body) == "answer" and body.get("stream"):
            self.calls.append(body)
            call = {"index": 0, "id": "call_1", "function": {"name": "lookup", "arguments": "{}"}}
            frame = json.dumps({"choices": [{"index": 0, "delta": {"tool_calls": [call]}}]})
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
    assert turn.text == ""


@pytest.mark.asyncio
async def test_strict_answer_about_pasted_mail_is_not_repaired_against_the_articles(monkeypatch):
    from tests.test_pasted_correspondence import _ENGLISH_HEADER_PASTE

    llm = _LiteLLM(grounding=_unsupported(ANSWER), repaired="")

    await _turn(
        monkeypatch,
        llm,
        profile=_internal(),
        retrieval={"evidence_pack": _pack(), "confidence_band": "high"},
        question=_ENGLISH_HEADER_PASTE,
    )

    assert llm.of("grounding_check") == [] and llm.of("repair") == []


@pytest.mark.asyncio
async def test_general_answer_keeps_its_links(monkeypatch):
    llm = _LiteLLM("Kijk op https://docs.example.com/install voor de stappen.")

    turn = await _turn(monkeypatch, llm, profile=_internal(kb_mode="general", stream_live=False))

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
