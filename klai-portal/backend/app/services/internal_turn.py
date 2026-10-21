"""The internal chat's turn (LibreChat employees in Strict, Open or general), on the help widget's flow.

The flow is chat-quality-history-and-plan.md §5 as widget_turn.py runs it:
read the case, search, select, route in code what the selection decided,
write. The steps that are the same come from widget_turn.py. What differs is
the end of the flow (§5, "Punt in de stroom"): the internal chat has no
appointment button, so there is no contact route and no off-topic referral;
the escalation, frustration and conversational addenda are help-widget prompt
concepts; and "not in the passages" is the Strict refusal that suggests the
open mode, while Open writes from every passage because it may answer from
general knowledge. ``app.api.partner.chat_completions`` resolves the
employee's profile and prompt settings before the turn and records and sends
what it returns.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NamedTuple

import structlog
from klai_chat_prompts import no_citable_sources_message

from app.core.config import Settings
from app.services import case_file
from app.services.answer_footer import render_answer_footer
from app.services.chat_profile import ChatProfile
from app.services.chat_prompting import _is_web_search_tool, _response_language
from app.services.chat_safety import _last_user_message
from app.services.retrieval_context import (
    INTERNAL_RETRIEVE_TOP_K,
    KnowledgeTurn,
    retrieval_upstream_errors,
    retrieve_context,
    strict_risk_model,
    timed,
)
from app.services.widget_reply import Reply
from app.services.widget_turn import (
    Found,
    fixed_reply,
    log_retrieval,
    mark_knowledge_turn_without_evidence,
    question_reply,
)
from app.services.widget_write import write_reply

if TYPE_CHECKING:
    from app.api.partner import ChatCompletionsRequest
    from app.api.partner_dependencies import PartnerAuthContext

logger = structlog.get_logger()


@dataclass
class InternalInputs:
    """What chat_completions resolved before the turn."""

    request: ChatCompletionsRequest
    auth: PartnerAuthContext
    settings: Settings
    profile: ChatProfile
    # The employee's active prompt templates as one block, and the org's telemetry level.
    templates_block: str
    telemetry_level: str
    pasted_correspondence: bool
    # Signals only, never text: the record is record_internal_turn's, LibreChat keeps the conversation.
    answer_signals: dict[str, Any]
    turn_started: float


class Live(NamedTuple):
    """An Open or general turn, which chat_completions streams as the writer's tokens arrive."""

    chunks: list[dict]
    system_prompt: str
    trusted_sources: list[dict[str, Any]]
    turn: KnowledgeTurn
    timing: dict[str, float]


async def internal_turn(inputs: InternalInputs) -> Reply | Live:
    turn = KnowledgeTurn()
    messages = inputs.request.messages
    case: case_file.Case | None = None
    case_ms = 0
    with retrieval_upstream_errors(inputs.auth.org_id):
        if inputs.profile.kb_mode == "general":
            # General mode searches nothing, so no step would read the case;
            # it would only hold back the first token.
            found, retrieval_ms = await timed(_search(inputs, turn, []))
        elif case_file.is_follow_up(messages) or inputs.pasted_correspondence:
            # 1. Read. A follow-up searches on the employee's own sentence plus
            # the case's two queries, as on the widget; that replaced the
            # internal query rewrite. A pasted mail does too on its first turn:
            # the rewrite used to distil it into a search query, and a long
            # mail as the only query buries the article that answers it.
            case, case_ms = await timed(_read_case(inputs))
            variants = case.search_variants(_last_user_message(messages) or "") if case else []
            # 2-3. Search, and select inside retrieve_context (passage_selection.py).
            found, retrieval_ms = await timed(_search(inputs, turn, variants))
        else:
            # A first question searches on its own words, with the case beside
            # it. The widget's first-turn paraphrases stay widget-only: they
            # were measured on the widget's meetlat, not on internal questions.
            (found, retrieval_ms), (case, case_ms) = await asyncio.gather(
                timed(_search(inputs, turn, [])), timed(_read_case(inputs))
            )
    if case is None and inputs.profile.kb_mode != "general":
        inputs.answer_signals["judge_failed"] = ["case"]
    profile, auth = inputs.profile, inputs.auth
    # The employee's sub: feedback on a reply finds its retrieval under
    # rl:{org}:{user}, so the log is written before any route returns, or a
    # thumbs-down on a question would bind to the previous turn's passages.
    log_retrieval(auth.org_id, profile.user_id or f"partner:{auth.key_id}", found.chunks)
    # 4. Route in code.
    if routed := _route(inputs, turn, found, case):
        return routed
    timing = {"started_at": inputs.turn_started, "retrieval_ms": retrieval_ms, "case_ms": case_ms}
    if profile.kb_mode != "strict":
        # 5. Write, live. An employee reads the tokens as they arrive, and an
        # Open answer may use general knowledge by design, so nothing holds it
        # for a check; the check runs after the reply (_check_answer_later).
        return Live(found.chunks, found.system_prompt, found.trusted_sources, turn, timing)
    # 5. Write, held until the reply is composed and judged, as on the widget.
    return await _write(inputs, turn, found, timing)


def _read_case(inputs: InternalInputs) -> Awaitable[case_file.Case | None]:
    # No previous case: LibreChat sends the whole conversation and the internal
    # record has no conversation key to store one under. No subjects either:
    # there is no referral to send an employee to.
    return case_file.update_case(
        inputs.request.messages, None, inputs.settings, delegated_org_id=inputs.auth.zitadel_org_id
    )


async def _search(inputs: InternalInputs, turn: KnowledgeTurn, follow_up_variants: list[str]) -> Found:
    request, auth, profile = inputs.request, inputs.auth, inputs.profile
    knowledge = request.knowledge
    chunks, system_prompt, trusted_sources, broad = await retrieve_context(
        follow_up_variants=follow_up_variants,
        answer_signals=inputs.answer_signals,
        org_id=auth.org_id,
        zitadel_org_id=auth.zitadel_org_id,
        # The employee's own KB settings scope the turn; no slugs means every
        # org KB, which retrieval-api scopes by the key's org and the employee.
        kb_slugs=list(profile.kb_slugs or []),
        messages=request.messages,
        settings=inputs.settings,
        pasted_correspondence=inputs.pasted_correspondence,
        is_preview=getattr(auth, "is_preview", False),
        retrieval_query=knowledge.query if knowledge is not None else None,
        top_k=INTERNAL_RETRIEVE_TOP_K,
        profile=profile,
        templates_block=inputs.templates_block,
        telemetry_level=inputs.telemetry_level,
        web_search_available=any(_is_web_search_tool(tool) for tool in request.tools or []),
        knowledge_turn=turn,
    )
    return Found(chunks, system_prompt, trusted_sources, broad, None)


def _route(inputs: InternalInputs, turn: KnowledgeTurn, found: Found, case: case_file.Case | None) -> Reply | None:
    language = _response_language(inputs.request.messages, pasted_correspondence=inputs.pasted_correspondence)
    if turn.refusal is not None:
        # Strict with nothing to search, no evidence, or the knowledge base
        # unreachable (retrieve_context): the fixed text, no model call.
        return _refusal(inputs, turn, turn.refusal, language)
    mark_knowledge_turn_without_evidence(turn, case)
    if turn.not_in_passages and inputs.profile.kb_mode == "strict":
        # Strict promised knowledge-base-only answers, and no passage answers
        # this question: the same fixed refusal as with zero passages, decided
        # before any model writes (step N). Open writes from every passage.
        logger.info("partner_chat_not_in_passages", org_id=inputs.auth.org_id, passages=len(found.chunks))
        return _refusal(inputs, turn, no_citable_sources_message(language, suggest_open_mode=True), language)
    # The selection step's one question, once per conversation, in Strict and
    # Open alike. No escalation: that is the widget's appointment concept.
    return question_reply(inputs, turn, found, None, case.scope if case else None)


def _refusal(inputs: InternalInputs, turn: KnowledgeTurn, text: str, language: str | None) -> Reply:
    return fixed_reply(inputs, turn, text, language, decision="refusal", refused=True, appointment=False)


async def _write(inputs: InternalInputs, turn: KnowledgeTurn, found: Found, timing: dict[str, float]) -> Reply:
    request, auth, profile = inputs.request, inputs.auth, inputs.profile
    knowledge = request.knowledge
    reply = await write_reply(
        messages=request.messages,
        model=strict_risk_model(
            request.model, profile=profile, turn=turn, chunks=found.chunks, messages=request.messages
        ),
        temperature=request.temperature,
        system_prompt=found.system_prompt,
        settings=inputs.settings,
        org_id=auth.org_id,
        citation_chunks=found.chunks,
        trusted_sources=found.trusted_sources,
        source_query=knowledge.query if knowledge is not None else None,
        answer_signals=inputs.answer_signals,
        turn_timing=timing,
        delegated_org_id=auth.zitadel_org_id,
        knowledge_turn=turn,
        pasted_correspondence=inputs.pasted_correspondence,
        profile=profile,
        tools=request.tools,
        tool_choice=request.tool_choice,
    )
    # LibreChat renders only text, so the sources and the turn's activity
    # follow the answer as a text footer, the last render step.
    footer = render_answer_footer(
        sources=reply.sources,
        kb_mode=profile.kb_mode,
        chunks_injected=len(found.chunks),
        sub_queries=turn.sub_queries,
        language=reply.language,
    )
    knowledge = request.knowledge
    if footer and reply.text and (knowledge is None or knowledge.include_sources):
        reply.text = f"{reply.text.rstrip()}\n\n{footer}"
    return reply
