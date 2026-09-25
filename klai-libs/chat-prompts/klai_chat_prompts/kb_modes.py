"""Prompt text of the internal chat's knowledge-base modes (Strict, Open,
general) and of multi-part questions.

Moved from the LiteLLM hook (one-chat-pipeline slice 4, klai_kb_answer_policy,
klai_kb_context_prompt, klai_kb_confidence_policy, klai_kb_system_prompt) so
this library stays the one home of chat prompt text. A submodule, not
``__init__``: the hook's vendored copy mirrors ``__init__`` only and keeps its
own copy of these blocks until slice 9 removes the hook.
"""

from __future__ import annotations

from klai_chat_prompts import _language_is_dutch

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

# The internal chat's wording of the widget's weak-source rule (portal
# ``clarify_decision.WEAK_SOURCES_ADDENDUM``): the same trigger, every retrieved
# source below the gap threshold, and the same instruction, without the
# widget's appointment button. It replaces the text that fired on the
# retrieval confidence band and ended in a clarifying question: the band does
# not predict whether an answer is right, and a question asked inside the
# answer almost never came (docs/architecture/chat-quality-history-and-plan.md
# §7.5). English on purpose, like every other instruction block here
# (SPEC-RAG-MULTILINGUAL-CHAT-001 REQ-10).
_WEAK_SOURCES_RULE = (
    "\n\n[This turn] Retrieval found nothing that clearly matches: every knowledge-base source above "
    "scored below the bar. Use them only if one of them literally answers what the user asked. "
)


def weak_sources_notice(kb_narrow: bool) -> str:
    if kb_narrow:
        return _WEAK_SOURCES_RULE + (
            "If none does, say plainly in the user's language that you cannot find this in the knowledge "
            "base, and give no steps and no workaround from a neighbouring source."
        )
    return _WEAK_SOURCES_RULE + (
        "If none does, build no answer from a neighbouring source: say in the user's language that the "
        "knowledge base does not cover this, and answer from general knowledge only where you can do so "
        "reliably, presented as general knowledge."
    )


# Multi-part user messages get one retrieval pass over the whole message, so
# aggregate confidence and any-token overlap say nothing about per-question
# coverage. This guard makes the model judge coverage per question instead of
# interpolating over the gaps (2026-08-17 webhook-FAQ incident: 11
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
    if _language_is_dutch(language):
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
