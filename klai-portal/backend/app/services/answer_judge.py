"""The one function that decides what the visitor gets.

SPEC-RAG-ANSWER-JUDGES-001 REQ-2 and REQ-3 put a light judge here: a second
model call that read the written draft and returned "answered", "partial" or
"not_answered", which decided the appointment button. It is gone because it was
a second owner of "answered". The selection step (passage_selection.py)
already lists the parts the visitor asked and, per part, the sentence that
answers it, and in production the light judge disagreed with that on 15 of
about 255 written widget replies in 14 days. The button now follows the
selection's open parts (``decide_answer``), and the statement check
(answer_grounding.py) is the only call after the reply that decides anything:
whether an uncited draft may be shown.

Whether a draft only asks something back is decided from its text, not by a
model: a boolean for it was wrong in 3 of 3 rounds on a pure question (see
:func:`is_clarifying_question`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.services.chat_profile import KbMode

AnswerDecision = Literal["answer", "partial_answer", "refusal", "general_knowledge"]
_UNSUPPORTED_GROUNDING = "some_not_in_articles"


def decide_answer(
    *,
    kb_mode: KbMode,
    has_sources: bool,
    escalation: bool,
    conversational: bool,
    open_part: bool,
    grounding: str | None,
) -> AnswerDecision:
    """What the user gets, for every surface. The checks only add; they never remove a cited answer.

    ``kb_mode`` is the mode the turn is answered under, which is where the
    surfaces really differ (plan §7.2, "Modi"):

    * ``general``: the turn searched nothing (general mode, a question about
      Klai itself, a thank-you), so a missing source is never a reason to
      refuse.
    * ``open``: the knowledge base plus general knowledge. With a source the
      answer is shown; without one it is shown labelled as general knowledge.
    * ``strict`` (the widget, and the internal Strict mode): the rules below.

    The original system is the floor. It showed every draft the citation
    composer could anchor to an article, and only checked model text WITHOUT a
    source for claims. Replayed on the last nine real Voys first turns on
    2026-09-17, letting a model's opinion on "does this answer the question"
    veto answers that had a source turned seven of eighteen answers into "not
    found", and the same question flipped outcome in 5 of 9. So nothing
    removes a cited answer.

    * With a source: always shown. ``open_part`` (the selection left a part the
      visitor asked unanswered, ``KnowledgeTurn.open_parts``) adds the
      appointment button under the answer (``partial_answer``) on the widget;
      the internal chat has no button, so there it only records the decision.
      An escalation or a conversational turn never gets it.
    * Without a source: the statement check decides. Statements the articles do
      not carry get the refusal; otherwise the text is shown. A check that
      failed (``grounding`` is None) refuses too, except on a conversational
      turn: "Ja, ik spreek Engels" states nothing about the organisation, and
      the check failed on 15 of 54 turns when it was measured.

    Safety blocks and broad-mode answers never get here.
    """
    if kb_mode == "general":
        return "answer"
    if kb_mode == "open":
        return "answer" if has_sources else "general_knowledge"
    if has_sources:
        return "partial_answer" if open_part and not escalation and not conversational else "answer"
    if grounding is None:
        return "answer" if conversational else "refusal"
    if grounding == _UNSUPPORTED_GROUNDING:
        return "refusal"
    return "answer"


def is_clarifying_question(text: str) -> bool:
    """The text ends on a question."""
    return text.rstrip().rstrip("*_ ").endswith("?")
