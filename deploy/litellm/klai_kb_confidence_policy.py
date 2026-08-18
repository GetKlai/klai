"""Low-confidence KB evidence policy for LiteLLM retrieval context.

Environment-derived constants in this module are boot-time configuration:
production imports the LiteLLM hook once per process, and runtime env toggles
take effect on process restart.
"""

from __future__ import annotations

import os
import re

from klai_citations import extract_salient_query_tokens

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
LOW_CONFIDENCE_INJECTION_DISABLED = (
    os.getenv("KNOWLEDGE_DISABLE_LOW_CONFIDENCE_INJECTION", "0") == "1"
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

MAX_SUB_QUESTIONS = 6

# CJK-pasted question lists use the full-width question mark (U+FF1F, "？")
# instead of (or alongside) the ASCII "?". Recognized everywhere the ASCII
# mark is, so a pasted Chinese/Japanese FAQ list splits the same way an
# ASCII one does.
_FULL_WIDTH_QUESTION_MARK = "？"
_QUESTION_MARK_CHARS = "?" + _FULL_WIDTH_QUESTION_MARK

_LEADING_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*+•]|\d{1,3}[.)])\s*")
_QUESTION_SEGMENT_RE = re.compile(r"[^?？]+[?？]")
_MIN_SUB_QUESTION_CHARS = 8
_MAX_SUB_QUESTION_CHARS = 300


def is_multi_question_query(query: object) -> bool:
    """Return whether the user message asks several distinct questions."""
    if not isinstance(query, str):
        return False
    return sum(query.count(mark) for mark in _QUESTION_MARK_CHARS) >= 2


def split_sub_questions(query: object, max_questions: int | None = None) -> list[str]:
    """Split a multi-part message into standalone sub-questions for retrieval.

    Deterministic on purpose (no LLM): pasted question lists put one question
    per line, so line-shaped questions win; inline prose falls back to
    ``?``-terminated segments; a numbered/bulleted list with no ``?`` anywhere
    in the message falls back to list-marker lines (e.g. a pasted procedure
    written as imperative steps). The list-marker fallback only fires when the
    message contains NO ``?`` character AT ALL — not merely "no line ends
    with ?". A message like "Can you help? Details:\n1. First\n2. Second" has
    its one real question mid-line (not line-terminal), so a line-terminal-
    only check would wrongly treat this as list-shaped and split it, losing
    the real question. Checking the whole message for any ``?`` keeps a
    genuine mixed message (one real question plus unrelated list lines)
    intact. Returns ``[]`` unless at least two usable questions are found —
    single questions keep the normal retrieval path.

    ``max_questions`` caps the returned list. ``None`` (the default) returns
    every usable question found, uncapped: callers that need a bounded subset
    for downstream fan-out (e.g. retrieval) must slice the result themselves
    and keep the remainder visible instead of relying on this function to
    silently drop them.
    """
    if not isinstance(query, str):
        return []
    questions: list[str] = []
    for line in query.splitlines():
        stripped = _LEADING_LIST_MARKER_RE.sub("", line.strip())
        if (
            stripped.endswith(("?", _FULL_WIDTH_QUESTION_MARK))
            and len(stripped) >= _MIN_SUB_QUESTION_CHARS
        ):
            questions.append(stripped[-_MAX_SUB_QUESTION_CHARS:])
    if len(questions) < 2:
        segments = [
            " ".join(segment.split())
            for segment in _QUESTION_SEGMENT_RE.findall(query)
        ]
        questions = [
            segment[-_MAX_SUB_QUESTION_CHARS:]
            for segment in segments
            if len(segment) >= _MIN_SUB_QUESTION_CHARS
        ]
    if len(questions) < 2:
        if not any(mark in query for mark in _QUESTION_MARK_CHARS):
            list_marker_questions: list[str] = []
            for line in query.splitlines():
                raw = line.strip()
                if not _LEADING_LIST_MARKER_RE.match(raw):
                    continue
                stripped = _LEADING_LIST_MARKER_RE.sub("", raw)
                if len(stripped) >= _MIN_SUB_QUESTION_CHARS:
                    list_marker_questions.append(stripped[-_MAX_SUB_QUESTION_CHARS:])
            if len(list_marker_questions) >= 2:
                questions = list_marker_questions
    if len(questions) < 2:
        return []
    return questions if max_questions is None else questions[:max_questions]


def low_confidence_query_tokens(query: object) -> set[str]:
    if not isinstance(query, str):
        return set()
    return extract_salient_query_tokens(query)


def has_direct_evidence_for_query(query: object, chunks: list[dict]) -> bool:
    """Return whether low-scored retrieval still has literal answer evidence.

    A single shared token is not evidence: a question about webhooks always
    shares the token "webhook" with tangential webhook chunks, which let
    fabricated answers through the low-confidence guard (2026-08-17 Voys
    incident). Require the chunks to cover at least two salient query tokens
    (or all of them, for one-token queries) before skipping the guard.
    """
    tokens = low_confidence_query_tokens(query)
    if not tokens:
        return False
    required = min(2, len(tokens))
    covered: set[str] = set()
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        chunk_tokens = extract_salient_query_tokens(
            " ".join(
                str(chunk.get(key) or "")
                for key in ("title", "heading_path", "source_label", "text", "content")
            )
        )
        covered |= tokens & chunk_tokens
        if len(covered) >= required:
            return True
    return False


def should_apply_low_confidence_injection(
    confidence_band: object,
    *,
    user_query: object,
    evidence_chunks: list[dict],
) -> bool:
    if confidence_band not in ("low", "unknown"):
        return False
    return not has_direct_evidence_for_query(user_query, evidence_chunks)
