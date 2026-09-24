"""Prompt text for the internal chat's modes and for multi-part questions.

Moved from the LiteLLM hook (one-chat-pipeline slice 4): the mode prefixes of
``klai_kb_answer_policy``, the context block of ``klai_kb_context_prompt``,
the guard texts of ``klai_kb_confidence_policy`` and the template block of
``klai_kb_system_prompt``. The foundation prompts themselves
(GROUNDED/OPEN_KB/GENERAL/META) stay in ``klai_chat_prompts``; only text
that lived in the hook alone moved here. The hook keeps its copy until slice
9 removes it, so a wording change here must not be mirrored there.

Strict and Open are the internal surface's modes (plan §7.2 "Modi"). The
multi-part-question guards and the per-question evidence layout are shared by
every surface, because the sub-question fan-out is.
"""

from __future__ import annotations

from typing import Any, Literal

from klai_chat_prompts import (
    GENERAL_CHAT_SYSTEM_PROMPT,
    GROUNDED_CHAT_SYSTEM_PROMPT,
    KB_CONTEXT_LANGUAGE_REMINDER,
    META_CHAT_SYSTEM_PROMPT,
    OPEN_KB_CHAT_SYSTEM_PROMPT,
)
from klai_chat_prompts import _language_is_dutch as language_is_dutch
from klai_citations import normalise_source_url, render_evidence_context

from app.services.pasted_correspondence import PASTED_CORRESPONDENCE_SCOPE

# A user-provided attachment is the user's OWN input, not a knowledge-base
# source. It must always be usable standalone content, in every mode and on
# every branch: a screenshot held up against the KB is a legitimate Strict use
# case. Strict/Open governs KB-grounding and general-knowledge fallback, NOT
# whether the model may look at what the user attached.
USER_PROVIDED_CONTENT_SCOPE = (
    "[User-provided content]\n"
    "Any images, screenshots, files, or text the user attached or pasted in "
    "this conversation, and the visible conversation itself, are the user's "
    "own input. Always inspect and use them to understand and answer the "
    "request. This is independent of Strict/Open mode and of whether the "
    "knowledge base returned results: even in Strict mode, and even when the "
    "knowledge base has zero or weak results, you may read and reason about "
    "what the user gave you. In Strict mode this permission only covers "
    "directly observable or user-provided information: do not add general-world "
    "explanations, organization-specific facts, prices, routes, product names, "
    "steps, or source claims unless the knowledge-base evidence below supports "
    "them. They are NOT knowledge-base sources — never cite them as numbered "
    "sources and never present their contents as knowledge-base facts. "
    "Strict/Open only controls how you use the knowledge base and whether you "
    "may add general knowledge; it never blocks the user's own attachments or "
    "visible conversation."
)

KB_ANSWER_FORMAT_INSTRUCTION = (
    "[ANSWER FORMAT — always follow this, EXCEPT where an "
    "active Klai Template (see block below) directs a "
    "different tone, structure, opening, wording, numbering, "
    "or whitespace:\n"
    "1. Default opening is a short TL;DR (2-3 sentences) of "
    "the answer. Write it as normal prose, not as a Markdown "
    "heading. Use the standard short-summary label in the "
    "SAME LANGUAGE as the user's question — NOT the language "
    "of the source documents. 'TL;DR' is universally "
    "understood and is a safe default in any language. SKIP "
    "this opening when an active template asks for a "
    "creative / narrative / story-style answer (e.g. 'Creatief') "
    "or for a fixed output form / specific opening.\n"
    "2. Do not write source lists, URLs, Markdown links, footnotes, "
    "or citation numbers. The application adds citations after "
    "generation from retrieved metadata.\n"
    "3. Do not preserve source-list step numbers when a retrieved "
    "chunk starts mid-procedure; rewrite steps into a clean sequence. "
    "This does not apply to numbering required by an active template.\n"
    "4. If needed for a clear explanation, or if the user asks for "
    "more detail, follow with an extended answer with inline "
    "explanation.\n"
    "   Be concise but complete. No walls of text — write as if you "
    "are helping a colleague.\n\n"
    "STRICT:\n"
    "- NEVER invent or write a URL. No notion.so, no portal.voys.nl, "
    "no guessed documentation paths.\n"
    "- NEVER use placeholder, example, or documentation-only domains.\n"
    "- Never use a title as URL target.\n\n"
    "IMAGES:\n"
    "- Only include image markdown if a chunk below already contains "
    "an explicit ![...](...) image tag.\n"
    "- Change NOTHING about the image URL. Copy the entire "
    "![...](https://...) tag exactly.\n"
    "- NEVER create, guess, search for, or suggest an image URL.\n"
    "- NEVER use placeholder, example, or documentation-only image URLs.\n"
    "- If the user asks for an image from the knowledge base and no "
    "explicit image tag is present in the chunks, say plainly that "
    "no knowledge-base image is available.\n"
    "- Knowledge-base images only: these image markdown rules apply "
    "only to images retrieved from knowledge-base chunks. They do "
    "not define how user-provided attachments may be used — see the "
    "[User-provided content] note above.\n"
    "- Do NOT add images in the TL;DR (section 1).]\n"
)

# English on purpose: every other instruction block in the prompt stack is
# English-wrapped (SPEC-RAG-MULTILINGUAL-CHAT-001 REQ-10). A Dutch guard here
# was the last strong language anchor before generation and pulled English
# questions into Dutch answers when the low-confidence band fired.
LOW_CONFIDENCE_INJECTION_TEXT = (
    "[Klai retrieval — low relevance]\n"
    "The retrieved KB material has a low relevance score for this "
    "question. Cite only what is literally in the chunks. Do NOT "
    "invent integration routes, product names, steps, amounts, or "
    "technical details that do not explicitly appear in the chunks. "
    "If the material does not fully cover the question, close with a "
    "clarifying question to the user — in the user's language — "
    "rather than giving a fabricated answer."
)
LOW_CONFIDENCE_OPEN_CONTEXT_TEXT = (
    "[Klai retrieval — low relevance in Open mode]\n"
    "The retrieved KB material has a low relevance score for this "
    "question. Treat the chunks as weak supplementary context. Open "
    "mode stays active: do not refuse solely because KB evidence is "
    "weak, tangential, or absent. Answer from general knowledge or "
    "visible user context when the question can be answered reliably "
    "that way. Present such parts explicitly as general knowledge or "
    "as derived from the user context, not as something that comes "
    "from the knowledge base. For organisation-specific facts, "
    "prices, routes, product names, steps, or source claims: do not "
    "invent them and say briefly that the knowledge base does not "
    "support that specific claim."
)

# Multi-part user messages get one retrieval pass over the whole message, so
# aggregate confidence and any-token overlap say nothing about per-question
# coverage. This guard makes the model judge coverage per question instead of
# interpolating over the gaps (2026-08-17 Voys webhook-FAQ incident: 11
# questions, 1 usable source, 11 confident answers).
MULTI_QUESTION_GUARD_TEXT = (
    "[Klai retrieval — multi-part question]\n"
    "The user message contains multiple questions, but retrieval ran on the "
    "message as a whole, so the chunks above may cover only SOME of the "
    "questions. Judge coverage per question: answer each question only from "
    "chunks that are about that question's topic, and for every question the "
    "chunks do not cover, say in the user's language that it is not in the "
    "knowledge base. The number of answers must equal the number of "
    "questions. Do not invent or substitute questions. Do not reuse a number "
    "or value from one question's chunks to answer a different question."
)
MULTI_QUESTION_FANOUT_GUARD_TEXT = (
    "[Klai retrieval — multi-part question, evidence grouped per question]\n"
    "The user message contains multiple questions and retrieval ran once PER "
    "question; the evidence above is grouped per question. Answer per "
    "question, using only that question's evidence group. For questions "
    "marked as having no knowledge-base evidence, say in the user's language "
    "that it is not in the knowledge base. For questions marked as "
    "retrieval-failed, say you could not check the knowledge base for that "
    "question — do NOT present that as missing knowledge. The number of "
    "answers must equal the number of questions. Do not invent or substitute "
    "questions. Do not reuse a number or value from one question's evidence "
    "to answer a different question."
)

_QUESTION_ECHO_MAX_CHARS = 150
# A pasted FAQ list with dozens of unchecked questions would otherwise render
# one marker block per question and dominate the prompt.
MAX_UNCHECKED_QUESTIONS_SHOWN = 6


def _sanitize_question_echo(text: str) -> str:
    """A sub-question is echoed into a system-role header; strip the brackets
    that could close that header early, collapse whitespace, cap the length."""
    collapsed = " ".join(text.replace("[", "").replace("]", "").split())
    return collapsed[:_QUESTION_ECHO_MAX_CHARS]


def sub_query_grouped_context(
    chunks: list[dict[str, Any]],
    sub_query_results: list[dict] | None,
    unchecked_questions: list[str] | None = None,
    *,
    include_source_urls: bool = False,
) -> str | None:
    """Evidence grouped per sub-question, with explicit coverage notes.

    ``None`` when retrieval reported no per-question results, so the
    single-query layout stays untouched. Questions without evidence and
    questions whose retrieval failed get distinct markers: the model must never
    conflate "not found" with "could not check". Questions beyond the fan-out
    cap follow with continuous numbering.
    """
    entries = [entry for entry in sub_query_results or [] if isinstance(entry, dict)]
    if not entries:
        return None

    chunks_by_index: dict[int, list[dict[str, Any]]] = {}
    ungrouped: list[dict[str, Any]] = []
    for chunk in chunks:
        index = chunk.get("sub_query_index")
        if isinstance(index, int):
            chunks_by_index.setdefault(index, []).append(chunk)
        else:
            ungrouped.append(chunk)

    blocks: list[str] = []
    for entry in entries:
        index = entry.get("index")
        header = f"[Question {index}: {_sanitize_question_echo(str(entry.get('query') or '').strip())}]"
        if entry.get("error"):
            blocks.append(
                f"{header}\n[Retrieval FAILED for this question — tell the "
                "user you could not check the knowledge base for it. Do NOT "
                "answer it and do NOT say it is not in the knowledge base.]"
            )
            continue
        if entry.get("retrieval_bypassed"):
            blocks.append(
                f"{header}\n[Retrieval was skipped for this question (the "
                "gate decided no knowledge-base lookup was needed) — answer "
                "it per the current mode's rules; do NOT say it is not in "
                "the knowledge base.]"
            )
            continue
        question_chunks = chunks_by_index.get(index) if isinstance(index, int) else None
        if question_chunks:
            block = f"{header}\n{render_evidence_context(question_chunks, include_source_urls=include_source_urls)}"
            if entry.get("confidence_band") in ("low", "unknown"):
                block += (
                    "\n[Low relevance for this question — cite only what is "
                    "literally in these chunks; do not derive or transfer "
                    "values from them.]"
                )
            blocks.append(block)
        else:
            blocks.append(
                f"{header}\n[No knowledge-base evidence found for this "
                "question — say plainly, in the user's language, that it is "
                "not in the knowledge base.]"
            )
    if unchecked_questions:
        start_index = len(entries) + 1
        shown = unchecked_questions[:MAX_UNCHECKED_QUESTIONS_SHOWN]
        for offset, question in enumerate(shown):
            blocks.append(
                f"[Question {start_index + offset}: {_sanitize_question_echo(question)}]\n"
                "[This question was NOT separately searched — do "
                "not attempt to answer it from general knowledge or from "
                "other questions' evidence. The application will inform the "
                "user separately that this question could not be checked.]"
            )
        remainder = len(unchecked_questions) - len(shown)
        if remainder > 0:
            blocks.append(
                f"[Plus {remainder} more questions were not separately "
                "searched — tell the user you could not check them all and "
                "suggest splitting the message into smaller parts.]"
            )
    if ungrouped:
        rendered = render_evidence_context(ungrouped, include_source_urls=include_source_urls)
        if rendered:
            blocks.append(f"[Additional evidence, not tied to one question]\n{rendered}")
    return "\n\n".join(blocks)


def multi_question_guard(*, multi_question: bool, sub_query_results: list[dict] | None) -> str:
    if not multi_question:
        return ""
    return MULTI_QUESTION_FANOUT_GUARD_TEXT if sub_query_results else MULTI_QUESTION_GUARD_TEXT


def build_template_instructions_block(instructions: list[dict]) -> str:
    """The org's active prompt templates as one block; "" when there are none.

    Template name and text are tenant-defined and may be in any language; the
    wrapper is English like every other instruction block.
    """
    if not instructions:
        return ""
    parts: list[str] = [
        "[Klai Templates — apply the following instructions to your answer. "
        "These instructions override the default answer format when they define "
        "a fixed structure, opening, wording, numbering, labels, fixed values, "
        "or whitespace. Preserve requested line breaks, blank lines, numbering, "
        "labels, and fixed values exactly. Do not collapse a fixed template into "
        "prose.]"
    ]
    for inst in instructions:
        name = inst.get("name") or "template"
        text = (inst.get("text") or "").strip()
        if not text:
            continue
        parts.append(f"[{name}]\n{text}")
    parts.append("[End templates]")
    return "\n\n".join(parts)


WEB_SEARCH_CAPABILITIES_BLOCK = (
    "[Klai Runtime Capabilities]\n"
    "Knowledge Base: none selected.\n"
    "Web Search: available for this turn.\n"
    "Instruction: for questions that need a live lookup, use the available "
    "Web Search tool or provided web results now. Do NOT tell the user to "
    "enable Search unless the tool call fails or no search result is returned.\n"
    "[End Klai Runtime Capabilities]"
)


def kb_retrieval_failure_notice(retrieval_failure: str) -> str:
    """Open-mode notice when retrieval-api could not be reached (Strict refuses instead)."""
    return (
        "[Klai Knowledge Base — TEMPORARILY UNAVAILABLE. Answer using "
        "your general knowledge. Begin your answer with a warning to "
        "the user, written in the language you detected from their "
        "most recent substantive message: tell them you could not "
        f"reach the knowledge base (technical reason: {retrieval_failure}), "
        "this answer is therefore not based on their own documentation, "
        "and they should refresh or try again later.]\n"
    )


def strict_kb_unavailable_message(language: object) -> str:
    """Strict-mode reply when the knowledge base could not be searched; no model runs."""
    if language_is_dutch(language):
        return (
            "De kennisbank is tijdelijk niet bereikbaar, dus ik kan dit niet "
            "betrouwbaar beantwoorden op basis van je kennisbronnen."
        )
    return (
        "The knowledge base is temporarily unavailable, so I cannot answer this reliably from your knowledge sources."
    )


def kb_zero_chunks_notice(kb_narrow: bool) -> str:
    if kb_narrow:
        return (
            "[Klai Knowledge Base — zero results for this query. "
            "Tell the user in their detected language that the "
            "answer is not in their knowledge base (e.g. "
            "'I cannot find this in the knowledge base' / "
            "'Dat staat niet in de kennisbank' / "
            "'Das steht nicht in der Wissensdatenbank'). "
            "Do not answer from general knowledge. "
            "Suggest the user rephrase the question or add "
            "documents to the knowledge base.]\n"
        )
    return (
        "[Klai Knowledge Base — zero results for this query. "
        "You may answer from your general knowledge instead. "
        "Begin your answer with a brief note in the user's "
        "detected language that nothing was found in their "
        "knowledge base (e.g. 'I couldn't find this in your "
        "knowledge base, but here is a general answer:' / "
        "'Dit staat niet in jouw kennisbank, maar hier is "
        "een algemeen antwoord:' / "
        "'Ich konnte dies nicht in Ihrer Wissensdatenbank "
        "finden, aber hier ist eine allgemeine Antwort:'), "
        "then answer normally.]\n"
    )


def kb_chunks_present_header(kb_narrow: bool) -> str:
    if kb_narrow:
        return (
            "[Klai Knowledge Base — answer strictly using only the sources "
            "below. Do not use general knowledge beyond these sources. "
            "If the answer is not present, say so plainly in the user's "
            "detected language (e.g. 'I cannot find this in the knowledge "
            "base' / 'Dat staat niet in de kennisbank' / 'Das steht nicht "
            "in der Wissensdatenbank').]\n"
        )
    return (
        "[Klai Knowledge Base — use this as supplementary context for "
        "your answer. You may complement it with your general "
        "knowledge.]\n"
    )


def _absolute_image_url(url: object, *, images_base_url: str) -> str:
    # Knowledge-base images are stored as site-relative paths; anything else
    # must be a real http(s) URL.
    value = url.strip().strip("<>") if isinstance(url, str) else ""
    if value.startswith("/"):
        return f"{images_base_url}{value}"
    return normalise_source_url(value)


def kb_context_block(
    *,
    kb_narrow: bool,
    chunks: list[dict[str, Any]],
    templates_block: str,
    images_base_url: str,
    low_confidence: bool,
    multi_question: bool,
    sub_query_results: list[dict] | None,
    unchecked_questions: list[str] | None,
) -> str:
    """The chunks-present block: mode header, answer format, templates, evidence, guards."""
    lines = [kb_chunks_present_header(kb_narrow), KB_ANSWER_FORMAT_INSTRUCTION]
    # Templates sit after ANSWER FORMAT and before the chunks, so template tone
    # and shape are the freshest formatting instruction before content.
    if templates_block:
        lines.append(templates_block)
    grouped = sub_query_grouped_context(chunks, sub_query_results, unchecked_questions)
    if grouped is not None:
        lines.append(grouped)
    elif rendered := render_evidence_context(chunks, include_source_urls=False):
        lines.append(rendered)
    for chunk in chunks:
        image_urls = [
            url
            for url in (_absolute_image_url(u, images_base_url=images_base_url) for u in chunk.get("image_urls") or [])
            if url
        ]
        for i, img_url in enumerate(image_urls, 1):
            lines.append(f"![afbeelding {i}]({img_url})")
        lines.append("")
    lines.append("[End knowledge base context]")
    if low_confidence:
        lines.append(LOW_CONFIDENCE_INJECTION_TEXT if kb_narrow else LOW_CONFIDENCE_OPEN_CONTEXT_TEXT)
    if guard := multi_question_guard(multi_question=multi_question, sub_query_results=sub_query_results):
        lines.append(guard)
    lines.append(KB_CONTEXT_LANGUAGE_REMINDER)
    return "\n".join(lines)


InternalPromptState = Literal["meta", "general", "no_retrieval", "retrieval_failure", "zero_chunks", "chunks"]


def internal_system_prompt(
    *,
    state: InternalPromptState,
    kb_narrow: bool,
    original_system: str | None,
    templates_block: str,
    pasted_correspondence: bool,
    context_block: str = "",
    retrieval_failure: str = "",
    web_search_available: bool = False,
) -> str:
    """The internal chat's system message, composed the way the hook composed it.

    The hook prepended a prefix to LibreChat's own system message, so that
    message (agent instructions, LibreChat's defaults) still closes the
    prompt. The pasted-correspondence contract sits between the two, where
    the hook's first prepend left it; a meta question never gets it, because
    the hook answered those before that prepend.
    """
    if state == "meta":
        prefix_blocks = [META_CHAT_SYSTEM_PROMPT, templates_block]
    elif state == "general":
        prefix_blocks = [
            GENERAL_CHAT_SYSTEM_PROMPT,
            USER_PROVIDED_CONTENT_SCOPE,
            WEB_SEARCH_CAPABILITIES_BLOCK if web_search_available else "",
            templates_block,
        ]
    else:
        foundation = GROUNDED_CHAT_SYSTEM_PROMPT if kb_narrow else OPEN_KB_CHAT_SYSTEM_PROMPT
        tail = {
            "no_retrieval": [templates_block],
            "retrieval_failure": [templates_block, kb_retrieval_failure_notice(retrieval_failure)],
            "zero_chunks": [templates_block, kb_zero_chunks_notice(kb_narrow)],
            # The context block already carries the templates.
            "chunks": [context_block],
        }[state]
        prefix_blocks = [foundation, USER_PROVIDED_CONTENT_SCOPE, *tail]
    parts = ["\n\n".join(block for block in prefix_blocks if block)]
    if pasted_correspondence and state != "meta":
        parts.append(PASTED_CORRESPONDENCE_SCOPE)
    if original_system:
        parts.append(original_system)
    return "\n\n".join(parts)
