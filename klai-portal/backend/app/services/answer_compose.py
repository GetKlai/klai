"""What the one writer (widget_write.write_reply) does to a written answer before anyone reads it.

The citation composer with its source firewall (``_compose_answer``), the
cleaning of a reply that has no source, the audit signals, the statement
check that runs after the reply, and the per-turn timing line. Every reply
the model writes passes here, held or streamed, on the widget, a partner key
and every internal mode.

REQ-07 (SPEC-RAG-MULTILINGUAL-CHAT-001) measures the visitor's query and the
model response with the SAME identifier that steers the prompt
(``klai_chat_prompts.language``). There is deliberately no second detector:
a second library is a second guess, not a second opinion, and it measured a
different thing than the prompt targeted.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import structlog
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
from app.services.answer_grounding import (
    GroundingCheck,
    check_grounding,
    grounding_budget,
)
from app.services.answer_judge import is_clarifying_question
from app.services.chat_safety import (
    _appointment_escalation,
    _helpdesk_refusal_offers,
    _text_offers_appointment,
    without_unsourced_contact_details,
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
from app.services.retrieval_context import _CHOSEN_PASSAGE_KEY, _top_chunk_score

logger = structlog.get_logger()

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


def _compose_answer(
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
