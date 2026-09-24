"""Prompt text for the internal chat's modes and for multi-part questions.

Moved from the LiteLLM hook (one-chat-pipeline slice 4): the mode prefixes of
``klai_kb_answer_policy``, the context block of ``klai_kb_context_prompt``,
the guard texts of ``klai_kb_confidence_policy`` and the template block of
``klai_kb_system_prompt``. The prompt text itself lives in
``klai_chat_prompts`` (foundations in ``__init__``, mode blocks in
``kb_modes``); this module renders evidence and composes the system message.

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
from klai_chat_prompts.kb_modes import (
    KB_ANSWER_FORMAT_INSTRUCTION,
    MULTI_QUESTION_FANOUT_GUARD_TEXT,
    MULTI_QUESTION_GUARD_TEXT,
    USER_PROVIDED_CONTENT_SCOPE,
    WEB_SEARCH_CAPABILITIES_BLOCK,
    kb_chunks_present_header,
    kb_retrieval_failure_notice,
    kb_zero_chunks_notice,
)
from klai_citations import normalise_source_url, render_evidence_context

from app.services.pasted_correspondence import PASTED_CORRESPONDENCE_SCOPE

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
