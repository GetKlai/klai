"""What the grounding checker treats as a claim about the organisation.

SPEC-RAG-ANSWER-JUDGES-001. The checker lists every concrete statement a reply
makes about the organisation, and on a turn without a source a single flagged
statement costs the visitor the answer. That made the assistant refuse turns
that were about itself: measured on 2026-09-18, "Ik wil graag een medewerker
spreken" and "Can I speak english?" both came back as "I can't find this in our
help articles", where the system before these checks answered them. The flagged
sentence was "Je kunt een afspraak maken met een medewerker via de knop onder
deze tekst" — a fact about the chat window that no help article will ever carry.

Widening the prompt's skip-list was tried first and rejected: a list broad
enough to free that sentence also freed "Ik verbind je nu door met een collega"
and "tijdens die afspraak wordt je contract opgezegd", which are exactly the
inventions this check exists to stop. The guarantee is handed over as evidence
instead, so the checker stays strict about everything the reply claims the
appointment or the button will DO.
"""

from __future__ import annotations

import pytest

from klai_chat_prompts import (
    CHAT_CONTRACT_TITLE,
    GROUNDING_CHECK_SYSTEM_PROMPT,
    chat_contract_article,
)


def test_the_contract_is_an_article_the_checker_can_read() -> None:
    article = chat_contract_article(appointment_offered=True)
    assert article is not None
    title, text = article
    assert title == CHAT_CONTRACT_TITLE
    assert "book an appointment" in text
    assert "language the visitor writes in" in text


def test_a_turn_without_a_booking_button_gets_no_contract() -> None:
    """The guarantee may only be evidence where it is true.

    Without a booking route there is no button, so a reply promising one is an
    invented claim and has to stay flagged.
    """
    assert chat_contract_article(appointment_offered=False) is None


def test_the_checker_is_pointed_at_the_contract_instead_of_a_skip_list() -> None:
    assert 'excerpt titled "This chat"' in GROUNDING_CHECK_SYSTEM_PROMPT


@pytest.mark.parametrize(
    "judged",
    [
        "what an appointment or an employee will DO for the visitor",
        "promise to transfer or connect them to a person now",
        "effect a button has on their account or subscription",
    ],
)
def test_the_dangerous_neighbours_stay_judged(judged: str) -> None:
    """Three inventions that sit next to the guarantee and must not ride along."""
    assert judged in GROUNDING_CHECK_SYSTEM_PROMPT


def test_the_checker_still_asks_for_the_statements_that_matter() -> None:
    """A price, a step and a limitation produced the serious invented cases."""
    for kept in ("a price", "a step", "a limitation", "a menu path", "a time frame"):
        assert kept in GROUNDING_CHECK_SYSTEM_PROMPT
