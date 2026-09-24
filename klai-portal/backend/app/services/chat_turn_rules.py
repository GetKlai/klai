"""Deterministic rules about the user's latest turn, shared by every chat surface.

Moved from the LiteLLM hook (one-chat-pipeline slice 4):
``klai_kb_request_context.is_trivial`` / ``is_meta_query`` and
``klai_kb_confidence_policy.is_multi_question_query`` /
``split_sub_questions``. The widget, the partner API and the internal chat all
decide these with the same code; the hook keeps its own copy only until slice
9 removes it.
"""

from __future__ import annotations

import re

TRIVIAL_PATTERNS = re.compile(
    r"^(ok|okay|oke|oké|ja|nee|yes|no|bedankt|thanks|thank you|"
    r"dank je|dank u|graag|np|prima|goed|good|sure|hmm+|ah+|oh+|"
    r"begrepen|understood|clear|got it|doei|bye|hoi|hallo|hello|hi)[\s!.?]*$",
    re.IGNORECASE,
)

META_QUERY_PATTERNS = re.compile(
    r"^\s*(?:"
    r"wat\s+(?:kan|kun)\s+(?:ik|je|jij)"
    r"(?:\s+(?:hier|met\s+(?:klai|jou|je)|allemaal|doen)){0,3}"
    r"|wat\s+(?:is|doet|doe)\s+(?:klai|jij|je)"
    r"|wat\s+(?:kan|kun)\s+(?:klai|je|jij)(?:\s+doen)?"
    r"|hoe\s+werkt\s+(?:deze\s+chat|dit|klai|jij|je)"
    r"|hoe\s+(?:gebruik|werk)\s+ik\s+(?:met\s+)?(?:deze\s+chat|klai|dit)"
    r"|wie\s+ben\s+je"
    r"|waarvoor\s+is\s+(?:dit|klai)"
    r"|waar\s+is\s+(?:dit|klai)\s+voor"
    r"|help(?:\s+me)?"
    r"|hulp"
    r"|what\s+can\s+(?:i|you)\s+do"
    r"(?:\s+(?:here|with\s+(?:klai|you))){0,2}"
    r"|what\s+(?:is|are|does)\s+klai(?:\s+do)?"
    r"|how\s+does\s+(?:this\s+chat|this|klai)\s+work"
    r"|how\s+do\s+i\s+use\s+(?:this\s+chat|klai|this)"
    r"|who\s+are\s+you"
    r")[\s!.?]*$",
    re.IGNORECASE,
)


def is_trivial(text: str) -> bool:
    # Short fragments ("top", "thx", a thumbs-up) skip retrieval UNLESS they are
    # question-shaped: a "?" plus at least one letter or digit. A length-only
    # floor sent "VPN?" and "prijs?" to the model without any knowledge. A short
    # meta request ("help") is not trivial either: it has its own prompt.
    text = text.strip()
    if len(text) < 8:
        question_shaped = "?" in text and any(char.isalnum() for char in text)
        return not (question_shaped or META_QUERY_PATTERNS.match(text))
    return bool(TRIVIAL_PATTERNS.match(text))


def is_meta_query(text: str) -> bool:
    """Return whether the user asks about Klai itself, not about knowledge-base content."""
    return bool(META_QUERY_PATTERNS.match(text.strip()))


def _previous_assistant_text(messages: list[dict]) -> str:
    seen_user = False
    for message in reversed(messages):
        role = message.get("role")
        if role == "user" and not seen_user:
            seen_user = True
            continue
        if seen_user and role == "assistant":
            content = message.get("content")
            return content if isinstance(content, str) else ""
    return ""


def is_trivial_turn(messages: list[dict], query: str) -> bool:
    """The one trivial-message gate for every surface: no retrieval for this turn.

    The hook's rule (``is_trivial``) plus one exception it lacked: a "ja" or
    "nee" that answers a question the assistant just asked is the reply to a
    clarifying question, not a thank-you, and needs the knowledge base with
    that question as its history. The widget's answer plan asks exactly such
    questions, so without the exception the gate would cut the visitor's
    answer off from the articles.
    """
    if not is_trivial(query):
        return False
    return not _previous_assistant_text(messages).rstrip().endswith("?")


# ---------------------------------------------------------------------------
# Multi-part messages
# ---------------------------------------------------------------------------

# retrieval-api accepts at most this many sub_queries (models.py); questions
# beyond the cap are reported to the model as not searched, never dropped.
MAX_SUB_QUESTIONS = 6

# CJK-pasted question lists use the full-width question mark (U+FF1F, "\uff1f")
# instead of (or alongside) the ASCII "?". Recognized everywhere the ASCII
# mark is, so a pasted Chinese/Japanese FAQ list splits the same way an
# ASCII one does.
_FULL_WIDTH_QUESTION_MARK = "\uff1f"
_QUESTION_MARK_CHARS = "?" + _FULL_WIDTH_QUESTION_MARK

_LEADING_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*+•]|\d{1,3}[.)])\s*")
# Split right after every question mark. A findall of "[^?]+[?]" gave the same
# segments but backtracked quadratically on text without a mark: 1.4 s on 20 000
# characters, and a partner body may carry 128 KB.
_AFTER_QUESTION_MARK_RE = re.compile(r"(?<=[?\uff1f])")
_MIN_SUB_QUESTION_CHARS = 8
_MAX_SUB_QUESTION_CHARS = 300


# Counting '?' misses the most common way a support question asks two things:
# two questions joined by "en" behind a single mark ("Wat is de opzegtermijn
# en hoe zeg ik namens een klant op?"). So every sentence ending in '?' still
# counts once, and each further clause joined by en/and/or counts again when it
# opens like a question of its own: a wh-word (optionally after a preposition,
# "naar welk adres"), a finite verb in first position (how a Dutch yes/no
# question inverts), or an embedded "of" (whether). A sentence without '?'
# counts each clause of that shape. "Werkt dit op iOS en Android?" stays one
# question: "Android" does not open like one.
#
# Rhetorical marks ("Echt waar?") are still counted. A filter for them was
# tried and measured: on 18 Dutch messages written for the test it dropped as
# many real questions ("Kosten? Levertijd? Garantie?") as it removed filler.
_WH_WORDS = (
    r"wie|wat|waar|waarom|waarvoor|waarmee|waardoor|wanneer|hoe|hoeveel|hoelang"
    r"|welke|welk|who|what|where|why|when|how|which|whether"
)
_LEADING_COORDINATOR = r"^(?:en|of|maar|and|or|but)?\s*"
_WH_START_RE = re.compile(
    rf"{_LEADING_COORDINATOR}"
    r"(?:(?:naar|met|voor|van|in|op|aan|bij|over|tot|uit|door"
    r"|to|with|for|from|on|at|by)\s+)?"
    rf"(?:{_WH_WORDS})\b",
    re.IGNORECASE,
)
# The bare imperative stems ("doe", "do") are left out on purpose: "Doe dat
# maar" opens with a verb and is an instruction, not a question.
_INVERSION_START_RE = re.compile(
    rf"{_LEADING_COORDINATOR}"
    r"(?:is|zijn|was|waren|heb|heeft|hebben|had|hadden|kan|kun|kunt|kunnen"
    r"|mag|mogen|moet|moeten|wil|wilt|willen|doet|doen|gaat|gaan|klopt|geldt"
    r"|werkt|wordt|worden|komt|staat|lukt|krijg|krijgt|krijgen"
    r"|does|did|can|could|should|would|will|are|were)\b",
    re.IGNORECASE,
)
# "of" marks an embedded question only mid-clause ("… checken of dat geldt").
_EMBEDDED_WHETHER_RE = re.compile(r"\w\s+of\s+\w", re.IGNORECASE)
# A '.' or ':' ends a sentence only before whitespace, so "66.86" and "10:30"
# stay inside one.
_SENTENCE_END = r"(?:[?\uff1f;\n]|[.:](?=\s|$))"
_SENTENCE_RE = re.compile(rf"(?:(?!{_SENTENCE_END}).)+{_SENTENCE_END}?", re.DOTALL)
_COORDINATOR_RE = re.compile(r",?\s+\b(?:en|and|or)\b\s+", re.IGNORECASE)


def _opens_like_question(clause: str) -> bool:
    return len(clause.split()) >= 2 and bool(
        _WH_START_RE.match(clause) or _INVERSION_START_RE.match(clause) or _EMBEDDED_WHETHER_RE.search(clause)
    )


def is_multi_question_query(query: object) -> bool:
    """Return whether the user message asks several distinct questions."""
    if not isinstance(query, str):
        return False
    units = 0
    for sentence in _SENTENCE_RE.findall(query):
        sentence = sentence.strip()
        marked = sentence.endswith(("?", _FULL_WIDTH_QUESTION_MARK))
        clauses = [
            _LEADING_LIST_MARKER_RE.sub("", clause.strip())
            for clause in _COORDINATOR_RE.split(sentence.rstrip("?\uff1f.;:").strip())
        ]
        clauses = [clause for clause in clauses if clause]
        if not clauses:
            continue
        units += int(marked) + sum(_opens_like_question(clause) for clause in clauses[1 if marked else 0 :])
        if units >= 2:
            return True
    return False


def split_sub_questions(query: object, max_questions: int | None = None) -> list[str]:
    """Split a multi-part message into standalone sub-questions for retrieval.

    Deterministic on purpose (no LLM): pasted question lists put one question
    per line, so line-shaped questions win; inline prose falls back to
    ``?``-terminated segments; a numbered/bulleted list with no ``?`` anywhere
    in the message falls back to list-marker lines (a pasted procedure written
    as imperative steps). The list-marker fallback only fires when the message
    contains NO ``?`` at all: "Can you help? Details:\\n1. First\\n2. Second"
    has its one real question mid-line, and splitting it would lose that
    question. Returns ``[]`` unless at least two usable questions are found.

    ``max_questions=None`` returns every usable question, uncapped: a caller
    that fans out must slice the result itself and keep the remainder visible.
    """
    if not isinstance(query, str):
        return []
    questions: list[str] = []
    for line in query.splitlines():
        stripped = _LEADING_LIST_MARKER_RE.sub("", line.strip())
        if stripped.endswith(("?", _FULL_WIDTH_QUESTION_MARK)) and len(stripped) >= _MIN_SUB_QUESTION_CHARS:
            questions.append(stripped[-_MAX_SUB_QUESTION_CHARS:])
    if len(questions) < 2:
        segments = [
            " ".join(part.split())
            for part in _AFTER_QUESTION_MARK_RE.split(query)
            if len(part) > 1 and part.endswith(("?", _FULL_WIDTH_QUESTION_MARK))
        ]
        questions = [
            segment[-_MAX_SUB_QUESTION_CHARS:] for segment in segments if len(segment) >= _MIN_SUB_QUESTION_CHARS
        ]
    if len(questions) < 2 and not any(mark in query for mark in _QUESTION_MARK_CHARS):
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
