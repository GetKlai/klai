"""The help widget's support-mode turn, step by step in the order of the flow.

The flow is chat-quality-history-and-plan.md §5: read the case, route in code
what needs no articles, search, select, route in code what the selection
decided, write. ``app.api.partner.chat_completions`` resolves the request, the
widget's settings and the previous case before the turn, and records and sends
the ``Reply`` after it; a reply decided in code and a written one both come
back as a ``Reply``. The internal chat runs the same flow (internal_turn.py)
and shares the steps that are the same there: the question, the fixed reply
and the retrieval log.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, NamedTuple, Protocol

import structlog
from klai_chat_prompts import contact_reply, no_citable_sources_message
from klai_chat_prompts.language import resolve_conversation_language

from app.core.config import Settings
from app.services import case_file, turn_judge
from app.services import escalation_intent as escalation_service
from app.services.chat_profile import ChatProfile
from app.services.clarify_gate import already_asked
from app.services.gap_classification import classify_gap
from app.services.off_topic_referral import off_topic_referral
from app.services.partner_chat import (
    KnowledgeTurn,
    PageContext,
    _last_user_message,
    output_safety_violation,
    retrieval_upstream_errors,
    retrieve_context,
    timed,
)
from app.services.passage_selection import plain_question
from app.services.pasted_correspondence import latest_user_turn_has_correspondence
from app.services.retrieval_log import write_retrieval_log
from app.services.widget_reply import Reply
from app.services.widget_write import write_reply

if TYPE_CHECKING:
    from app.api.partner import ChatCompletionsRequest
    from app.api.partner_dependencies import PartnerAuthContext

logger = structlog.get_logger()

# Hold references to fire-and-forget tasks to prevent GC (same pattern as partner_dependencies)
_pending: set[asyncio.Task] = set()  # type: ignore[type-arg]


class StepInputs(Protocol):
    """What the steps shared with the internal chat read from a turn's inputs."""

    request: ChatCompletionsRequest
    auth: PartnerAuthContext
    answer_signals: dict[str, Any]


@dataclass
class TurnInputs:
    """What chat_completions resolved before the turn."""

    request: ChatCompletionsRequest
    auth: PartnerAuthContext
    settings: Settings
    profile: ChatProfile
    kb_slugs: list[str]
    retrieval_enabled: bool
    partner_user_id: str | None
    widget_system_prompt: str | None
    page_context: PageContext | None
    tone_register: str
    off_topic_subjects: str
    off_topic_reply: str
    previous_case: dict | None
    audit_widget_id: str | None
    audit_session_key: str | None
    user_turn_write: asyncio.Task[int | None] | None
    audit_ready: bool
    pasted_correspondence: bool
    # The turn's audit sink (SPEC-KNOWLEDGE-ACTIVITY-001 §4.1): every step
    # writes into it, the record stores it, the visitor never sees it.
    answer_signals: dict[str, Any]
    turn_started: float


class Found(NamedTuple):
    chunks: list[dict]
    system_prompt: str
    trusted_sources: list[dict[str, Any]]
    # retrieve_context's per-turn decision: support mode + visitor consent + a
    # real retrieval attempt that produced a gap. The writer then neither cites
    # the weak chunks nor shows a misleading "passages gevonden" activity; the
    # retrieval log still records them, retrieval genuinely ran.
    broad: bool
    gap: str | None


class Writing(NamedTuple):
    escalation: str | None
    conversational: bool


async def widget_turn(inputs: TurnInputs) -> Reply:
    turn = KnowledgeTurn()
    messages = inputs.request.messages
    with retrieval_upstream_errors(inputs.auth.org_id):
        if case_file.is_follow_up(messages):
            # 1. Read. The case's queries go into a follow-up's search, so the
            # case comes first.
            case, case_ms = await timed(_read_case(inputs))
            _keep_case(inputs, case)
            # 2. Route in code. A person, contact or an appointment needs no
            # articles, so that route is decided before the search (plan §5,
            # step 2); a first turn takes it after the search, in step 5.
            if case is not None and case.wants in case_file.CONTACT_WANTS:
                return _contact_request_reply(inputs, turn, case)
            # 3-4. Search, and select inside retrieve_context (passage_selection.py).
            variants = case.search_variants(_last_user_message(messages) or "") if case else []
            found, retrieval_ms = await timed(_search(inputs, turn, variants))
        else:
            # A first question keeps its two paraphrases and runs the case
            # beside the search: the case's queries narrow (product, problem,
            # variant) where the paraphrases widen, and on the meetlat that
            # widening brought both variants' sections in, so the selection
            # step could ask which one applies; with the case's queries alone
            # two of the six expected clarifying questions were answered
            # instead (plan §8, step M).
            (found, retrieval_ms), (case, case_ms) = await asyncio.gather(
                timed(_search(inputs, turn, [])), timed(_read_case(inputs))
            )
            _keep_case(inputs, case)
    # 5. Route in code what the case and the selection decided.
    routed = await _route(inputs, turn, found, case)
    if isinstance(routed, Reply):
        return routed
    # 6. Write.
    timing = {"started_at": inputs.turn_started, "retrieval_ms": retrieval_ms, "case_ms": case_ms}
    return await _write(inputs, turn, found, routed, sentiment=case.sentiment if case else None, timing=timing)


def _read_case(inputs: TurnInputs) -> Awaitable[case_file.Case | None]:
    # The case is the one reading of the turn (case_file.py): what the
    # visitor wants, their tone, whether the subject is one this widget
    # answers, and the queries for a follow-up's search. It replaced the
    # question judge that used to run beside it (plan §8, step M).
    return case_file.update_case(
        inputs.request.messages,
        inputs.previous_case,
        inputs.settings,
        off_topic_subjects=inputs.off_topic_subjects,
        delegated_org_id=inputs.auth.zitadel_org_id,
    )


def _keep_case(inputs: TurnInputs, case: case_file.Case | None) -> None:
    if case is not None:
        inputs.answer_signals["case"] = case.model_dump()


async def _search(inputs: TurnInputs, turn: KnowledgeTurn, follow_up_variants: list[str]) -> Found:
    request, auth = inputs.request, inputs.auth
    knowledge = request.knowledge
    chunks, system_prompt, trusted_sources, broad = await retrieve_context(
        follow_up_variants=follow_up_variants,
        answer_signals=inputs.answer_signals,
        org_id=auth.org_id,
        zitadel_org_id=auth.zitadel_org_id,
        kb_slugs=inputs.kb_slugs,
        messages=request.messages,
        settings=inputs.settings,
        partner_user_id=inputs.partner_user_id,
        widget_system_prompt=inputs.widget_system_prompt,
        page_context=inputs.page_context,
        backend_managed_citations=True,
        support_mode=True,
        broad_mode=bool(request.broad_mode),
        tone_register=inputs.tone_register,
        pasted_correspondence=inputs.pasted_correspondence,
        is_preview=getattr(auth, "is_preview", False),
        audit_widget_id=inputs.audit_widget_id,
        audit_session_key=inputs.audit_session_key,
        audit_write=inputs.user_turn_write,
        retrieval_query=knowledge.query if knowledge is not None else None,
        top_k=knowledge.top_k if knowledge is not None and knowledge.top_k is not None else 8,
        retrieval_enabled=inputs.retrieval_enabled,
        profile=inputs.profile,
        templates_block="",
        telemetry_level="shadow",
        # A widget key may not send tools (chat_completions rejects them).
        web_search_available=False,
        knowledge_turn=turn,
    )
    return Found(chunks, system_prompt, trusted_sources, broad, classify_gap(chunks))


async def _route(inputs: TurnInputs, turn: KnowledgeTurn, found: Found, case: case_file.Case | None) -> Reply | Writing:
    escalation, wants_contact = _escalation(inputs, case)
    scope = case.scope if case else None
    # SPEC-RAG-ANSWER-TIERS-001 REQ-1. A conversational turn leaves the
    # knowledge pipeline only on a retrieval gap: with usable chunks the
    # composer answers from them either way, and the answer judge still checks
    # what an uncited reply claims. The class also reaches the generation, or
    # the profile would tell the model to refuse "do you speak English".
    conversational = found.gap is not None and turn_judge.is_conversational(scope)
    # Every turn logs its reading, a failure as its own word: a share you
    # cannot see is a boundary that drifts unnoticed.
    if case is None:
        inputs.answer_signals["judge_failed"] = ["case"]
    logger.info(
        "partner_chat_turn_judge",
        org_id=inputs.auth.org_id,
        wgt_id=inputs.auth.key_id,
        turn_scope=turn_judge.scope_label(scope),
        wants_human=wants_contact,
        sentiment=case.sentiment if case else None,
        topic=("not_handled" if case.out_of_scope else "handled") if case else None,
        retrieval_gap=found.gap,
    )
    if referral := await _off_topic_reply(inputs, turn, case, wants_contact):
        return referral
    if wants_contact and case is not None:
        return _contact_request_reply(inputs, turn, case)
    if not_found := _not_in_passages_reply(inputs, turn, found, escalation):
        return not_found
    if question := question_reply(inputs, turn, found, escalation, scope):
        return question
    return Writing(escalation, conversational)


def _escalation(inputs: TurnInputs, case: case_file.Case | None) -> tuple[str | None, bool]:
    """The escalation this turn carries, and whether the visitor asked for a person, contact or an appointment."""
    # Escalation is the backend's call, not the model's: when the visitor asks
    # for a person or is frustrated, the button goes under this answer whatever
    # retrieval found, and the model is told so for this one turn. See
    # escalation_intent.py for why this layer exists.
    escalation = escalation_service.escalation_intent(_last_user_message(inputs.request.messages))
    # The case reads the whole conversation and names what the visitor wants
    # now; "ik wil jullie mailen" and "graag contact met een medewerker" got
    # the not-found text in the first week of real traffic because neither
    # the phrase detector nor the turn judge called them a request for a
    # person. Such a turn is answered in code: handed to the writer with the
    # human-request instruction, it still answered "can I create an e-mail
    # ticket" with another product's ticket button.
    if case is not None and case.wants in case_file.CONTACT_WANTS:
        return escalation_service.HUMAN_REQUEST, True
    if escalation is None and case is not None and case.sentiment == "negative":
        return escalation_service.FRUSTRATION, False
    return escalation, False


async def _off_topic_reply(
    inputs: TurnInputs, turn: KnowledgeTurn, case: case_file.Case | None, wants_contact: bool
) -> Reply | None:
    # A subject this widget does not answer (prices, quotes, payment terms):
    # the visitor gets a referral with the appointment button and no answer
    # model sees the articles, so a price cannot slip in from one. The referral
    # names the visitor's subject in a sentence of ours, or falls back to the
    # tenant's own (off_topic_referral.py); that call waits up to 2.5 s, on these
    # turns only. A request for a person wins from it: "kan ik jullie ook
    # gewoon bellen" read as such a subject on the meetlat, and the visitor
    # asked how to reach a person, not about it.
    if not (
        inputs.off_topic_subjects
        and inputs.off_topic_reply
        and case is not None
        and case.out_of_scope
        and not wants_contact
    ):
        # A failed case falls through to the normal answer on purpose: that is
        # what the visitor got before this setting existed, and refusing every
        # turn because one call timed out would be worse than answering one
        # price question. Same fail direction as every other check here.
        return None
    messages = inputs.request.messages
    language = resolve_conversation_language(messages).language
    referral = await off_topic_referral(
        _last_user_message(messages) or "", language, inputs.settings, delegated_org_id=inputs.auth.zitadel_org_id
    )
    logger.info(
        "partner_chat_off_topic",
        org_id=inputs.auth.org_id,
        wgt_id=inputs.auth.key_id,
        referral=referral is not None,
    )
    return fixed_reply(inputs, turn, referral or inputs.off_topic_reply, language, decision="off_topic", refused=False)


def _contact_request_reply(inputs: TurnInputs, turn: KnowledgeTurn, case: case_file.Case) -> Reply:
    language = resolve_conversation_language(inputs.request.messages).language
    logger.info(
        "partner_chat_contact_request",
        org_id=inputs.auth.org_id,
        wgt_id=inputs.auth.key_id,
        wants=case.wants,
    )
    return fixed_reply(inputs, turn, contact_reply(language), language, decision="human_request", refused=False)


def _not_in_passages_reply(
    inputs: TurnInputs, turn: KnowledgeTurn, found: Found, escalation: str | None
) -> Reply | None:
    # Retrieval found passages, and none of them answers this question
    # (passage_selection.py): the visitor gets the honest "not found" with the
    # appointment button and no answer model writes, because a model that is
    # handed passages on the same subject writes a procedure out of them. A
    # frustrated visitor gets this reply too: it carries the appointment, and
    # sending that turn to the writer with every passage produced an invented
    # cause. Not when the visitor asked for a person: that turn is about the
    # appointment itself. The case's "chat" does not exempt a turn: the
    # selection step read the same conversation and calls a thank-you or a
    # remark an answer that needs no passage, and the smaller judge labelled
    # "how do I put this on my laptop" conversational, which sent it to the
    # writer with passages that did not answer it.
    if not turn.not_in_passages or escalation == escalation_service.HUMAN_REQUEST:
        return None
    language = resolve_conversation_language(inputs.request.messages).language
    logger.info(
        "partner_chat_not_in_passages",
        org_id=inputs.auth.org_id,
        wgt_id=inputs.auth.key_id,
        passages=len(found.chunks),
    )
    return fixed_reply(
        inputs, turn, no_citable_sources_message(language, helpdesk=True), language, decision="refusal", refused=True
    )


def question_reply(
    inputs: StepInputs, turn: KnowledgeTurn, found: Found, escalation: str | None, scope: str | None
) -> Reply | None:
    # The one question this turn should ask. Not on a broad turn (no articles
    # to reason over), not when the visitor asked for a person or is
    # frustrated: there the reply is the appointment, not on a conversational
    # turn, and not when the latest turn is pasted correspondence, whose
    # question is the distilled email itself.
    messages = inputs.request.messages
    if (
        found.broad
        or escalation is not None
        or turn_judge.is_conversational(scope)
        or latest_user_turn_has_correspondence(messages)
    ):
        return None
    # The selection step read the passages themselves, so it decides whether
    # the steps differ per variant, and it wrote the one question. That
    # question is the whole reply, sent as it is: asked to put it into words,
    # the answer model wrote an opening line and stopped in two of four
    # replayed turns. Once per conversation. The question is model-written and
    # goes out as it is, so it passes the checks a written reply passes: one
    # plain line ending on a question mark (which is also how "already asked"
    # recognises it next turn), and the output safety check. It needs passages
    # to have been chosen: a question about variants nobody found is a guess.
    selection = turn.selection
    asks = (
        selection is not None
        and selection.verdict == "depends"
        and turn.passages_chosen
        and plain_question(selection.question)
        and output_safety_violation(selection.question) is None
        and not already_asked(messages)
    )
    logger.info(
        "clarify_decision",
        org_id=inputs.auth.org_id,
        wgt_id=inputs.auth.key_id,
        fired=asks,
        reason=selection.verdict if selection else "no_selection",
    )
    if not asks or selection is None:
        return None
    inputs.answer_signals.update(planned_question=True, question_asked=True, clarity="ambiguous")
    language = resolve_conversation_language(messages).language
    question = " ".join(selection.question.split())
    return fixed_reply(
        inputs, turn, question, language, decision="clarifying_question", refused=False, appointment=False
    )


def fixed_reply(
    inputs: StepInputs,
    turn: KnowledgeTurn,
    text: str,
    language: str | None,
    *,
    decision: str,
    refused: bool,
    appointment: bool = True,
) -> Reply:
    """A reply decided in code: recorded like any other, with the appointment button unless told otherwise."""
    inputs.answer_signals.update(
        decision=decision,
        sources_count=0,
        refused=refused,
        broad_mode=False,
        model=inputs.request.model,
        appointment=appointment,
    )
    if language is not None:
        inputs.answer_signals["language"] = language
    return Reply(
        text=text,
        sources=[],
        decision={"route": decision},
        signals=inputs.answer_signals,
        language=language,
        turn=turn,
        appointment=appointment,
    )


async def _write(
    inputs: TurnInputs,
    turn: KnowledgeTurn,
    found: Found,
    writing: Writing,
    *,
    sentiment: str | None,
    timing: dict[str, float],
) -> Reply:
    request, auth = inputs.request, inputs.auth
    if request.web_search:
        # Live web search never runs for a public widget key.
        logger.debug("partner_web_search_ignored_widget", org_id=auth.org_id)
    log_retrieval(auth.org_id, f"partner:{auth.key_id}", found.chunks)
    knowledge = request.knowledge
    system_prompt = found.system_prompt
    if writing.escalation:
        system_prompt += escalation_service.ESCALATION_TURN_ADDENDUM[writing.escalation]
    if writing.conversational:
        system_prompt += turn_judge.CONVERSATIONAL_TURN_ADDENDUM
    return await write_reply(
        messages=request.messages,
        model=request.model,
        temperature=request.temperature,
        system_prompt=system_prompt,
        settings=inputs.settings,
        org_id=auth.org_id,
        citation_chunks=[] if found.broad else found.chunks,
        trusted_sources=found.trusted_sources,
        source_query=knowledge.query if knowledge is not None else None,
        page_context=inputs.page_context,
        broad_mode=found.broad,
        force_escalation=writing.escalation is not None,
        conversational=writing.conversational,
        sentiment=sentiment,
        # The signals go to the widget's audit row, so only when it is written.
        answer_signals=inputs.answer_signals if inputs.audit_ready else None,
        signal_chunks=found.chunks,
        turn_timing=timing,
        delegated_org_id=auth.zitadel_org_id,
        knowledge_turn=turn,
        pasted_correspondence=inputs.pasted_correspondence,
    )


def log_retrieval(org_id: int, user_id: str, chunks: list[dict]) -> None:
    """The chunks this turn read, keyed so that feedback on the answer finds them (rl:{org}:{user})."""
    task = asyncio.create_task(
        write_retrieval_log(
            org_id=org_id,
            user_id=user_id,
            chunk_ids=[c.get("chunk_id", "") for c in chunks if c.get("chunk_id")],
            reranker_scores=[c.get("reranker_score", 0.0) for c in chunks if c.get("reranker_score") is not None],
            query_resolved="",
            embedding_model_version="",
            retrieved_at=datetime.now(UTC),
        )
    )
    _pending.add(task)
    task.add_done_callback(_pending.discard)
