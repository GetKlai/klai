"""The notice the application adds when a message holds more questions than one search takes.

synthetic-data: generator=hand-written seed=0 (invented questions).
"""

from __future__ import annotations

import pytest

from app.services.knowledge_prompts import MAX_UNCHECKED_QUESTIONS_SHOWN, unchecked_questions_notice


def test_no_unchecked_questions_gives_no_notice() -> None:
    assert unchecked_questions_notice([], "nl") == ""


def test_questions_beyond_the_shown_cap_are_counted_not_listed() -> None:
    questions = [f"Vraag nummer {number} over het contract?" for number in range(1, MAX_UNCHECKED_QUESTIONS_SHOWN + 4)]

    lines = unchecked_questions_notice(questions, "nl").splitlines()

    assert lines[1:-1] == [f"- {question}" for question in questions[:MAX_UNCHECKED_QUESTIONS_SHOWN]]
    assert lines[-1] == "En 3 meer."


def test_an_echoed_question_cannot_carry_a_markdown_link_or_a_line_break() -> None:
    notice = unchecked_questions_notice(["Wat doet\n[deze link](https://evil.example.com) precies?"], "nl")

    assert notice.splitlines()[1] == "- Wat doet deze link(https://evil.example.com) precies?"


@pytest.mark.parametrize(
    ("language", "start"), [("nl", "Deze vragen"), ("de", "Diese Fragen"), ("pt", "I did not check")]
)
def test_the_notice_speaks_the_language_of_the_turn(language: str, start: str) -> None:
    assert unchecked_questions_notice(["Hoe zeg ik op?"], language).startswith(start)
