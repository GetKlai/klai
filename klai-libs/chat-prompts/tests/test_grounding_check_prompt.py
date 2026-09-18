"""What the grounding checker is told to ignore.

SPEC-RAG-ANSWER-JUDGES-001. The checker lists every concrete statement a reply
makes about the organisation, and on a turn without a source a single flagged
statement costs the visitor the answer. That made the assistant refuse to answer
turns that were about itself: measured on 2026-09-18, "Ik wil graag een
medewerker spreken" and "Can I speak english?" both came back as "I can't find
this in our help articles", where the system before these checks answered them.

The cause was not the decision table but the checker's idea of a statement: it
flagged "Je kunt een afspraak maken met een medewerker via de knop onder deze
tekst" as an unsupported claim about the company. No help article will ever
carry that sentence, because it describes the chat window.

These tests pin the skip-list, not the wording around it. On the six
conversational drafts from real traffic the instruction took flagged replies
from 4 of 6 to 0 of 6 while the invented-price and invented-step drafts stayed
flagged.
"""

from __future__ import annotations

import pytest

from klai_chat_prompts import GROUNDING_CHECK_SYSTEM_PROMPT


@pytest.mark.parametrize(
    "phrase",
    [
        "about ITSELF or about this conversation",
        "which languages it speaks or understands",
        "reach an employee or book an appointment through this chat",
        "what a button or link in",
        "not claims about the company",
    ],
)
def test_the_checker_is_told_to_skip_the_assistants_own_words(phrase: str) -> None:
    assert phrase in GROUNDING_CHECK_SYSTEM_PROMPT


def test_the_checker_still_asks_for_the_statements_that_matter() -> None:
    """The skip-list may not grow into a licence to ignore company claims.

    A price, a step and a limitation are the three categories that produced the
    serious cases when they were invented, so they stay named explicitly.
    """
    for kept in ("a price", "a step", "a limitation", "a menu path", "a time frame"):
        assert kept in GROUNDING_CHECK_SYSTEM_PROMPT
