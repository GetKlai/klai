"""Partner chat completions service.

SPEC-API-001 TASK-008/009:
- Retrieve context from retrieval-api
- Forward to LiteLLM for non-streaming and streaming completions
- Build augmented system prompt with retrieved chunks

SPEC-RAG-MULTILINGUAL-CHAT-001 REQ-02: the grounded system prompt is
imported from the shared library ``klai-chat-prompts``. Do NOT inline
the prompt here — both this service and ``klai-retrieval-api``'s
``services/synthesis.py`` MUST load the same constant. A CI lint
asserts no service contains a hardcoded copy.

REQ-07 measures the visitor's query and the model response with the SAME
identifier that steers the prompt (``klai_chat_prompts.language``), so
VictoriaLogs gets ``language_correctness`` per ``chat_synthesis_complete``
event. There is deliberately no second detector: a second library is a second
guess, not a second opinion, and it measured a different thing than the prompt
targeted.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncGenerator
from typing import Any

import httpx
import structlog
from fastapi import HTTPException, status
from klai_chat_prompts import (
    broad_mode_answer_marker,
    strip_appointment_offer_marker,
)
from klai_chat_prompts import (
    no_citable_sources_message as _no_citable_sources_message,
)
from klai_chat_prompts.language import (
    UNKNOWN_LANGUAGE,
    identify_text_language,
)

from app.core.config import Settings
from app.services.answer_epistemics import pop_answer_contract_stream_text, strip_answer_contract_markers
from app.services.answer_footer import render_answer_footer
from app.services.answer_grounding import (
    GroundingCheck,
    check_grounding,
    grounding_budget,
)
from app.services.answer_judge import decide_answer, is_clarifying_question
from app.services.chat_profile import ChatProfile
from app.services.chat_prompting import (
    _augment_messages_with_system_prompt,
    _emit_language_correctness_log,
    _llm_request_body,
    _response_language,
)
from app.services.chat_safety import (
    _appointment_escalation,
    _helpdesk_refusal_offers,
    _last_user_message,
    _text_offers_appointment,
    output_safety_violation,
    safety_refusal_message,
    without_unsourced_contact_details,
)
from app.services.chat_sources import (
    _chunk_source_title,
)
from app.services.chat_sse import (
    _sse_activity_delta,
    _sse_content_delta,
    _sse_error_frame,
    _sse_language_delta,
    _sse_sources_delta,
    _sse_tool_calls_delta,
)
from app.services.citations import (
    ComposedCitations,
    compose_answer_with_trusted_sources,
    evidence_chunks_from_chunks,
    source_url_key,
    strip_model_citation_artifacts,
    urls_in_passages,
)
from app.services.gap_classification import classify_gap
from app.services.retrieval_context import _CHOSEN_PASSAGE_KEY, KnowledgeTurn, _top_chunk_score
from app.trace import get_trace_headers

logger = structlog.get_logger()

_STREAM_GUARD_TAIL_CHARS = 32


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


def _pop_live_stream_text(buffer: str) -> tuple[str, str]:
    """Withhold an incomplete markdown link/URL from a live (Open) stream.

    It only withholds and never rewrites citation markup, because a live turn
    shows the model's own words as they arrive; composition still runs once on
    the full text at the end, same as a held turn.
    """
    start = _earliest_guard_start(buffer)
    if start >= 0:
        return buffer[:start], buffer[start:]
    if len(buffer) <= _STREAM_GUARD_TAIL_CHARS:
        return "", buffer
    safe_len = len(buffer) - _STREAM_GUARD_TAIL_CHARS
    return buffer[:safe_len], buffer[safe_len:]


_NO_CITABLE_SOURCES_DECISION_KEY = "no_citable_sources_refusal"
"""Decision key marking "this answer IS the fixed no-citable-sources refusal".

Only the composer knows that — it decides between a grounded answer and the
canned text — and the audit trail must record it without reading the
visitor-facing wording back. Callers ``pop`` it before the decision is logged,
so ``partner_chat_citation_selection_decision`` keeps its exact payload.
"""


def _fill_answer_signals(
    sink: dict[str, Any] | None,
    *,
    decision: dict[str, Any],
    refused: bool,
    chunks: list[dict] | None,
    sources: list[dict],
    model: str | None,
    query_text: str,
    reply: str = "",
) -> None:
    """Write this answer's certainty signals into the caller-owned audit sink.

    The sink is an in-process hand-off to ``record_widget_turn`` (partner.py
    creates it and reads it back once the turn is done): nothing in it is ever
    emitted as an SSE frame or written into the completion body, so a visitor
    cannot see how certain we were — only the audit row can
    (SPEC-KNOWLEDGE-ACTIVITY-001 §4.1).

    ``chunks`` must be the retrieval result the turn was decided on, not the
    citation list: a broad-mode turn deliberately cites nothing, yet its
    certainty is exactly the weak retrieval that triggered broad mode.
    ``language`` is the visitor's question language, not the answer's, so a
    gap in English knowledge groups as English even when the model answered
    in Dutch (spec §4.9).

    Deriving the signals must never cost the visitor their answer, so a
    failure costs the audit row its signals and one loud warning.
    """
    if sink is None:
        return
    try:
        _, top_score = _top_chunk_score(chunks or [])
        sink.update(
            {
                "top_score": top_score,
                "gap_type": classify_gap(chunks or []),
                "sources_count": len(sources),
                "refused": refused,
                # Only "answer" is a broad answer; "offer" is a refusal that
                # asks the visitor for broad-mode consent.
                "broad_mode": decision.get("broad_mode") == "answer",
                # Visitor text, so the intent-aware entry point: "graag in het
                # Nederlands" is a Dutch turn whatever language it is typed in.
                "language": identify_text_language(query_text) or UNKNOWN_LANGUAGE,
                "model": model,
                # What the visitor was shown, for whoever reviews stored answers:
                # the button is a frame beside the text, so the text alone
                # cannot tell whether an offer was there.
                "appointment": bool(decision.get("escalation")),
            }
        )
        if decision.get("sentiment"):
            sink["sentiment"] = decision["sentiment"]
        if sink.get("planned_question"):
            sink["question_asked"] = is_clarifying_question(reply)
        # retrieve_context writes the band; a turn that never retrieved has none.
        sink.setdefault("band", "unknown")
    except Exception:
        logger.warning("partner_chat_answer_signals_failed", exc_info=True)


# Anything a reader could click or paste. strip_model_citation_artifacts is a
# CITATION cleaner, not a URL firewall: it only knows the "scheme://" shape, so
# "www.evil.example/phish" and "evil.example/phish" walked straight through it
# while GitHub-flavoured Markdown renderers autolink both. REQ-5 needs the
# stricter job, so it gets its own pattern rather than stretching that one.
#
# Deliberately aggressive, with a known ceiling: this also removes a bare
# "voys.nl" written as ordinary prose. On a branch that by definition has zero
# retrieved sources the rule is simply "no links", and mangling one sentence
# beats rendering an invented support URL on a public help page. The SUPPORT
# profile already forbids the model from writing URLs at all, so reaching this
# at all is the exception.
_LINKLIKE_RE = re.compile(
    r"(?:(?:https?|ftp)://|www\.)\S+"
    r"|\b[\w-]+(?:\.[\w-]+)+\.[a-z]{2,}(?:/\S*)?"
    r"|\b[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}/\S*",
    re.IGNORECASE,
)


def _answer_without_retrieved_sources(text: str, citation_chunks: list[dict] | None = None) -> str:
    """Render an answer that has no retrieved sources behind it.

    SPEC-RAG-ANSWER-TIERS-001 REQ-5, the invariant for every branch that returns
    the model's words without the composer: **a link may only reach a visitor
    when it came from retrieval and survived the source selector.** Both
    non-strict branches bypass ``compose_answer_with_trusted_sources``, and that
    composer is the only MECHANICAL place where an output URL is checked against
    the allowed set. The SUPPORT profile also bans URLs, but that is a prompt,
    and a prompt is a request rather than a guarantee.

    Measured 2026-09-15: a consented broad-mode answer carrying
    ``https://evil.example.com/phish`` reached the visitor untouched, and it had
    been able to since broad mode shipped. A model that invents a plausible
    support URL on a public help page is the failure this closes.

    One function so the invariant has one home; a future branch that returns
    model text without sources calls this or it is a defect.
    """
    # Evidence labels only come off when the helper is told which ids exist —
    # without them it deliberately leaves "E1" alone, because in ordinary prose
    # that is just a word. Retrieval still runs on these branches and still
    # injects the labels into the prompt, so the model can echo them; reproduced
    # 2026-09-15 with "Evidence E1" and "(E1)" reaching the visitor.
    evidence_ids = {
        chunk_id
        for chunk in evidence_chunks_from_chunks(citation_chunks or [])
        if (chunk_id := getattr(chunk, "evidence_id", None))
    }
    cleaned = strip_model_citation_artifacts(text, evidence_ids=evidence_ids or None)
    return _LINKLIKE_RE.sub("", cleaned).strip()


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
    """The part of a live answer the caller has not seen yet, cleaned.

    A live turn streams the model's words up to any link or URL, which the
    guard holds back until the end. The rest must come from the composed answer
    so a model-written link is cleaned like on a held turn. The composer may
    normalise whitespace, so the cut tolerates that; if it changed text the
    caller already saw, the answer cannot be aligned and replaying it would
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


def _compose_backend_managed_answer(
    text: str,
    trusted_sources: list[dict[str, Any]] | None,
    citation_chunks: list[dict] | None,
    user_query: str,
    web_chunks: list[dict] | None = None,
    web_query: str | None = None,
    helpdesk: bool = False,
    broad: bool = False,
    force_escalation: bool = False,
    *,
    response_language: str | None,
    suggest_open_mode: bool = False,
) -> tuple[str, list[dict], dict[str, Any]]:
    """Compose the answer with KB and (optionally) web sources as separate tiers.

    KB and web are kept in distinct candidate sets so the private knowledge base
    and the open web never share a trust tier: each goes through the citation
    firewall independently, and the merged source list tags every entry with its
    ``origin`` (``"kb"`` or ``"web"``). The no-citable-sources refusal only fires
    when BOTH tiers come up empty, so a web-grounded answer is not stripped just
    because the knowledge base had nothing.

    Web sources are validated against ``web_query`` (the concise query they were
    actually retrieved for) — NOT ``user_query``, which on the support route is
    the long KB-retrieval blob and would reject relevant web sources as
    "query_not_supported".

    ``helpdesk`` selects the customer-facing refusal wording (help articles +
    offer to reach support) instead of the internal-team "kennisbronnen" phrasing.
    Default False keeps every existing caller's refusal text identical.
    ``suggest_open_mode`` is the internal chat's variant: the Strict refusal
    points at the Open mode the employee can switch to.

    ``response_language`` is this turn's conversation-level decision, the same
    one that steers the system prompt and the client-facing ``delta.language``
    frame. It is a required keyword-only parameter on purpose: a refusal that
    disagrees with the language the rest of the turn committed to is the bug
    this parameter exists to prevent, and a caller must not be able to
    re-introduce it by omission. ``user_query`` stays the rewritten KB search
    query, the right input for citation composition and web validation
    (``query_text=``), which is why the two are separate parameters.

    A conversational turn (turn_scope "conversation") is NOT special-cased here
    any more. It used to skip the citation firewall and return the model's text
    directly, which cost a misclassified real question its sources and turned it
    into the fixed refusal (measured on 90 real Voys follow-ups on 2026-09-17:
    11 fires, at least 3 wrong, one refusing "hoe kan ik kijken of er ergens een
    doorschakeling in zit?"). Such a turn now takes the normal path: with no
    supporting article the answer judge decides, and text that states nothing
    about the organisation still reaches the visitor.

    ``broad`` marks a consented general-knowledge turn on the helpdesk widget:
    the model had the SUPPORT_BROAD profile, no article context was injected,
    and web search never runs for widget keys — so there is no evidence tier
    for the citation firewall to select from. The answer is labelled as general
    knowledge here (never by the model) and returns with empty sources. Public
    refusal decisions additionally carry ``broad_mode`` ("answer" / "offer") so
    the stream wrapper can emit the matching widget signal.

    Public helpdesk decisions can additionally carry ``escalation``
    ``{"appointment": True}`` — "this answer offers the visitor an
    appointment", so the widget can render the booking button under exactly
    that message. Two sources feed it: the canned refusal (its own text makes
    the offer) and the SUPPORT profile's machine marker on an offer the model
    wrote itself. The marker is stripped here and can never reach a visitor.
    """
    text, model_offered_appointment = strip_appointment_offer_marker(text)
    # The marker only means something on the public help-page widget; partner
    # API callers never see the SUPPORT prompt, so their path stays untouched.
    # The model's marker is corroborated against its own visible text: measured
    # 2026-09-09, one in six plain step-by-step answers carried a bare marker
    # and no offer at all, which put the button under a reply that never
    # mentions an appointment. ``force_escalation`` is the backend's own
    # decision (escalation_intent) — the visitor asked for a person or is
    # frustrated — and needs no corroboration: the button goes under this
    # answer whatever the model wrote or retrieval found.
    offered_appointment = helpdesk and (
        (model_offered_appointment and _text_offers_appointment(text)) or force_escalation
    )
    # The refusal and the broad-mode marker follow the conversation decision,
    # not a fresh identification of the visitor's last turn. Identifying one
    # turn abstains often — measured in 594de988d on ten typical short Dutch
    # widget questions it abstains on five — and an abstention renders Dutch
    # (see klai_chat_prompts._language_is_dutch for that measured rationale).
    # That combination produced the reported defect: an English conversation
    # whose latest turn is a short "Why?" answers in English, frames the widget
    # in English via delta.language, and then refuses in Dutch. The replay
    # carries the whole conversation, so it abstains far less; when it does
    # abstain the Dutch default still applies, deliberately.
    refusal_language = response_language
    if broad:
        if not text.strip():
            # The model produced nothing even with the broad profile; stay on
            # the honest refusal. No offer signal: consent already happened.
            decision: dict[str, Any] = {
                "reason": "broad_mode_no_output",
                _NO_CITABLE_SOURCES_DECISION_KEY: True,
            }
            # The helpdesk refusal itself offers an appointment, so the widget
            # gets the button even though the model wrote nothing at all.
            if helpdesk:
                decision["escalation"] = _appointment_escalation()
            return (
                _no_citable_sources_message(refusal_language, helpdesk=helpdesk, suggest_open_mode=suggest_open_mode),
                [],
                decision,
            )
        marker = broad_mode_answer_marker(refusal_language)
        decision = {"reason": "broad_mode_answer", "broad_mode": "answer"}
        if offered_appointment:
            decision["escalation"] = _appointment_escalation()
        broad_text = _answer_without_retrieved_sources(text, citation_chunks)
        if helpdesk:
            broad_text = without_unsourced_contact_details(broad_text, [])
        return f"{marker}\n\n{broad_text}", [], decision

    # A source below the score bar must share a word with the question to be
    # cited. The selection step read the passage and pointed at the sentence
    # that answers, so for its passages that word check is skipped: it dropped
    # "gespreksgegevens exporteren" for a visitor who wrote "belgegevens
    # downloaden", and the reply without a source became a refusal.
    chosen_by_selection = bool(citation_chunks) and all(chunk.get(_CHOSEN_PASSAGE_KEY) for chunk in citation_chunks)
    composed = compose_answer_with_trusted_sources(
        text,
        trusted_sources or [],
        query_text=None if chosen_by_selection else user_query,
        evidence_chunks=citation_chunks or [],
        # The help widget may repeat an address only when it stands in the
        # passages the writer read (an API endpoint, the web phone address).
        allowed_urls=urls_in_passages(citation_chunks or []) if helpdesk else None,
    )
    if helpdesk and composed.content:
        gated = without_unsourced_contact_details(composed.content, citation_chunks)
        if gated != composed.content:
            composed = ComposedCitations(content=gated, sources=composed.sources, decision=composed.decision)
    if not composed.content:
        decision = dict(composed.decision)
        decision[_NO_CITABLE_SOURCES_DECISION_KEY] = True
        if helpdesk:
            decision.update(_helpdesk_refusal_offers(citation_chunks))
        return (
            _no_citable_sources_message(refusal_language, helpdesk=helpdesk, suggest_open_mode=suggest_open_mode),
            [],
            decision,
        )

    kb_sources = [{**source, "origin": "kb"} for source in composed.sources]
    web_sources: list[dict] = []
    decision = dict(composed.decision)
    if web_chunks:
        # Run the same firewall over the cleaned answer with web evidence only,
        # validated against the web query the results were retrieved for.
        web_composed = compose_answer_with_trusted_sources(
            composed.content,
            [],
            query_text=web_query or user_query,
            evidence_chunks=web_chunks,
        )
        kb_url_keys = {source_url_key(s.get("url")) for s in kb_sources if s.get("url")}
        web_sources = [
            {**source, "origin": "web"}
            for source in web_composed.sources
            # Drop a web source that duplicates a KB source URL: the knowledge
            # base is the higher-trust tier, so the KB entry wins.
            if source_url_key(source.get("url")) not in kb_url_keys
        ]
        decision["web"] = web_composed.decision

    sources = _renumber_sources(kb_sources + web_sources)
    if not sources:
        decision[_NO_CITABLE_SOURCES_DECISION_KEY] = True
        if helpdesk:
            decision.update(_helpdesk_refusal_offers(citation_chunks))
        return (
            _no_citable_sources_message(refusal_language, helpdesk=helpdesk, suggest_open_mode=suggest_open_mode),
            [],
            decision,
        )
    if offered_appointment:
        decision["escalation"] = _appointment_escalation()
    return composed.content, sources, decision


# Background work that outlives the reply: an Open turn's grounding check.
# Held here so the event loop keeps a strong reference until it finishes.
_pending_turn_tasks: set[asyncio.Task] = set()


def _grounding_signals(sink: dict[str, Any] | None, grounding: GroundingCheck | None, *, keep_statements: bool) -> None:
    """Put the statement-level check's outcome into the audit sink.

    ``keep_statements`` stores the flagged statements themselves, only where
    the turn's text is stored anyway (``widget_messages``); the internal record
    holds signals and no text, because LibreChat keeps the conversation.
    """
    if sink is None:
        return
    if grounding is None:
        sink.setdefault("judge_failed", []).append("grounding")
        return
    sink["unsupported"] = len(grounding.unsupported)
    if keep_statements:
        # The draft's flagged statements, not only their count: nothing
        # changes a reply after writing, and without the text a measurement
        # cannot tell whether a flag was right. Capped; it is
        # text the stored reply already holds. Not shown in the activity view yet.
        sink["unsupported_statements"] = [item.statement[:300] for item in grounding.unsupported[:5]]


def _check_answer_later(
    *,
    question: str,
    draft: str,
    articles: list[tuple[str, str]],
    settings: Settings,
    org_id: int | str | None,
    delegated_org_id: str | None,
    answer_signals: dict[str, Any],
    keep_statements: bool = False,
) -> asyncio.Task[None]:
    """Check an answer after it went out, and only record what the check found.

    Product decision (2026-09-24): Open is checked, but not repaired until the
    check has been measured on Open answers, which may use general knowledge
    by design and so carry statements no article supports. A widget reply
    written from chosen passages is checked the same way (plan §8, step M).
    Nobody waits for this; the per-turn record does.
    """

    async def check() -> None:
        grounding = await check_grounding(
            question=question,
            draft=draft,
            articles=articles,
            settings=settings,
            org_id=org_id,
            timeout_seconds=grounding_budget("internal"),
            delegated_org_id=delegated_org_id,
        )
        _grounding_signals(answer_signals, grounding, keep_statements=keep_statements)
        if grounding is not None:
            answer_signals["grounding"] = _grounding_label(grounding)

    task = asyncio.create_task(check())
    _pending_turn_tasks.add(task)
    task.add_done_callback(_pending_turn_tasks.discard)
    return task


async def _finish_live_answer(
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
    profile: ChatProfile,
    knowledge_turn: KnowledgeTurn | None = None,
    delegated_org_id: str | None = None,
) -> tuple[str, list[dict], dict[str, Any]]:
    """Settle an internal Open or general turn after composition: record the one decision, start the check.

    These turns stream live, because an employee reads the tokens as they
    arrive: an Open answer may use general knowledge by design, so it is shown
    as written and checked afterwards for the record only; a general turn is
    not checked at all, it had no articles. The internal Strict turn is held
    and judged in ``widget_write.write_reply``, like the help widget's.

    An empty result (the model wrote nothing) keeps the composer's refusal.
    """
    answer_mode = knowledge_turn.answer_mode if knowledge_turn is not None else profile.kb_mode
    draft_text, _ = strip_appointment_offer_marker(draft)
    safe_text = _answer_without_retrieved_sources(draft_text, citation_chunks)
    if not safe_text:
        return content, sources, decision
    articles = [(_chunk_source_title(chunk), str(chunk.get("text") or "")) for chunk in citation_chunks or []]
    outcome = decide_answer(
        kb_mode=answer_mode,
        has_sources=bool(sources),
        escalation=False,
        conversational=False,
        judgement=None,
    )
    logger.info("partner_chat_answer_judge", org_id=org_id, decision=outcome, kb_mode=answer_mode)
    if answer_signals is not None:
        answer_signals["decision"] = outcome
    if answer_mode == "open" and articles and knowledge_turn is not None and answer_signals is not None:
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
    # names) are the model's or the user's own and stay as the hook left them.
    uncited = draft_text if answer_mode == "general" else safe_text
    return (content, sources, decision) if sources else (uncited, [], {"reason": outcome})


def _grounding_label(grounding: GroundingCheck) -> str:
    if grounding.unsupported:
        return "some_not_in_articles"
    return "all_in_articles" if grounding.statements else "no_company_statements"


def _log_turn_timing(
    turn_timing: dict[str, float] | None,
    *,
    org_id: int | str | None,
    generation_ms: int,
    answer_judge_ms: int,
    answer_signals: dict[str, Any] | None = None,
) -> None:
    """REQ-4: one line per judged turn with where its wall-clock went, also kept in the record.

    ``turn_timing`` comes from the route: ``started_at`` (perf_counter at the
    start of the request), ``retrieval_ms`` and ``case_ms``, which ran
    concurrently, so their sum is not a duration.
    """
    if turn_timing is None:
        return
    timings = {
        "retrieval_ms": turn_timing.get("retrieval_ms"),
        "case_ms": turn_timing.get("case_ms"),
        "generation_ms": generation_ms,
        "checks_ms": answer_judge_ms,
        "total_ms": _elapsed_ms(turn_timing["started_at"]),
    }
    logger.info(
        "partner_chat_turn_timing",
        org_id=org_id,
        retrieval_ms=timings["retrieval_ms"],
        case_ms=timings["case_ms"],
        generation_ms=generation_ms,
        answer_judge_ms=answer_judge_ms,
        total_ms=timings["total_ms"],
    )
    if answer_signals is not None:
        answer_signals["timings"] = timings


def _elapsed_ms(started_at: float) -> int:
    return round((time.perf_counter() - started_at) * 1000)


def _count_citation_rescues(decision: dict[str, Any]) -> int:
    """Count applied rescues across the separate KB and web trust tiers."""
    count = sum(
        1 for entry in decision.get("selected", []) if isinstance(entry, dict) and entry.get("reason") == "rescued"
    )
    web_decision = decision.get("web")
    if isinstance(web_decision, dict):
        count += _count_citation_rescues(web_decision)
    return count


def _log_citation_rescues(decision: dict[str, Any], *, org_id: int | str | None) -> None:
    """Emit one dashboardable event when active rescue exposed extra sources."""
    rescued_sources = _count_citation_rescues(decision)
    if rescued_sources:
        logger.info(
            "citation_rescue_applied",
            org_id=org_id,
            rescued_sources=rescued_sources,
        )


def _renumber_sources(sources: list[dict]) -> list[dict]:
    """Give a merged KB+web source list a single contiguous label sequence."""
    for index, source in enumerate(sources, start=1):
        source["label"] = str(index)
    return sources


async def _chat_completion_streaming_with_composed_citations(  # noqa: C901 - live streaming + tool_calls branching alongside composition
    *,
    augmented_messages: list[dict],
    model: str,
    temperature: float,
    settings: Settings,
    org_id: int | str | None,
    user_query: str,
    trusted_sources: list[dict[str, Any]] | None,
    citation_chunks: list[dict] | None,
    response_language: str | None,
    web_chunks: list[dict] | None = None,
    web_query: str | None = None,
    emit_sources: bool = True,
    answer_signals: dict[str, Any] | None = None,
    conversation: list[dict] | None = None,
    turn_timing: dict[str, float] | None = None,
    profile: ChatProfile = ChatProfile(surface="widget"),
    tools: list[dict] | None = None,
    tool_choice: Any | None = None,
    delegated_org_id: str | None = None,
    knowledge_turn: KnowledgeTurn | None = None,
    pasted_correspondence: bool = False,
) -> AsyncGenerator[bytes]:
    """Stream the internal chat's Open or general turn live, then compose its citations.

    The content is streamed incrementally through :func:`_pop_live_stream_text`,
    because an employee reads the tokens as they arrive. Composition still runs
    once on the full text at the end for sources and signals, but its content
    is not re-sent: the caller already saw it live. An upstream failure before
    the first byte (LiteLLM mid-restart / 5xx) is surfaced as a clean SSE error
    frame.

    The widget, the partner key and the internal Strict turn are not written
    here: widget_write.write_reply composes and holds them, and
    widget_reply.reply_frames renders them.

    ``answer_signals`` is the caller's audit sink (see :func:`_fill_answer_signals`)
    — filled once the answer is composed, and never part of any frame.

    A ``tool_calls`` delta is forwarded unbuffered the moment it arrives,
    regardless of what is streamed — see :func:`_sse_tool_calls_delta`. It never
    enters ``raw_text_parts``, so it can never reach the citation composer or
    the grounding check.

    With citable sources, a text footer
    (:func:`app.services.answer_footer.render_answer_footer`) is appended as
    the LAST render step, after composition has settled ``sources``. The
    content already streamed with the model's own raw markers, so the footer
    follows as one extra content delta after the live text (see the emission
    below). ``knowledge_turn`` carries the sub-questions for that footer and
    the mode the answer is decided under.

    ``pasted_correspondence`` strips the answer-contract markers the
    correspondence prompt asks for, from the live stream as it goes and from
    the text every later step reads.
    """
    raw_text_parts: list[str] = []
    sub_queries = knowledge_turn.sub_queries if knowledge_turn is not None else []
    # The page-context message is prepended, so the last user turn in
    # augmented_messages is the human's own words, never the rewritten
    # retrieval query. The canned source refusal follows the conversation
    # decision (response_language above); this stays the input for the
    # output-safety refusal, which has no conversation decision to follow.
    visitor_query = _last_user_message(augmented_messages) or ""
    chat_url = f"{settings.litellm_base_url}/v1/chat/completions"
    generation_started = time.perf_counter()
    live_buffer = ""
    live_emitted: list[str] = []
    called_tool = False
    marker_buffer = ""
    request_json = _llm_request_body(
        model,
        augmented_messages,
        temperature,
        stream=True,
        tools=tools,
        tool_choice=tool_choice,
        delegated_org_id=delegated_org_id,
    )
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST",
                chat_url,
                json=request_json,
                headers={
                    "Authorization": f"Bearer {settings.litellm_master_key}",
                    **get_trace_headers(),
                },
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[6:].strip()
                    if not payload:
                        continue
                    if payload == "[DONE]":
                        break
                    try:
                        evt: dict[str, Any] = json.loads(payload)
                    except json.JSONDecodeError:
                        logger.debug("partner_chat_sse_parse_skipped", exc_info=True)
                        continue
                    delta = (evt.get("choices") or [{}])[0].get("delta") or {}
                    if tool_calls := delta.get("tool_calls"):
                        called_tool = True
                        yield _sse_tool_calls_delta(tool_calls)
                    text = delta.get("content")
                    if isinstance(text, str) and text:
                        raw_text_parts.append(text)
                        if pasted_correspondence:
                            text, marker_buffer = pop_answer_contract_stream_text(marker_buffer + text, final=False)
                        live_buffer += text
                        safe_text, live_buffer = _pop_live_stream_text(live_buffer)
                        if safe_text:
                            live_emitted.append(safe_text)
                            yield _sse_content_delta(safe_text)
    except httpx.TransportError:
        logger.warning("partner_chat_upstream_unreachable", org_id=org_id, target=chat_url, exc_info=True)
        yield _sse_error_frame("Chat service unavailable")
        yield b"data: [DONE]\n\n"
        return
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "partner_chat_upstream_error",
            org_id=org_id,
            status_code=exc.response.status_code,
            exc_info=True,
        )
        yield _sse_error_frame("Chat service error")
        yield b"data: [DONE]\n\n"
        return

    generation_ms = _elapsed_ms(generation_started)
    draft = "".join(raw_text_parts)
    if called_tool and not draft.strip():
        # A turn that only calls a tool is not an answer yet: the client runs
        # the tool and sends its result back. Composing it would append the
        # no-sources refusal to the tool call. OpenAI clients read the
        # tool_calls finish reason as "run the tools now".
        yield b'data: {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}\n\n'
        yield b"data: [DONE]\n\n"
        return
    if pasted_correspondence:
        draft = strip_answer_contract_markers(draft)
    content, sources, decision = _compose_backend_managed_answer(
        draft,
        trusted_sources,
        citation_chunks,
        user_query,
        web_chunks,
        web_query,
        response_language=response_language,
        suggest_open_mode=True,
    )
    judge_started = time.perf_counter()
    if safety_reason := output_safety_violation(draft):
        logger.warning(
            "partner_chat_output_blocked",
            org_id=org_id,
            stage="stream_composed_output",
            reason=safety_reason,
        )
        # The employee's own words, never user_query (the KB-rewritten search
        # query when the caller passes one).
        content = safety_refusal_message(visitor_query)
        sources = []
        decision = {"reason": safety_reason, "output_blocked": True}
    else:
        content, sources, decision = await _finish_live_answer(
            content,
            sources,
            decision,
            draft=draft,
            messages=conversation or [],
            citation_chunks=citation_chunks,
            settings=settings,
            org_id=org_id,
            answer_signals=answer_signals,
            profile=profile,
            knowledge_turn=knowledge_turn,
            delegated_org_id=delegated_org_id,
        )
    _log_turn_timing(
        turn_timing,
        org_id=org_id,
        generation_ms=generation_ms,
        answer_judge_ms=_elapsed_ms(judge_started),
        answer_signals=answer_signals,
    )
    # Consumed before the decision is logged so that event keeps its exact
    # payload: the marker only travels to the audit sink (see _fill_answer_signals).
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
        chunks=citation_chunks,
        sources=sources,
        model=model,
        query_text=visitor_query,
        reply=content,
    )
    # Deliberately kept separate as the last render step
    # (chat-quality-history-and-plan.md §7.2): the sources frame emitted
    # below is structured data, but LibreChat renders only text, so the footer
    # carries the same finalised ``sources`` as text.
    footer_text = ""
    if profile.surface == "internal" and emit_sources:
        footer_text = render_answer_footer(
            sources=sources,
            kb_mode=profile.kb_mode,
            chunks_injected=len(citation_chunks or []),
            sub_queries=sub_queries,
            language=response_language,
            general_knowledge=decision.get("reason") == "general_knowledge",
        )
    # What still has to go out: only the unseen tail (plus the footer), since
    # the rest was streamed live.
    if decision.pop("output_blocked", False):
        # The streamed part cannot be taken back; the rest of the draft is
        # withheld and the refusal follows it.
        outgoing = f"\n\n{content}"
    else:
        outgoing = _unstreamed_tail(content, "".join(live_emitted), draft, citation_chunks)
        if footer_text:
            outgoing = f"{outgoing.rstrip()}\n\n{footer_text}"
    if citation_chunks:
        yield _sse_activity_delta(
            [
                {
                    "step": "knowledge_retrieved",
                    "label": "Kennisbank geraadpleegd",
                    "detail": f"{len(citation_chunks)} passages gevonden",
                    "count": len(citation_chunks),
                }
            ]
        )
    if web_chunks:
        yield _sse_activity_delta(
            [
                {
                    "step": "web_searched",
                    "label": "Web doorzocht",
                    "detail": f"{len(web_chunks)} resultaten gevonden",
                    "count": len(web_chunks),
                }
            ]
        )
    if not sources or not emit_sources:
        if outgoing:
            yield _sse_content_delta(outgoing)
        yield b"data: [DONE]\n\n"
        _emit_language_correctness_log(
            org_id=org_id,
            query=user_query,
            response_text=content,
            chunks_injected=len(citation_chunks or []),
        )
        return

    yield _sse_sources_delta(sources)
    yield _sse_activity_delta(
        [
            {
                "step": "sources_attached",
                "label": "Bronnen gekoppeld",
                "detail": f"{len(sources)} bronnen beschikbaar",
                "count": len(sources),
            }
        ]
    )
    if outgoing:
        yield _sse_content_delta(outgoing)
    yield b"data: [DONE]\n\n"
    _emit_language_correctness_log(
        org_id=org_id,
        query=user_query,
        response_text=content,
        chunks_injected=len(citation_chunks or []),
    )


def _extract_completion_text(body: dict) -> str:
    """Pull the assistant text out of a LiteLLM /v1/chat/completions
    response body. Returns "" if the body shape is unexpected — callers
    use this only for observability, never for user-visible behaviour.
    """
    try:
        choice = body["choices"][0]
        message = choice.get("message") or {}
        text = message.get("content")
        return text if isinstance(text, str) else ""
    except (KeyError, IndexError, TypeError):
        return ""


async def chat_completion_non_streaming(
    messages: list[dict],
    model: str,
    temperature: float,
    system_prompt: str,
    settings: Settings,
    *,
    org_id: int | str | None = None,
    citation_chunks: list[dict] | None = None,
    web_chunks: list[dict] | None = None,
    web_query: str | None = None,
    trusted_sources: list[dict[str, Any]] | None = None,
    source_query: str | None = None,
    answer_signals: dict[str, Any] | None = None,
    turn_timing: dict[str, float] | None = None,
    profile: ChatProfile,
    tools: list[dict] | None = None,
    tool_choice: Any | None = None,
    delegated_org_id: str | None = None,
    knowledge_turn: KnowledgeTurn | None = None,
    pasted_correspondence: bool = False,
) -> dict:
    """Forward to LiteLLM and return complete response as dict.

    POST to litellm with stream=false. Emits the
    ``chat_synthesis_complete`` log event before returning so
    cross-lingual correctness is observable on every call (REQ-07).

    ``answer_signals`` is the caller's audit sink (see :func:`_fill_answer_signals`);
    it is filled once the answer is composed and never added to the returned body.

    ``message.language`` carries the same per-turn :func:`resolve_conversation_language`
    decision that steered the system prompt (see ``response_language`` above);
    the key is omitted entirely when the decision is ``None``, which it is on
    pasted correspondence (see :func:`_response_language`).
    """
    response_language = _response_language(messages, pasted_correspondence=pasted_correspondence)
    sub_queries = knowledge_turn.sub_queries if knowledge_turn is not None else []
    augmented_messages = _augment_messages_with_system_prompt(
        messages, system_prompt, response_language=response_language, profile=profile
    )

    litellm_url = settings.litellm_base_url
    chat_url = f"{litellm_url}/v1/chat/completions"

    request_json = _llm_request_body(
        model,
        augmented_messages,
        temperature,
        stream=False,
        tools=tools,
        tool_choice=tool_choice,
        delegated_org_id=delegated_org_id,
    )

    generation_started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                chat_url,
                json=request_json,
                headers={
                    "Authorization": f"Bearer {settings.litellm_master_key}",
                    **get_trace_headers(),
                },
            )
            resp.raise_for_status()
            body = resp.json()
    except httpx.TransportError as exc:
        # Upstream unreachable or slow (e.g. LiteLLM mid-restart during a
        # deploy). Return a clean 502 instead of a bare 500 so callers can tell
        # it apart from a request error and retry. Transport errors have no
        # .response, so log the target URL per the python error-handling rules.
        logger.warning(
            "partner_chat_upstream_unreachable",
            org_id=org_id,
            target=chat_url,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": {"type": "upstream_error", "message": "Chat service unavailable"}},
        ) from exc
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "partner_chat_upstream_error",
            org_id=org_id,
            status_code=exc.response.status_code,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": {"type": "upstream_error", "message": "Chat service error"}},
        ) from exc

    generation_ms = _elapsed_ms(generation_started)
    answer_judge_ms = 0
    # Kept for citation composition only (see the composer's user_query):
    # source_query is the KB-rewritten search query and must never reach the
    # refusal language.
    composer_query = source_query or _last_user_message(messages) or ""
    # augmented_messages prepends page context, so this IS the human's words.
    # Used by the output-safety refusal, which identifies this one turn; the
    # source refusal and the broad marker follow response_language instead.
    visitor_query = _last_user_message(augmented_messages) or ""
    for choice in body.get("choices") or []:
        message = choice.get("message") if isinstance(choice, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        # A message that only calls a tool is not an answer to compose.
        if (
            isinstance(message, dict)
            and isinstance(content, str)
            and not (message.get("tool_calls") and not content.strip())
        ):
            # Same per-turn decision as the system prompt's language reminder
            # (see response_language above) — never a second, independent guess.
            if response_language is not None:
                message["language"] = response_language
            if pasted_correspondence:
                content = strip_answer_contract_markers(content)
            if safety_reason := output_safety_violation(content):
                logger.warning(
                    "partner_chat_output_blocked",
                    org_id=org_id,
                    stage="non_streaming_markers_output",
                    reason=safety_reason,
                )
                message["content"] = safety_refusal_message(visitor_query)
                message["sources"] = []
                # Same record as the streaming pad writes for a blocked turn;
                # skipping it left only the band retrieval had already stored.
                _fill_answer_signals(
                    answer_signals,
                    decision={"reason": safety_reason},
                    refused=False,
                    chunks=citation_chunks,
                    sources=[],
                    model=model,
                    query_text=visitor_query,
                )
                continue
            rendered_content, sources, decision = _compose_backend_managed_answer(
                content,
                trusted_sources,
                citation_chunks,
                composer_query,
                web_chunks,
                web_query,
                response_language=response_language,
                suggest_open_mode=True,
            )
            judge_started = time.perf_counter()
            rendered_content, sources, decision = await _finish_live_answer(
                rendered_content,
                sources,
                decision,
                draft=content,
                messages=messages,
                citation_chunks=citation_chunks,
                settings=settings,
                org_id=org_id,
                answer_signals=answer_signals,
                profile=profile,
                knowledge_turn=knowledge_turn,
                delegated_org_id=delegated_org_id,
            )
            answer_judge_ms = _elapsed_ms(judge_started)
            # Popped before the log so that event keeps its exact payload;
            # the marker only travels to the audit sink.
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
                chunks=citation_chunks,
                sources=sources,
                model=model,
                query_text=visitor_query,
                reply=rendered_content,
            )
            message["content"] = rendered_content
            message["sources"] = sources
            # Deliberately kept separate as the last render step
            # (chat-quality-history-and-plan.md §7.2): see the matching
            # comment on the streaming path.
            if profile.surface == "internal":
                footer_text = render_answer_footer(
                    sources=sources,
                    kb_mode=profile.kb_mode,
                    chunks_injected=len(citation_chunks or []),
                    sub_queries=sub_queries,
                    language=response_language,
                    general_knowledge=decision.get("reason") == "general_knowledge",
                )
                if footer_text:
                    message["content"] = f"{rendered_content.rstrip()}\n\n{footer_text}"
    _log_turn_timing(
        turn_timing,
        org_id=org_id,
        generation_ms=generation_ms,
        answer_judge_ms=answer_judge_ms,
        answer_signals=answer_signals,
    )

    # Passive language-correctness telemetry (SPEC-RAG-MULTILINGUAL-CHAT-001 REQ-07).
    user_query = _last_user_message(messages) or ""
    response_text = _extract_completion_text(body)
    _emit_language_correctness_log(
        org_id=org_id,
        query=user_query,
        response_text=response_text,
        chunks_injected=len(citation_chunks or []),
    )
    return body


async def chat_completion_streaming(
    messages: list[dict],
    model: str,
    temperature: float,
    system_prompt: str,
    settings: Settings,
    *,
    org_id: int | str | None = None,
    citation_chunks: list[dict] | None = None,
    web_chunks: list[dict] | None = None,
    web_query: str | None = None,
    trusted_sources: list[dict[str, Any]] | None = None,
    source_query: str | None = None,
    emit_sources: bool = True,
    answer_signals: dict[str, Any] | None = None,
    turn_timing: dict[str, float] | None = None,
    profile: ChatProfile,
    tools: list[dict] | None = None,
    tool_choice: Any | None = None,
    delegated_org_id: str | None = None,
    knowledge_turn: KnowledgeTurn | None = None,
    pasted_correspondence: bool = False,
) -> AsyncGenerator[bytes]:
    """Stream LiteLLM SSE response with backend-managed KB citations.

    The internal chat's live leg (an Open or general turn): it buffers the model
    output, runs the deterministic citation composer, then emits only sources
    that support the final answer.

    ``answer_signals`` is the caller's audit sink, filled once the answer is
    composed.

    Every turn (including a safety refusal or a broad-mode answer) opens with
    one ``delta.language`` frame carrying the same
    :func:`resolve_conversation_language` decision that steered the
    system prompt below — never a second, independently-computed guess. The
    frame is omitted entirely when the decision is ``None``, which it is on
    pasted correspondence (see :func:`_response_language`).

    An internal Strict turn is not written here: ``widget_write.write_reply``
    holds and judges it, and drops a web-search tool before the call.
    """
    response_language = _response_language(messages, pasted_correspondence=pasted_correspondence)
    augmented_messages = _augment_messages_with_system_prompt(
        messages, system_prompt, response_language=response_language, profile=profile
    )
    user_query = source_query or _last_user_message(messages) or ""
    if response_language is not None:
        yield _sse_language_delta(response_language)
    async for chunk in _chat_completion_streaming_with_composed_citations(
        augmented_messages=augmented_messages,
        model=model,
        temperature=temperature,
        settings=settings,
        org_id=org_id,
        user_query=user_query,
        trusted_sources=trusted_sources,
        citation_chunks=citation_chunks,
        response_language=response_language,
        web_chunks=web_chunks,
        web_query=web_query,
        emit_sources=emit_sources,
        answer_signals=answer_signals,
        conversation=messages,
        turn_timing=turn_timing,
        profile=profile,
        tools=tools,
        tool_choice=tool_choice,
        delegated_org_id=delegated_org_id,
        knowledge_turn=knowledge_turn,
        pasted_correspondence=pasted_correspondence,
    ):
        yield chunk
