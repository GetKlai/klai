"""The one decision after retrieval: answer, or ask which variant applies.

SPEC-RAG-ANSWER-JUDGES-001 logbook 2.54. The gate reads the retrieved articles,
not a model: it asks only when strong articles from different documents cover
the same topic in different variants and the conversation names none of them.
A model only writes the question.

synthetic-data: generator=hand-written seed=0 (fictional product "Alpha phone
app", help.example.com URLs).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import clarify_decision as cd
from app.services.clarify_gate import ClarifyDecision, clarify_gate

THRESHOLD = 0.4


def _chunk(title: str, score: float, *, heading: str = "Troubleshooter > I can't call", slug: str = "") -> dict:
    return {
        "title": title,
        "heading_path": heading,
        "source_url": f"https://help.example.com/{slug or title.lower().replace(' ', '-')}",
        "reranker_score": score,
        "text": "Check the microphone permission and restart the app.",
    }


IPHONE = _chunk("Alpha phone app for iPhone troubleshooter", 0.91)
ANDROID = _chunk("Alpha phone app for Android troubleshooter", 0.88)
WINDOWS = _chunk("Alpha phone app for Windows troubleshooter", 0.8)
INVOICES = _chunk("Invoices and payments", 0.95, heading="Invoices > Pay by direct debit")
ASK = [{"role": "user", "content": "I can't call"}]


def test_two_documents_on_one_topic_that_differ_in_a_variant_ask_which_applies():
    gate = clarify_gate(ASK, [IPHONE, ANDROID], THRESHOLD)

    assert gate.reason == "asked"
    assert gate.options == ("iPhone", "Android")
    assert gate.axis == "device"


def test_a_variant_the_visitor_named_earlier_is_not_asked_again():
    messages = [
        {"role": "user", "content": "My Android phone is new"},
        {"role": "assistant", "content": "Welcome to the Alpha phone app."},
        {"role": "user", "content": "I can't call"},
    ]

    assert clarify_gate(messages, [IPHONE, ANDROID], THRESHOLD).reason == "variant_named"


def test_an_unrelated_strong_document_is_not_offered_as_a_variant():
    gate = clarify_gate(ASK, [INVOICES, IPHONE, ANDROID, WINDOWS], THRESHOLD)

    assert gate.reason == "asked"
    assert gate.options == ("iPhone", "Android", "Windows")
    assert gate.documents == 4


def test_sections_of_one_document_are_no_choice():
    second_section = {**IPHONE, "heading_path": "Troubleshooter > No sound", "reranker_score": 0.7}

    assert clarify_gate(ASK, [IPHONE, second_section], THRESHOLD).reason == "one_document"


def test_weak_articles_never_ask():
    weak = [{**IPHONE, "reranker_score": 0.3}, {**ANDROID, "reranker_score": 0.2}]

    assert clarify_gate(ASK, weak, THRESHOLD).reason == "soft_gap"


def test_no_second_question_right_after_one():
    messages = [
        {"role": "user", "content": "It does not work"},
        {"role": "assistant", "content": "Which app do you use?"},
        {"role": "user", "content": "I can't call"},
    ]

    assert clarify_gate(messages, [IPHONE, ANDROID], THRESHOLD).reason == "asked_last_turn"


def test_a_greeting_that_ends_on_a_question_is_no_earlier_question():
    messages = [{"role": "assistant", "content": "Hi, how can I help?"}, {"role": "user", "content": "I can't call"}]

    assert clarify_gate(messages, [IPHONE, ANDROID], THRESHOLD).reason == "asked"


def test_documents_that_share_a_title_word_but_not_the_topic_are_no_variants():
    """Two procedures that share only a verb are not two variants of one."""
    voicemail = _chunk("Set up voicemail", 0.9, heading="Voicemail > Record a greeting")
    plan = _chunk("Set up your call plan and change it", 0.8, heading="Call plan > Add a step")

    gate = clarify_gate([{"role": "user", "content": "how do I set up voicemail"}], [voicemail, plan], THRESHOLD)

    assert gate.reason == "no_axis"


def test_editions_named_in_the_title_are_the_options():
    basic = _chunk("Opening hours | Basic", 0.9, heading="Opening hours | Basic > Set the hours")
    advanced = _chunk("Opening hours | Advanced", 0.85, heading="Opening hours | Advanced > Holidays")

    gate = clarify_gate([{"role": "user", "content": "how do I set opening hours"}], [basic, advanced], THRESHOLD)

    assert (gate.reason, gate.options, gate.axis) == ("asked", ("Basic", "Advanced"), "edition")


def test_a_hyphenated_title_word_stays_whole_as_an_option():
    ios = _chunk("Alpha iOS-app", 0.9, slug="ios")
    android = _chunk("Alpha Android-app", 0.85, slug="android")

    assert clarify_gate(ASK, [ios, android], THRESHOLD).options == ("iOS-app", "Android-app")


async def _decide(question: str | None) -> ClarifyDecision:
    reply = None if question is None else cd.ClarifyQuestion(question=question)
    with patch.object(cd, "structured_judge_call", AsyncMock(return_value=reply)) as call:
        decision = await cd.clarify_decision(ASK, [IPHONE, ANDROID], MagicMock(klai_gap_soft_threshold=THRESHOLD))
    assert call.await_count == 1
    return decision


async def test_the_written_question_reaches_the_answer_prompt():
    decision = await _decide("Do you call with the iPhone app or the Android app?")

    assert decision.reason == "asked"
    assert decision.addendum is not None
    assert "Do you call with the iPhone app or the Android app?" in decision.addendum


@pytest.mark.parametrize(
    "question",
    ["See https://help.example.com/app for the steps?", "Which app?\nAnd which phone?", "Which {app} do you use?"],
)
async def test_a_written_question_that_is_not_one_plain_line_is_dropped(question):
    decision = await _decide(question)

    assert decision.reason == "question_shape"
    assert decision.addendum is None


async def test_a_failed_writer_answers_directly():
    decision = await _decide(None)

    assert decision.reason == "model_failed"
    assert decision.addendum is None
