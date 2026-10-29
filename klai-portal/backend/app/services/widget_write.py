"""Step 6 of every chat turn: the one writer, its citations, and the checks that decide what the reader gets.

The widget, a partner key and every internal mode are written here, delivered
in one of two ways. Held: the model call is held until the reply is composed
and judged (citations are chosen against the final text), and the caller
renders the ``Reply`` as frames or as a body (widget_reply.py). Streamed: an
internal Open or general turn on a streaming request, because an employee
reads the tokens as they arrive. The model's text goes out as it is written,
the reply settles on the whole text exactly as a held one does, and only the
unseen tail and the footer follow.

What a reply settles to depends on the mode, not on the delivery. The widget
and internal Strict are judged; Strict with what differs for an employee: the
Strict refusal that suggests the open mode instead of the helpdesk text, no
appointment button or sentence, the employee's own tools and attachments, and
statements kept as counts only. Open and general are shown as written and
checked afterwards for the record only.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from functools import partial
from typing import Any

import httpx
import structlog
from klai_chat_prompts import (
    LANGUAGE_NAMES,
    appointment_offer_sentence,
    chat_contract_article,
    strip_appointment_offer_marker,
)
from klai_chat_prompts import no_citable_sources_message as _no_citable_sources_message
from klai_chat_prompts.language import (
    LONG_PROSE_WORDS,
    METHOD_EXPLICIT_REQUEST,
    METHOD_FUNCTION_WORDS,
    identify_surface_language,
    resolve_conversation_language,
)

from app.core.config import Settings
from app.services.answer_check import AnswerCheck, check_hard_facts
from app.services.answer_compose import (
    _NO_CITABLE_SOURCES_DECISION_KEY,
    _answer_without_retrieved_sources,
    _check_answer_later,
    _compose_answer,
    _elapsed_ms,
    _fill_answer_signals,
    _grounding_label,
    _grounding_signals,
    _log_citation_rescues,
    _log_turn_timing,
)
from app.services.answer_epistemics import pop_answer_contract_stream_text, strip_answer_contract_markers
from app.services.answer_footer import render_answer_footer
from app.services.answer_grounding import check_statements, grounding_budget
from app.services.answer_judge import decide_answer, is_clarifying_question
from app.services.chat_profile import ChatProfile
from app.services.chat_prompting import (
    _augment_messages_with_system_prompt,
    _emit_language_correctness_log,
    _is_web_search_tool,
    _llm_request_body,
    _strip_web_search_tools,
)
from app.services.chat_safety import (
    _appointment_escalation,
    _appointment_escalation_signal,
    _helpdesk_refusal_offers,
    _last_user_message,
    _text_offers_appointment,
    output_safety_violation,
    safety_refusal_message,
    without_unsourced_contact_details,
)
from app.services.chat_sources import PageContext, _chunk_source_title
from app.services.knowledge_prompts import unchecked_questions_notice
from app.services.retrieval_context import _CHOSEN_PASSAGE_KEY, KnowledgeTurn
from app.services.widget_reply import AnswerUpstreamError, Reply
from app.trace import get_trace_headers

logger = structlog.get_logger()

# A dash used as punctuation: an em dash anywhere between words, an en dash
# with a space on either side. "9\u201317" keeps its range dash.
_PUNCTUATION_DASH = re.compile(r"\s*\u2014\s*|\s+\u2013\s+")

# What a streamed reply holds back at its end until the next delta shows it
# is not the start of a link.
_STREAM_GUARD_TAIL_CHARS = 32

# Word tokens, as the language module counts them.
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

# A streamed reply shows nothing until this many words are written, because
# that is when the language of what the model writes can be told: on
# 29 October 2026 the model answered English questions in Dutch under a
# "Respond in English" instruction (3 to 4 of 6 replayed widget turns on main,
# the source lines being Dutch), and a streamed reply cannot be taken back.
# Six words is what identify_surface_language needs to decide between Dutch
# and English on an opening line; on the model's pace that is well under a
# second.
_LANGUAGE_GATE_WORDS = 6


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
    sentiment: str | None = None,
    answer_signals: dict[str, Any] | None = None,
    signal_chunks: list[dict] | None = None,
    turn_timing: dict[str, float] | None = None,
    delegated_org_id: str | None = None,
    knowledge_turn: KnowledgeTurn | None = None,
    pasted_correspondence: bool = False,
    profile: ChatProfile = ChatProfile(surface="widget"),
    tools: list[dict] | None = None,
    tool_choice: Any | None = None,
    web_chunks: list[dict] | None = None,
    web_query: str | None = None,
    include_sources: bool = True,
    stream: bool = False,
) -> Reply:
    """Write the answer, compose its citations, check it, and return what the reader is to get.

    ``broad_mode`` is the turn's decision (see ``_broad_mode_active``) that this is
    a consented general-knowledge answer; the composer labels it and nothing is
    checked, because every fact in it would count as unsupported.
    ``answer_signals`` is the turn's audit sink: filled here, never part of what
    the visitor receives. ``signal_chunks`` is the retrieval result to score the
    answer on; it differs from ``citation_chunks`` on a broad turn, where the
    caller empties the citation list on purpose.
    ``profile`` is the widget's unless an internal turn is written; only that
    turn sends ``tools`` (a widget key may not), and Strict never searches the
    web, so a web-search tool is dropped there. ``web_chunks`` are the live web
    results an internal Open or general turn read beside the passages, cited as
    their own tier against ``web_query``. ``include_sources`` is the caller's
    choice; it drops the sources from the internal footer, never its warning.

    ``stream`` returns before the model is called, with ``Reply.deltas`` set:
    the model's text as the reader may see it, and the reply settles in place
    once those deltas are exhausted (see widget_reply.streamed_frames).
    """
    turn = knowledge_turn if knowledge_turn is not None else KnowledgeTurn(answer_mode=profile.kb_mode)
    decision = None if pasted_correspondence else resolve_conversation_language(messages)
    response_language = decision.language if decision else None
    # The writer repairs a reply's language only when the decision is firm: an
    # explicit request, the user's own function words, or langid on a question
    # of at least LONG_PROSE_WORDS words. A langid guess on a shorter question
    # is not enough to translate a reply by: "Ondersteunt Klai SIP-trunking?"
    # is identified as German at full confidence, and translating a correct
    # Dutch answer into German would be worse than the Dutch answer the model
    # gives. On the replayed widget questions of 29 October 2026, 5 of 6 were
    # langid decisions on 9 to 11 words.
    repair_language = (
        response_language
        if decision is not None
        and (
            decision.method in (METHOD_EXPLICIT_REQUEST, METHOD_FUNCTION_WORDS)
            or len(_WORD_RE.findall(_last_user_message(messages) or "")) >= LONG_PROSE_WORDS
        )
        else None
    )
    augmented = _augment_messages_with_system_prompt(
        messages, system_prompt, page_context, response_language=response_language, profile=profile
    )
    strict = profile.kb_mode == "strict"
    reply = Reply(
        text="",
        sources=[],
        signals=answer_signals if answer_signals is not None else {},
        language=response_language,
        turn=turn,
    )
    settle = partial(
        _settle,
        reply,
        messages=messages,
        model=model,
        settings=settings,
        org_id=org_id,
        citation_chunks=citation_chunks,
        trusted_sources=trusted_sources,
        signal_chunks=signal_chunks,
        web_chunks=web_chunks,
        web_query=web_query,
        # Kept for citation composition only: it is the KB-rewritten search
        # query and must never reach the refusal language.
        user_query=source_query or _last_user_message(messages) or "",
        # The page-context message is prepended, so the last user turn here is
        # the human's own words, never the rewritten retrieval query. The
        # refusal follows the conversation decision; this stays the input for
        # the output-safety refusal, which has no conversation decision to follow.
        visitor_query=_last_user_message(augmented) or "",
        broad_mode=broad_mode,
        conversational=conversational,
        force_escalation=force_escalation,
        sentiment=sentiment,
        answer_signals=answer_signals,
        turn_timing=turn_timing,
        delegated_org_id=delegated_org_id,
        pasted_correspondence=pasted_correspondence,
        profile=profile,
        include_sources=include_sources,
        repair_language=repair_language,
    )
    generation: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "settings": settings,
        "org_id": org_id,
        "delegated_org_id": delegated_org_id,
        "language": response_language,
        "tools": _strip_web_search_tools(tools) if strict else tools,
        # A forced choice of a tool Strict just stripped would name a tool the
        # request no longer carries.
        "tool_choice": None
        if strict and isinstance(tool_choice, dict) and _is_web_search_tool(tool_choice)
        else tool_choice,
    }
    if stream:
        reply.deltas = _streamed(
            _generate_streamed(augmented, **generation),
            pasted_correspondence=pasted_correspondence,
            settle=settle,
            target=repair_language,
        )
        return reply
    generation_started = time.perf_counter()
    completion, draft, tool_calls = await _generate(augmented, **generation)
    if strict:
        # The widget, a partner key and internal Strict have sent only these
        # since the held writer replaced their two generation paths.
        completion = {key: completion[key] for key in ("id", "model", "usage", "finish_reason") if key in completion}
    else:
        # An Open or general body was LiteLLM's own response, so it keeps every
        # top-level field LiteLLM sent; OpenAI clients expect `created`.
        completion.setdefault("created", int(time.time()))
    await settle(
        draft, generation_ms=_elapsed_ms(generation_started), completion=completion, tool_calls=tool_calls, shown=None
    )
    return reply


async def _settle(
    reply: Reply,
    draft: str,
    *,
    generation_ms: int,
    completion: dict[str, Any],
    tool_calls: list[dict] | None,
    shown: _StreamedText | None,
    messages: list[dict],
    model: str,
    settings: Settings,
    org_id: int | str | None,
    citation_chunks: list[dict] | None,
    trusted_sources: list[dict[str, Any]] | None,
    signal_chunks: list[dict] | None,
    web_chunks: list[dict] | None,
    web_query: str | None,
    user_query: str,
    visitor_query: str,
    broad_mode: bool,
    conversational: bool,
    force_escalation: bool,
    sentiment: str | None,
    answer_signals: dict[str, Any] | None,
    turn_timing: dict[str, float] | None,
    delegated_org_id: str | None,
    pasted_correspondence: bool,
    profile: ChatProfile,
    include_sources: bool,
    repair_language: str | None,
) -> None:
    """Compose, check and decide the written ``draft``, and put the outcome in ``reply``.

    ``shown`` is what a streamed reply already put on screen, None on a held one.
    ``repair_language`` is the language the reply is translated into when the
    model wrote another one; the judge and the citations work on the draft as
    written, because they match it against the source passages in their own
    language, and only the accepted text is translated.
    """
    turn, response_language = reply.turn, reply.language
    helpdesk = profile.surface != "internal"
    reply.completion, reply.tool_calls = completion, tool_calls
    if tool_calls and not draft.strip():
        # A turn that only calls a tool is not an answer yet: the client runs
        # the tool and sends its result back. Composing it would put the
        # no-sources refusal after the tool call. The record still gets the
        # route and the timing.
        if answer_signals is not None:
            answer_signals.update(decision="tool_calls", model=model, sources_count=0, refused=False)
        _log_turn_timing(
            turn_timing, org_id=org_id, generation_ms=generation_ms, answer_judge_ms=0, answer_signals=answer_signals
        )
        return
    if pasted_correspondence:
        draft = strip_answer_contract_markers(draft)
    content, sources, decision = _compose_answer(
        draft,
        trusted_sources,
        citation_chunks,
        user_query,
        web_chunks,
        web_query,
        helpdesk=helpdesk,
        broad=broad_mode,
        force_escalation=force_escalation,
        response_language=response_language,
        suggest_open_mode=not helpdesk,
    )
    judge_started = time.perf_counter()
    if safety_reason := output_safety_violation(draft):
        logger.warning(
            "partner_chat_output_blocked", org_id=org_id, stage="stream_composed_output", reason=safety_reason
        )
        content = safety_refusal_message(visitor_query)
        sources = []
        decision = {"reason": safety_reason, "output_blocked": True}
    elif profile.kb_mode == "strict":
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
            knowledge_turn=turn,
            delegated_org_id=delegated_org_id,
            helpdesk=helpdesk,
        )
        if helpdesk:
            content = without_dashes(content)
        if sentiment:
            decision["sentiment"] = sentiment
    elif settled := _open_answer(
        content,
        sources,
        decision,
        draft=draft,
        messages=messages,
        citation_chunks=citation_chunks,
        settings=settings,
        org_id=org_id,
        answer_signals=answer_signals,
        knowledge_turn=turn,
        delegated_org_id=delegated_org_id,
    ):
        content, sources, decision = settled
        # The text the employee saw: the draft as it streamed, which cannot be
        # taken back, or the held reply, which no longer holds the links the
        # source gate removed.
        seen = strip_appointment_offer_marker(draft)[0] if shown is not None else content
        _flag_hard_facts(
            seen,
            decision,
            turn=turn,
            messages=messages,
            citation_chunks=citation_chunks,
            answer_signals=answer_signals,
        )
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
    if not decision.get("output_blocked") and (shown is None or not shown.emitted):
        # A held reply, or a streamed one the gate kept back: what the reader
        # gets is the accepted text, translated when the model wrote the
        # source language. A streamed reply that was shown cannot be taken back.
        content = await _in_decided_language(
            content,
            reply,
            target=repair_language,
            model=model,
            settings=settings,
            org_id=org_id,
            delegated_org_id=delegated_org_id,
            language=response_language,
        )
        if helpdesk and "language_repaired" in reply.signals:
            content = without_dashes(content)
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
    # The query side of this measurement is the visitor's own words, never a
    # rewritten search query (docs: knowledge.md, "Multilingual chat").
    _emit_language_correctness_log(
        org_id=org_id, query=visitor_query, response_text=content, chunks_injected=len(citation_chunks or [])
    )
    reply.text = _sent_text(
        content,
        draft=draft,
        shown=shown,
        blocked=bool(decision.get("output_blocked")),
        notice=""
        if decision.get("output_blocked")
        else unchecked_questions_notice(turn.unchecked_questions, response_language),
        footer=_footer(
            reply, sources, decision, profile=profile, passages=citation_chunks, include_sources=include_sources
        )
        if not helpdesk
        else "",
        citation_chunks=citation_chunks,
    )
    # A safety refusal replaced the decision wholesale, so a blocked answer
    # neither labels itself general knowledge, invites the visitor to broaden,
    # nor carries a booking button.
    reply.sources = sources
    reply.appointment = _appointment_escalation_signal(decision) is not None
    reply.broad_mode = decision.get("broad_mode")
    reply.passages = len(citation_chunks or [])
    reply.web_results = len(web_chunks or [])


def _footer(
    reply: Reply,
    sources: list[dict],
    decision: dict[str, Any],
    *,
    profile: ChatProfile,
    passages: list[dict] | None,
    include_sources: bool,
) -> str:
    """The internal chat's text footer: LibreChat renders only text, so the sources and the activity follow the answer.

    A caller that asked for no sources still gets the warning about a hard
    fact outside the knowledge base: on a streamed reply that text is already
    on screen.
    """
    return render_answer_footer(
        sources=sources if include_sources else [],
        kb_mode=profile.kb_mode,
        chunks_injected=len(passages or []),
        sub_queries=reply.turn.sub_queries,
        language=reply.language,
        general_knowledge=include_sources and decision.get("reason") == "general_knowledge",
        hard_facts=decision.get("hard_facts_outside_kb"),
    )


def _sent_text(
    content: str,
    *,
    draft: str,
    shown: _StreamedText | None,
    blocked: bool,
    notice: str,
    footer: str,
    citation_chunks: list[dict] | None,
) -> str:
    """What is sent of the settled reply: the notice about unchecked questions, then the footer as the last render step.

    On a held reply that is the whole reply. A streamed one already showed the
    model's own words up to any link, so only the unseen tail follows; a
    safety block cannot take back what streamed, so the rest of the draft is
    withheld and the refusal follows it, without the notice. The notice sits
    before the footer so the footer stays the tail that
    ``strip_answer_footer_from_text`` cuts from the history.
    """
    if shown is not None and blocked:
        return f"\n\n{content}"
    extras = [part for part in (notice, footer) if part]
    if shown is None:
        return "\n\n".join([content.rstrip(), *extras]) if extras and content else content
    tail = _unstreamed_tail(content, "".join(shown.emitted), draft, citation_chunks)
    return tail.rstrip() + "".join(f"\n\n{part}" for part in extras) if extras else tail


async def _generate(
    messages: list[dict],
    *,
    model: str,
    temperature: float,
    settings: Settings,
    org_id: int | str | None,
    delegated_org_id: str | None,
    language: str | None,
    tools: list[dict] | None,
    tool_choice: Any | None,
) -> tuple[dict[str, Any], str, list[dict] | None]:
    """The answer model's text, the id, model and usage it came with, and the tools it called."""
    chat_url = f"{settings.litellm_base_url}/v1/chat/completions"
    request_json = _llm_request_body(
        model,
        messages,
        temperature,
        stream=False,
        tools=tools,
        tool_choice=tool_choice,
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
    except ValueError as exc:
        # A 200 that is not JSON: the old streamed path skipped such lines and
        # fell back to the refusal; here it is an upstream error like any other.
        logger.warning("partner_chat_upstream_error", org_id=org_id, status_code=200, exc_info=True)
        raise AnswerUpstreamError("Chat service error", language) from exc
    except httpx.TransportError as exc:
        logger.warning("partner_chat_upstream_unreachable", org_id=org_id, target=chat_url, exc_info=True)
        raise AnswerUpstreamError("Chat service unavailable", language) from exc
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "partner_chat_upstream_error", org_id=org_id, status_code=exc.response.status_code, exc_info=True
        )
        raise AnswerUpstreamError("Chat service error", language) from exc
    choices = body.get("choices") or []
    message = (choices[0].get("message") or {}) if choices else {}
    text = message.get("content") or ""
    completion = {key: value for key, value in body.items() if key != "choices"}
    if choices and choices[0].get("finish_reason"):
        # A truncated answer keeps saying so ("length"), as the plain body always did.
        completion["finish_reason"] = choices[0]["finish_reason"]
    return completion, text if isinstance(text, str) else "", message.get("tool_calls") or None


async def _generate_streamed(
    messages: list[dict],
    *,
    model: str,
    temperature: float,
    settings: Settings,
    org_id: int | str | None,
    delegated_org_id: str | None,
    language: str | None,
    tools: list[dict] | None,
    tool_choice: Any | None,
) -> AsyncIterator[str | list[dict]]:
    """The answer model's text deltas, and its tool-call deltas, as they arrive.

    A tool-call delta never enters the text, so it can never reach the citation
    composer or the grounding check. Once the stream has started a failure
    cannot become a 502, so it is raised for the renderer to send as an error
    frame.
    """
    chat_url = f"{settings.litellm_base_url}/v1/chat/completions"
    request_json = _llm_request_body(
        model,
        messages,
        temperature,
        stream=True,
        tools=tools,
        tool_choice=tool_choice,
        delegated_org_id=delegated_org_id,
    )
    try:
        async with (
            httpx.AsyncClient(timeout=120.0) as client,
            client.stream(
                "POST",
                chat_url,
                json=request_json,
                headers={"Authorization": f"Bearer {settings.litellm_master_key}", **get_trace_headers()},
            ) as resp,
        ):
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                payload = line.removeprefix("data: ").strip() if line.startswith("data: ") else ""
                if payload == "[DONE]":
                    break
                if not payload:
                    continue
                try:
                    event: dict[str, Any] = json.loads(payload)
                except json.JSONDecodeError:
                    logger.debug("partner_chat_sse_parse_skipped", exc_info=True)
                    continue
                delta = (event.get("choices") or [{}])[0].get("delta") or {}
                if tool_calls := delta.get("tool_calls"):
                    yield tool_calls
                text = delta.get("content")
                if isinstance(text, str) and text:
                    yield text
    except httpx.TransportError as exc:
        logger.warning("partner_chat_upstream_unreachable", org_id=org_id, target=chat_url, exc_info=True)
        raise AnswerUpstreamError("Chat service unavailable", language) from exc
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "partner_chat_upstream_error", org_id=org_id, status_code=exc.response.status_code, exc_info=True
        )
        raise AnswerUpstreamError("Chat service error", language) from exc


async def _in_decided_language(
    text: str,
    reply: Reply,
    *,
    target: str | None,
    model: str,
    settings: Settings,
    org_id: int | str | None,
    delegated_org_id: str | None,
    language: str | None,
) -> str:
    """The accepted text, translated into ``target`` when the model wrote another language.

    The final system instruction names the language, and the model still
    follows its source lines often enough (3 to 4 of 6 replayed English widget
    turns on a Dutch knowledge base, 29 October 2026) that the instruction
    alone is not the contract. One translation call, only on a mismatch.
    ``target`` is None when the turn's decision is not firm enough to
    translate by (see ``write_reply``). The judge has already accepted the
    text against the passages in their own language: translating the draft
    first made the Strict judge refuse a correct answer in 2 of 2 replayed
    rounds, because it no longer matched the Dutch passages word for word.
    """
    if not target:
        return text
    written = identify_surface_language(text)
    if not written or written == target:
        return text
    name = LANGUAGE_NAMES.get(target, target)
    messages = [
        {
            "role": "system",
            "content": (
                # The marker pattern is described, not shown: shown as "[n]"
                # in this instruction, the model wrote a literal "[n]" into a
                # translated step on 29 October 2026.
                f"Translate the reply below into {name}. Keep the markdown, the links, the numbered citation "
                "markers in square brackets and the names of menus, buttons and settings exactly as they are; "
                "translate everything else. Reply with the translation only."
            ),
        },
        {"role": "user", "content": text},
    ]
    _, translated, _ = await _generate(
        messages,
        model=model,
        temperature=0.0,
        settings=settings,
        org_id=org_id,
        delegated_org_id=delegated_org_id,
        language=language,
        tools=None,
        tool_choice=None,
    )
    if not translated.strip():
        return text
    reply.signals["language_repaired"] = {"from": written, "to": target}
    logger.info("partner_chat_language_repaired", org_id=org_id, written=written, target=target)
    return translated


@dataclass
class _StreamedText:
    """What the model has written so far on a streamed reply, and what of it the reader has seen."""

    pasted_correspondence: bool
    target: str | None = None
    raw: list[str] = field(default_factory=list)
    emitted: list[str] = field(default_factory=list)
    held_back: str = ""
    marker_buffer: str = ""
    # The language gate: closed until the opening words can be judged, then
    # either open or, when the model writes another language than the turn
    # decided, shut for good: the reader then sees the translation only.
    gate_closed: bool = True
    wrong_language: bool = False

    def take(self, text: str, *, final: bool = False) -> str:
        """Keep a content delta and return the part of it that is safe to send now."""
        if text:
            self.raw.append(text)
        if self.gate_closed:
            if self.target is None:
                self.gate_closed = False
            else:
                head = "".join(self.raw)
                if len(head.split()) < _LANGUAGE_GATE_WORDS and not final:
                    return ""
                written = identify_surface_language(head)
                self.gate_closed = False
                self.wrong_language = bool(written) and written != self.target
                text = head
        if self.wrong_language:
            return ""
        if self.pasted_correspondence:
            text, self.marker_buffer = pop_answer_contract_stream_text(self.marker_buffer + text, final=False)
        shown, self.held_back = _pop_streamable_text(self.held_back + text)
        if shown:
            self.emitted.append(shown)
        return shown


async def _streamed(
    deltas: AsyncIterator[str | list[dict]],
    *,
    pasted_correspondence: bool,
    settle: Callable[..., Awaitable[None]],
    target: str | None,
) -> AsyncIterator[str | list[dict]]:
    """Pass the model's deltas on as the reader may see them, then settle the reply on the whole text.

    The text goes out with the model's own markers as written; citations are
    composed once, on the accumulated text, never re-rendered mid-stream, which
    is what keeps the footer's numbering in step with what the reader saw.
    """
    text = _StreamedText(pasted_correspondence=pasted_correspondence, target=target)
    tool_calls: list[dict] = []
    generation_started = time.perf_counter()
    async for delta in deltas:
        if isinstance(delta, list):
            tool_calls.extend(delta)
            yield delta
        elif shown := text.take(delta):
            yield shown
    # A reply shorter than the gate is judged on what there is. When the gate
    # kept the whole reply back, nothing was shown: settle translates the
    # accepted text and the reader gets it as the reply's final frames.
    if shown := text.take("", final=True):
        yield shown
    await settle(
        "".join(text.raw),
        generation_ms=_elapsed_ms(generation_started),
        completion={},
        tool_calls=tool_calls or None,
        shown=text,
    )


def _earliest_guard_start(text: str) -> int:
    starts = [
        idx
        for idx in (
            text.find("["),
            text.find("!["),
            text.find("http://"),
            text.find("https://"),
        )
        if idx >= 0
    ]
    return min(starts) if starts else -1


def _pop_streamable_text(buffer: str) -> tuple[str, str]:
    """Withhold an incomplete markdown link/URL from a streamed reply.

    It only withholds and never rewrites citation markup, because a streamed
    reply shows the model's own words as they arrive; composition still runs
    once on the full text at the end.
    """
    start = _earliest_guard_start(buffer)
    if start >= 0:
        return buffer[:start], buffer[start:]
    if len(buffer) <= _STREAM_GUARD_TAIL_CHARS:
        return "", buffer
    safe_len = len(buffer) - _STREAM_GUARD_TAIL_CHARS
    return buffer[:safe_len], buffer[safe_len:]


def _collapse_whitespace_with_index_map(text: str) -> tuple[str, list[int]]:
    collapsed: list[str] = []
    index_map: list[int] = []
    in_whitespace = False
    for index, char in enumerate(text):
        if char.isspace():
            if collapsed and not in_whitespace:
                collapsed.append(" ")
                index_map.append(index)
            in_whitespace = True
            continue
        collapsed.append(char)
        index_map.append(index)
        in_whitespace = False
    if collapsed and collapsed[-1] == " ":
        collapsed.pop()
        index_map.pop()
    return "".join(collapsed), index_map


def _unstreamed_tail(final_text: str, emitted_text: str, raw_text: str, citation_chunks: list[dict] | None) -> str:
    """The part of a streamed answer the reader has not seen yet, cleaned.

    A streamed reply shows the model's words up to any link or URL, which the
    guard holds back until the end. The rest must come from the composed answer
    so a model-written link is cleaned there. The composer may
    normalise whitespace, so the cut tolerates that; if it changed text the
    reader already saw, the answer cannot be aligned and replaying it would
    duplicate the whole answer (LibreChat, 2026-06-11), so the raw remainder
    goes out with every link removed instead.
    """
    if not emitted_text or final_text.startswith(emitted_text):
        return final_text[len(emitted_text) :]
    collapsed_final, final_map = _collapse_whitespace_with_index_map(final_text)
    collapsed_emitted, _ = _collapse_whitespace_with_index_map(emitted_text)
    if collapsed_emitted and collapsed_final.startswith(collapsed_emitted):
        return final_text[final_map[len(collapsed_emitted) - 1] + 1 :]
    remainder = raw_text[len(emitted_text) :]
    leading = remainder[: len(remainder) - len(remainder.lstrip())]
    return leading + _answer_without_retrieved_sources(remainder, citation_chunks)


def _open_answer(
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
    knowledge_turn: KnowledgeTurn,
    delegated_org_id: str | None,
) -> tuple[str, list[dict], dict[str, Any]] | None:
    """Settle an internal Open or general reply: record the one decision, start the check.

    An Open answer may use general knowledge by design, so it is shown as
    written and checked afterwards for the record only; a general turn is not
    checked at all, it had no articles. None when the model wrote nothing a
    reader could get: the composer's refusal stands.
    """
    answer_mode = knowledge_turn.answer_mode
    draft_text, _ = strip_appointment_offer_marker(draft)
    safe_text = _answer_without_retrieved_sources(draft_text, citation_chunks)
    if not safe_text:
        return None
    articles = [(_chunk_source_title(chunk), str(chunk.get("text") or "")) for chunk in citation_chunks or []]
    outcome = decide_answer(
        kb_mode=answer_mode,
        has_sources=bool(sources),
        escalation=False,
        conversational=False,
        open_part=False,
        grounding=None,
    )
    logger.info("partner_chat_answer_judge", org_id=org_id, decision=outcome, kb_mode=answer_mode)
    if answer_signals is not None:
        answer_signals["decision"] = outcome
    if answer_mode == "open" and articles and answer_signals is not None:
        knowledge_turn.grounding_check = _check_answer_later(
            question=_last_user_message(messages) or "",
            draft=safe_text,
            articles=articles,
            settings=settings,
            org_id=org_id,
            delegated_org_id=delegated_org_id,
            answer_signals=answer_signals,
        )
    # Without a source the composer's refusal is replaced by the model's own
    # words, cleaned like every reply without a source; an Open one is
    # labelled general knowledge in the footer.
    # A general turn searched nothing, so its links (web search, file
    # names) are the model's or the user's own and stay as written.
    if not sources:
        content, decision = (draft_text if answer_mode == "general" else safe_text), {"reason": outcome}
    return content, sources, decision


def _flag_hard_facts(
    seen: str,
    decision: dict[str, Any],
    *,
    turn: KnowledgeTurn,
    messages: list[dict],
    citation_chunks: list[dict] | None,
    answer_signals: dict[str, Any] | None,
) -> None:
    """Count an Open reply's e-mail addresses, links, phone numbers and amounts that stand in no passage.

    Per kind, for the footer's warning and the record: the employee may
    already have read them, so they are flagged rather than removed.
    """
    if turn.answer_mode != "open" or not (
        hard_facts := check_hard_facts(seen, citation_chunks, messages).hard_facts_outside
    ):
        return
    decision["hard_facts_outside_kb"] = hard_facts
    if answer_signals is not None:
        answer_signals["hard_facts_outside_kb"] = hard_facts


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
    knowledge_turn: KnowledgeTurn,
    delegated_org_id: str | None,
    helpdesk: bool,
) -> tuple[str, list[dict], dict[str, Any]]:
    """SPEC-RAG-ANSWER-JUDGES-001 REQ-2/REQ-3: the statement check, then the one decision.

    The check sees the draft after the marker and link stripper, exactly the
    text the visitor would get. An empty result (the model wrote nothing) has
    nothing to check and keeps the composer's refusal; a consented broad-mode
    answer is not checked either.

    An uncited answer keeps only an appointment offer that the backend forced
    or the model's own words make, or one the turn calls for because it did
    not answer from the articles; a partial answer always gets one.
    """
    if decision.get("broad_mode") == "answer":
        return content, sources, decision
    draft_text, model_offered_appointment = strip_appointment_offer_marker(draft)
    safe_text = _answer_without_retrieved_sources(draft_text, citation_chunks)
    if helpdesk:
        # The judges may hand this text to the visitor instead of the composed
        # one; it passes the same provenance gate.
        safe_text = without_unsourced_contact_details(safe_text, citation_chunks)
    if not safe_text:
        return content, sources, decision
    searched_nothing = knowledge_turn.answer_mode == "general"
    own_input = knowledge_turn.user_provided_content or knowledge_turn.pasted_correspondence
    if not helpdesk and (searched_nothing or own_input):
        # An internal Strict turn that searched nothing (a thank-you, a
        # question about Klai itself) has no article to miss, and one that
        # rests on what the employee attached or pasted is not judged against
        # the articles: two correct observations from a screenshot read as two
        # unsupported statements. Without a source the model's own words go
        # out; a turn that searched nothing keeps its links, they are the
        # model's or the employee's own.
        if answer_signals is not None:
            answer_signals["decision"] = "answer"
        if sources:
            return content, sources, decision
        return (
            (draft_text, [], {"reason": "answer"})
            if searched_nothing
            else (safe_text, [], {"reason": "user_provided_content"})
        )
    articles = [(_chunk_source_title(chunk), str(chunk.get("text") or "")) for chunk in citation_chunks or []]
    # The checker only sees the question, the articles and the reply, so it cannot
    # know a booking button is really attached and flagged "Klik op de knop hieronder
    # om een afspraak in te plannen" as an unsupported claim about the company. Handing
    # it the guarantee as one more article keeps it strict about what that appointment
    # will DO, which a skip-list in the prompt could not separate. The internal chat
    # has no button.
    if helpdesk and (contract := chat_contract_article(appointment_offered=True)) is not None:
        articles = [*articles, contract]
    chosen = bool(citation_chunks) and all(chunk.get(_CHOSEN_PASSAGE_KEY) for chunk in citation_chunks or [])
    deferred = chosen and bool(sources) and answer_signals is not None
    checks_started = time.perf_counter()
    if deferred and answer_signals is not None:
        # The writer read only passages the selection step chose and cited
        # one, and after writing nothing changes such a reply (below), so the
        # statement check is measurement and runs after the reply went out. On
        # the critical path it cost 1.6 s median of a 6.2 s written turn in the
        # first week of real traffic. An uncited reply keeps the check on the
        # path: without a source its statements decide between showing and
        # refusing (decide_answer).
        grounding = None
        knowledge_turn.grounding_check = _check_answer_later(
            question=_last_user_message(messages) or "",
            draft=safe_text,
            articles=articles,
            settings=settings,
            org_id=org_id,
            delegated_org_id=delegated_org_id,
            answer_signals=answer_signals,
            keep_statements=helpdesk,
        )
    else:
        grounding = await check_statements(
            question=_last_user_message(messages) or "",
            draft=safe_text,
            articles=articles,
            settings=settings,
            org_id=org_id,
            timeout_seconds=grounding_budget("widget" if helpdesk else "internal"),
            delegated_org_id=delegated_org_id,
        )
    checks_ms = _elapsed_ms(checks_started)
    label = _grounding_label(grounding) if grounding is not None else None
    outcome = decide_answer(
        kb_mode="strict",
        has_sources=bool(sources),
        escalation=force_escalation,
        conversational=conversational,
        open_part=bool(knowledge_turn.open_parts),
        grounding=label,
    )
    logger.info(
        "partner_chat_answer_judge",
        org_id=org_id,
        decision=outcome,
        grounding=label,
        grounding_checked=grounding is not None,
        checks_ms=checks_ms,
        unsupported=len(grounding.unsupported) if grounding is not None else None,
    )
    if answer_signals is not None:
        answer_signals["decision"] = outcome
        if not deferred:
            # The internal record keeps signals, never text (record_internal_turn).
            _grounding_signals(answer_signals, grounding, keep_statements=helpdesk)
            if label is not None:
                answer_signals["grounding"] = label
    return _decided(
        outcome,
        content,
        sources,
        decision,
        safe_text=safe_text,
        grounding=grounding,
        citation_chunks=citation_chunks,
        response_language=response_language,
        force_escalation=force_escalation,
        # Unanswered is the selection's call: a part it left open, or no
        # selection at all (no passages, or none it could read). A selection
        # that lists no part (a thank-you) asked nothing of the articles.
        unanswered=not conversational and (bool(knowledge_turn.open_parts) or knowledge_turn.selection is None),
        offered_by_model=model_offered_appointment,
        helpdesk=helpdesk,
    )


def _refusal(
    reason: str, response_language: str | None, citation_chunks: list[dict] | None, *, helpdesk: bool
) -> tuple[str, list[dict], dict[str, Any]]:
    """The fixed "not in the sources" reply: the helpdesk text with its offers, or the Strict text suggesting Open."""
    decision: dict[str, Any] = {"reason": reason, _NO_CITABLE_SOURCES_DECISION_KEY: True}
    if helpdesk:
        decision.update(_helpdesk_refusal_offers(citation_chunks))
    text = _no_citable_sources_message(response_language, helpdesk=helpdesk, suggest_open_mode=not helpdesk)
    return text, [], decision


def _decided(
    outcome: str,
    content: str,
    sources: list[dict],
    decision: dict[str, Any],
    *,
    safe_text: str,
    grounding: AnswerCheck | None,
    citation_chunks: list[dict] | None,
    response_language: str | None,
    force_escalation: bool,
    unanswered: bool,
    offered_by_model: bool,
    helpdesk: bool,
) -> tuple[str, list[dict], dict[str, Any]]:
    """What the visitor gets for the decision: the composed reply, a refusal, or the model's own words.

    ``unanswered``: the turn is not conversational and did not answer from the
    articles, because no selection ran or it left a part the visitor asked
    open (``KnowledgeTurn.open_parts``).
    """
    refused = bool(decision.get(_NO_CITABLE_SOURCES_DECISION_KEY))
    if outcome == "refusal":
        if refused:
            return content, sources, decision
        return _refusal("answer_judge_refusal", response_language, citation_chunks, helpdesk=helpdesk)
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
        # answer, and a button under "graag gedaan" offers help with nothing.
        # The internal chat has no button to offer.
        if helpdesk and (force_escalation or unanswered or (offered_by_model and _text_offers_appointment(safe_text))):
            decision["escalation"] = _appointment_escalation()
    if outcome == "partial_answer" and helpdesk:
        decision["escalation"] = _appointment_escalation()
    # Only a reply the visitor actually reads: a refusal states nothing about
    # the organisation. Nothing changes a reply
    # after it is written: what the writer may read is decided before
    # (passage_selection.py), and the check's verdict is kept for measuring.
    # Editing a flagged reply was the largest cause of a bad answer in the
    # owner's review, and turning it into a refusal on one "contradicted"
    # threw away a correct procedure whose warning the check misread.
    chosen = bool(citation_chunks) and all(chunk.get(_CHOSEN_PASSAGE_KEY) for chunk in citation_chunks or [])
    if (
        outcome in ("answer", "partial_answer")
        and grounding is not None
        and len(grounding.claims) >= 2
        and len(grounding.unsupported) == len(grounding.claims)
        and not chosen
    ):
        # The selection step did not choose this turn's passages (it failed,
        # or was skipped), so the writer read everything retrieval found. A
        # reply in which the check then supports nothing is not shown.
        return _refusal("grounding_nothing_left", response_language, citation_chunks, helpdesk=helpdesk)
    # A reply without a source that does not answer from the articles is the
    # visitor's dead end, and the button under it is the way out. The profile
    # and the weak-source rule ask the model to say so; measured on the owner's
    # review of real answers, 5 of 15 such replies did not. The sentence is
    # ours, so it is there whenever the button is. Not behind a question: the
    # visitor is asked something, not sent away. Not under a reply that kept
    # its source: the button may be added to such a reply, its text never changes.
    if (
        not sources
        and decision.get("escalation")
        and unanswered
        and not _text_offers_appointment(content)
        and not is_clarifying_question(content)
    ):
        content = f"{content.rstrip()} {appointment_offer_sentence(response_language)}"
    return content, sources, decision
