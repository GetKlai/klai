"""Step 6 of the widget's turn: the writer, citations, and the checks that decide what the visitor reads.

One composition for a streamed and a non-streamed request. The model call is
held until the reply is composed anyway (citations are chosen against the
final text), so there is nothing to stream from the model; the caller renders
the ``Reply`` as frames or as a body (widget_reply.py).
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Literal

import httpx
import structlog
from klai_chat_prompts import (
    appointment_offer_sentence,
    chat_contract_article,
    strip_appointment_offer_marker,
)
from klai_chat_prompts import no_citable_sources_message as _no_citable_sources_message

from app.core.config import Settings
from app.services.answer_epistemics import strip_answer_contract_markers
from app.services.answer_grounding import GroundingCheck, check_grounding, grounding_budget
from app.services.answer_judge import AnswerJudgement, decide_answer, is_clarifying_question, judge_answer
from app.services.partner_chat import (
    _CHOSEN_PASSAGE_KEY,
    _NO_CITABLE_SOURCES_DECISION_KEY,
    KnowledgeTurn,
    PageContext,
    _answer_without_retrieved_sources,
    _appointment_escalation,
    _appointment_escalation_signal,
    _augment_messages_with_system_prompt,
    _check_answer_later,
    _chunk_source_title,
    _compose_backend_managed_answer,
    _elapsed_ms,
    _emit_language_correctness_log,
    _fill_answer_signals,
    _grounding_label,
    _grounding_signals,
    _helpdesk_refusal_offers,
    _last_user_message,
    _llm_request_body,
    _log_citation_rescues,
    _log_turn_timing,
    _response_language,
    _text_offers_appointment,
    _visitor_question,
    output_safety_violation,
    safety_refusal_message,
    without_unsourced_contact_details,
)
from app.services.widget_reply import AnswerUpstreamError, Reply
from app.trace import get_trace_headers

logger = structlog.get_logger()

# A dash used as punctuation: an em dash anywhere between words, an en dash
# with a space on either side. "9\u201317" keeps its range dash.
_PUNCTUATION_DASH = re.compile(r"\s*\u2014\s*|\s+\u2013\s+")


def without_dashes(text: str) -> str:
    """The widget owner wants no dash as punctuation in a reply; the model copies them from the prompt."""
    return _PUNCTUATION_DASH.sub(", ", text)


async def write_reply(
    *,
    messages: list[dict],
    model: str,
    temperature: float,
    system_prompt: str,
    settings: Settings,
    org_id: int | str | None = None,
    page_context: PageContext | None = None,
    citation_chunks: list[dict] | None = None,
    trusted_sources: list[dict[str, Any]] | None = None,
    source_query: str | None = None,
    broad_mode: bool = False,
    conversational: bool = False,
    force_escalation: bool = False,
    clarity: Literal["clear", "ambiguous"] | None = None,
    sentiment: str | None = None,
    answer_signals: dict[str, Any] | None = None,
    signal_chunks: list[dict] | None = None,
    turn_timing: dict[str, float] | None = None,
    delegated_org_id: str | None = None,
    knowledge_turn: KnowledgeTurn | None = None,
    pasted_correspondence: bool = False,
) -> Reply:
    """Write the answer, compose its citations, check it, and return what the visitor is to read.

    ``broad_mode`` is the turn's decision (see ``_broad_mode_active``) that this is
    a consented general-knowledge answer; the composer labels it and nothing is
    checked, because every fact in it would count as unsupported.
    ``answer_signals`` is the turn's audit sink: filled here, never part of what
    the visitor receives. ``signal_chunks`` is the retrieval result to score the
    answer on; it differs from ``citation_chunks`` on a broad turn, where the
    caller empties the citation list on purpose.
    """
    turn = knowledge_turn if knowledge_turn is not None else KnowledgeTurn()
    response_language = _response_language(messages, pasted_correspondence=pasted_correspondence)
    augmented = _augment_messages_with_system_prompt(
        messages, system_prompt, page_context, response_language=response_language
    )
    # The page-context message is prepended, so the last user turn here is the
    # human's own words, never the rewritten retrieval query. The refusal
    # follows the conversation decision; this stays the input for the
    # output-safety refusal, which has no conversation decision to follow.
    visitor_query = _last_user_message(augmented) or ""
    # Kept for citation composition only: it is the KB-rewritten search query
    # and must never reach the refusal language.
    user_query = source_query or _last_user_message(messages) or ""

    generation_started = time.perf_counter()
    completion, draft = await _generate(
        augmented,
        model=model,
        temperature=temperature,
        settings=settings,
        org_id=org_id,
        delegated_org_id=delegated_org_id,
        language=response_language,
    )
    generation_ms = _elapsed_ms(generation_started)
    if pasted_correspondence:
        draft = strip_answer_contract_markers(draft)
    content, sources, decision = _compose_backend_managed_answer(
        draft,
        trusted_sources,
        citation_chunks,
        user_query,
        helpdesk=True,
        broad=broad_mode,
        force_escalation=force_escalation,
        response_language=response_language,
    )
    judge_started = time.perf_counter()
    if safety_reason := output_safety_violation(draft):
        logger.warning(
            "partner_chat_output_blocked", org_id=org_id, stage="stream_composed_output", reason=safety_reason
        )
        content = safety_refusal_message(visitor_query)
        sources = []
        decision = {"reason": safety_reason, "output_blocked": True}
    else:
        content, sources, decision = await _judge(
            content,
            sources,
            decision,
            draft=draft,
            messages=messages,
            citation_chunks=citation_chunks,
            settings=settings,
            org_id=org_id,
            answer_signals=answer_signals,
            response_language=response_language,
            force_escalation=force_escalation,
            conversational=conversational,
            clarity=clarity,
            knowledge_turn=turn,
            delegated_org_id=delegated_org_id,
        )
        content = without_dashes(content)
        if sentiment:
            decision["sentiment"] = sentiment
    _log_turn_timing(
        turn_timing,
        org_id=org_id,
        generation_ms=generation_ms,
        answer_judge_ms=_elapsed_ms(judge_started),
        answer_signals=answer_signals,
    )
    # Consumed before the decision is logged so that event keeps its exact
    # payload: the marker only travels to the audit sink.
    refused = bool(decision.pop(_NO_CITABLE_SOURCES_DECISION_KEY, False))
    logger.info(
        "partner_chat_citation_selection_decision",
        org_id=org_id,
        selected_count=len(sources),
        decision=decision,
    )
    _log_citation_rescues(decision, org_id=org_id)
    _fill_answer_signals(
        answer_signals,
        decision=decision,
        refused=refused,
        chunks=signal_chunks if signal_chunks is not None else citation_chunks,
        sources=sources,
        model=model,
        query_text=visitor_query,
        reply=content,
    )
    _emit_language_correctness_log(
        org_id=org_id, query=user_query, response_text=content, chunks_injected=len(citation_chunks or [])
    )
    # A safety refusal replaced the decision wholesale, so a blocked answer
    # neither labels itself general knowledge, invites the visitor to broaden,
    # nor carries a booking button.
    return Reply(
        text=content,
        sources=sources,
        decision={**decision, "route": "written"},
        signals=answer_signals if answer_signals is not None else {},
        language=response_language,
        turn=turn,
        appointment=_appointment_escalation_signal(decision) is not None,
        broad_mode=decision.get("broad_mode"),
        passages=len(citation_chunks or []),
        completion=completion,
    )


async def _generate(
    messages: list[dict],
    *,
    model: str,
    temperature: float,
    settings: Settings,
    org_id: int | str | None,
    delegated_org_id: str | None,
    language: str | None,
) -> tuple[dict[str, Any], str]:
    """The answer model's text, and the id, model and usage it came with."""
    chat_url = f"{settings.litellm_base_url}/v1/chat/completions"
    request_json = _llm_request_body(
        model,
        messages,
        temperature,
        stream=False,
        tools=None,
        tool_choice=None,
        delegated_org_id=delegated_org_id,
    )
    try:
        # 120 s: the longer of the two budgets the streamed and the plain call had.
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                chat_url,
                json=request_json,
                headers={"Authorization": f"Bearer {settings.litellm_master_key}", **get_trace_headers()},
            )
            resp.raise_for_status()
            body = resp.json()
    except httpx.TransportError as exc:
        logger.warning("partner_chat_upstream_unreachable", org_id=org_id, target=chat_url, exc_info=True)
        raise AnswerUpstreamError("Chat service unavailable", language) from exc
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "partner_chat_upstream_error", org_id=org_id, status_code=exc.response.status_code, exc_info=True
        )
        raise AnswerUpstreamError("Chat service error", language) from exc
    choices = body.get("choices") or []
    text = ((choices[0].get("message") or {}).get("content") if choices else None) or ""
    return {key: body[key] for key in ("id", "model", "usage") if key in body}, text if isinstance(text, str) else ""


async def _judge(
    content: str,
    sources: list[dict],
    decision: dict[str, Any],
    *,
    draft: str,
    messages: list[dict],
    citation_chunks: list[dict] | None,
    settings: Settings,
    org_id: int | str | None,
    answer_signals: dict[str, Any] | None,
    response_language: str | None,
    force_escalation: bool,
    conversational: bool,
    clarity: Literal["clear", "ambiguous"] | None,
    knowledge_turn: KnowledgeTurn,
    delegated_org_id: str | None,
) -> tuple[str, list[dict], dict[str, Any]]:
    """SPEC-RAG-ANSWER-JUDGES-001 REQ-2/REQ-3: the answer judge, then the one decision.

    The judge sees the draft after the marker and link stripper, exactly the
    text the visitor would get. An empty result (the model wrote nothing) has
    nothing to judge and keeps the composer's refusal; a consented broad-mode
    answer is not judged either.

    A clarifying question carries no buttons: a "broaden the search" or "book
    an appointment" button under a question reads as a refusal. An uncited
    answer keeps only an appointment offer that the backend forced or the
    model's own words make; a partial answer always gets one.
    """
    if decision.get("broad_mode") == "answer":
        return content, sources, decision
    draft_text, model_offered_appointment = strip_appointment_offer_marker(draft)
    # The judges may hand this text to the visitor instead of the composed
    # one; it passes the same provenance gate.
    safe_text = without_unsourced_contact_details(
        _answer_without_retrieved_sources(draft_text, citation_chunks), citation_chunks
    )
    if not safe_text:
        return content, sources, decision
    articles = [(_chunk_source_title(chunk), str(chunk.get("text") or "")) for chunk in citation_chunks or []]
    # The checker only sees the question, the articles and the reply, so it cannot
    # know a booking button is really attached and flagged "Klik op de knop hieronder
    # om een afspraak in te plannen" as an unsupported claim about the company. Handing
    # it the guarantee as one more article keeps it strict about what that appointment
    # will DO, which a skip-list in the prompt could not separate.
    if (contract := chat_contract_article(appointment_offered=True)) is not None:
        articles = [*articles, contract]
    chosen = bool(citation_chunks) and all(chunk.get(_CHOSEN_PASSAGE_KEY) for chunk in citation_chunks or [])
    deferred = chosen and bool(sources) and answer_signals is not None
    checks_started = time.perf_counter()
    light_judge = judge_answer(
        messages=messages, draft=safe_text, articles=articles, settings=settings, delegated_org_id=delegated_org_id
    )
    if deferred and answer_signals is not None:
        # The writer read only passages the selection step chose and cited
        # one, and after writing nothing changes such a reply (below), so the
        # statement check is measurement and runs after the reply went out. On
        # the critical path it cost 1.6 s median of a 6.2 s written turn in the
        # first week of real traffic; the light judge, which decides the
        # appointment button, costs 0.4 s. An uncited reply keeps the check on
        # the path: without a source its statements decide between showing and
        # refusing (decide_answer).
        judgement, grounding = await light_judge, None
        knowledge_turn.grounding_check = _check_answer_later(
            question=_visitor_question(messages),
            draft=safe_text,
            articles=articles,
            settings=settings,
            org_id=org_id,
            delegated_org_id=delegated_org_id,
            answer_signals=answer_signals,
            keep_statements=True,
        )
    else:
        # Both checks read the same draft and run together, so the heavier one
        # costs its own 2.1 s median once and nothing on top of the light judge.
        judgement, grounding = await asyncio.gather(
            light_judge,
            check_grounding(
                question=_visitor_question(messages),
                draft=safe_text,
                articles=articles,
                settings=settings,
                org_id=org_id,
                timeout_seconds=grounding_budget("widget"),
                delegated_org_id=delegated_org_id,
            ),
        )
    checks_ms = _elapsed_ms(checks_started)
    if judgement is not None and grounding is not None:
        # The statement-level check decides grounding: measured against 54
        # hand-checked answers it catches 96% where the light judge caught 22%.
        judgement = judgement.model_copy(update={"grounding": _grounding_label(grounding)})
    outcome = decide_answer(
        kb_mode="strict",
        has_sources=bool(sources),
        escalation=force_escalation,
        conversational=conversational,
        clarity=clarity,
        draft_is_question=is_clarifying_question(safe_text),
        judgement=judgement,
    )
    verdicts = judgement.model_dump() if judgement is not None else {}
    logger.info(
        "partner_chat_answer_judge",
        org_id=org_id,
        decision=outcome,
        judge_failed=judgement is None,
        grounding_checked=grounding is not None,
        checks_ms=checks_ms,
        unsupported=len(grounding.unsupported) if grounding is not None else None,
        **verdicts,
    )
    if answer_signals is not None:
        answer_signals.update(verdicts, decision=outcome)
        if not deferred:
            _grounding_signals(answer_signals, grounding, keep_statements=True)
        if judgement is None:
            answer_signals.setdefault("judge_failed", []).append("answer")
    return _decided(
        outcome,
        content,
        sources,
        decision,
        safe_text=safe_text,
        judgement=judgement,
        grounding=grounding,
        citation_chunks=citation_chunks,
        response_language=response_language,
        force_escalation=force_escalation,
        conversational=conversational,
        offered_by_model=model_offered_appointment,
    )


def _decided(
    outcome: str,
    content: str,
    sources: list[dict],
    decision: dict[str, Any],
    *,
    safe_text: str,
    judgement: AnswerJudgement | None,
    grounding: GroundingCheck | None,
    citation_chunks: list[dict] | None,
    response_language: str | None,
    force_escalation: bool,
    conversational: bool,
    offered_by_model: bool,
) -> tuple[str, list[dict], dict[str, Any]]:
    """What the visitor gets for the judges' outcome: the composed reply, a refusal, or the model's own words."""
    refused = bool(decision.get(_NO_CITABLE_SOURCES_DECISION_KEY))
    if outcome == "refusal":
        if refused:
            return content, sources, decision
        refusal: dict[str, Any] = {
            "reason": "answer_judge_refusal",
            _NO_CITABLE_SOURCES_DECISION_KEY: True,
            **_helpdesk_refusal_offers(citation_chunks),
        }
        return _no_citable_sources_message(response_language, helpdesk=True), [], refusal
    if outcome == "clarifying_question":
        return safe_text, [], {"reason": "clarifying_question"}
    if refused:
        content, sources = safe_text, []
        decision = {
            key: value
            for key, value in decision.items()
            if key not in (_NO_CITABLE_SOURCES_DECISION_KEY, "broad_mode", "escalation")
        }
        decision["reason"] = "uncited_no_claims"
        # A reply with no source that does not answer the question is a dead end
        # for the visitor, so it carries the same button the backend's own
        # refusal carries. Without this the model's own "dat staat niet in onze
        # helpartikelen" arrived bare, and a visitor in a simulated conversation
        # on 2026-09-18 needed two more turns to find out a person was reachable
        # at all: they repeated their question, got the backend refusal with the
        # button, and then had to ask how to book.
        # Not on a conversational turn: decide_answer deliberately treats one as an
        # answer even when the light judge calls it unanswered, and a button under
        # "graag gedaan" offers help with nothing.
        dead_end = not conversational and judgement is not None and judgement.verdict != "answered"
        if force_escalation or dead_end or (offered_by_model and _text_offers_appointment(safe_text)):
            decision["escalation"] = _appointment_escalation()
    if outcome == "partial_answer":
        decision["escalation"] = _appointment_escalation()
    # Only a reply the visitor actually reads: a refusal and a clarifying
    # question state nothing about the organisation. Nothing changes a reply
    # after it is written: what the writer may read is decided before
    # (passage_selection.py), and the check's verdict is kept for measuring.
    # Editing a flagged reply was the largest cause of a bad answer in the
    # owner's review, and turning it into a refusal on one "contradicted"
    # threw away a correct procedure whose warning the check misread.
    chosen = bool(citation_chunks) and all(chunk.get(_CHOSEN_PASSAGE_KEY) for chunk in citation_chunks or [])
    if (
        outcome in ("answer", "partial_answer")
        and grounding is not None
        and len(grounding.statements) >= 2
        and len(grounding.unsupported) == len(grounding.statements)
        and not chosen
    ):
        # The selection step did not choose this turn's passages (it failed,
        # or was skipped), so the writer read everything retrieval found. A
        # reply in which the check then supports nothing is not shown.
        refusal = {
            "reason": "grounding_nothing_left",
            _NO_CITABLE_SOURCES_DECISION_KEY: True,
            **_helpdesk_refusal_offers(citation_chunks),
        }
        return _no_citable_sources_message(response_language, helpdesk=True), [], refusal
    # A reply without a source that the judge does not call an answer is the
    # visitor's dead end, and the button under it is the way out. The profile
    # and the weak-source rule ask the model to say so; measured on the owner's
    # review of real answers, 5 of 15 such replies did not. The sentence is
    # ours, so it is there whenever the button is. Not behind a question: the
    # visitor is asked something, not sent away. Not under a reply that kept
    # its source: a judge may add the button to such a reply, never change it.
    if (
        not sources
        and decision.get("escalation")
        and judgement is not None
        and judgement.verdict != "answered"
        and not _text_offers_appointment(content)
        and not is_clarifying_question(content)
    ):
        content = f"{content.rstrip()} {appointment_offer_sentence(response_language)}"
    return content, sources, decision
