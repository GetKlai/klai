"""Whether a conversation already had its one clarifying question (clarify_gate.already_asked).

The selection step writes the question; this only decides whether it may be
asked, once per conversation.

synthetic-data: generator=hand-written seed=0 (fictional product "Alpha phone app").
"""

from __future__ import annotations

from app.services.clarify_gate import already_asked


def test_no_second_question_right_after_one():
    messages = [
        {"role": "user", "content": "It does not work"},
        {"role": "assistant", "content": "Which app do you use?"},
        {"role": "user", "content": "I can't call"},
    ]

    assert already_asked(messages)


def test_one_question_per_conversation():
    """A visitor who did not name a device was asked for it, got an answer, and
    was asked for it again two turns later (seen in the owner's review)."""
    messages = [
        {"role": "user", "content": "I can't call"},
        {"role": "assistant", "content": "Do you use an Android phone or an iPhone?"},
        {"role": "user", "content": "Calling is not available"},
        {"role": "assistant", "content": "Select the number and choose call."},
        {"role": "user", "content": "I can't call"},
    ]

    assert already_asked(messages)


def test_a_greeting_that_ends_on_a_question_is_no_earlier_question():
    messages = [{"role": "assistant", "content": "Hi, how can I help?"}, {"role": "user", "content": "I can't call"}]

    assert not already_asked(messages)
