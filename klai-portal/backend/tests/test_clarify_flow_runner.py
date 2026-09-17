"""Unit tests for the REQ-0 baseline runner's pure logic.

Only the SSE parser and the outcome classifier have branching worth
testing - no network, no fixtures that only prove "it didn't crash".
"""

from __future__ import annotations

import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent.parent / "evaluation"
if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))

import clarify_flow_runner as runner  # noqa: E402

# -- parse_sse_stream ---------------------------------------------------


def _frame(delta: dict) -> str:
    return f'data: {{"choices": [{{"delta": {_json(delta)}}}]}}'


def _json(delta: dict) -> str:
    import json

    return json.dumps(delta)


def test_parse_sse_stream_concatenates_content_chunks():
    lines = [
        _frame({"content": "Hallo, "}),
        _frame({"content": "hoe kan ik helpen?"}),
        "data: [DONE]",
    ]
    parsed = runner.parse_sse_stream(lines)
    assert parsed["content"] == "Hallo, hoe kan ik helpen?"
    assert parsed["sources"] is None


def test_parse_sse_stream_keeps_last_single_shot_field():
    lines = [
        _frame({"language": "nl"}),
        _frame({"content": "Antwoord."}),
        _frame({"sources": [{"title": "Pricing"}]}),
        _frame({"broad_mode": "answer"}),
        _frame({"escalation": {"appointment": True}}),
        "data: [DONE]",
    ]
    parsed = runner.parse_sse_stream(lines)
    assert parsed["content"] == "Antwoord."
    assert parsed["sources"] == [{"title": "Pricing"}]
    assert parsed["language"] == "nl"
    assert parsed["broad_mode"] == "answer"
    assert parsed["escalation"] == {"appointment": True}


def test_parse_sse_stream_stops_at_done_marker():
    lines = [
        _frame({"content": "Voor"}),
        "data: [DONE]",
        _frame({"content": "onbereikbaar"}),
    ]
    parsed = runner.parse_sse_stream(lines)
    assert parsed["content"] == "Voor"


def test_parse_sse_stream_ignores_non_data_lines():
    lines = [
        "",
        "id: 1",
        _frame({"content": "Ok"}),
        "data: [DONE]",
    ]
    parsed = runner.parse_sse_stream(lines)
    assert parsed["content"] == "Ok"


# -- classify_turn --------------------------------------------------------


def test_classify_turn_matches_dutch_base_refusal():
    text = "Ik kan dit niet betrouwbaar beantwoorden op basis van de beschikbare kennisbronnen."
    assert runner.classify_turn(text, None) == "canned_refusal"


def test_classify_turn_matches_english_helpdesk_refusal():
    text = "I can't find this in our help articles. If you want to be sure, schedule an appointment with someone who can help you personally."
    assert runner.classify_turn(text, None) == "canned_refusal"


def test_classify_turn_with_sources_is_answer_with_sources():
    assert runner.classify_turn("Klai Chat costs 28 euro per user per month [1].", [{"title": "Pricing"}]) == (
        "answer_with_sources"
    )


def test_classify_turn_no_sources_question_mark_is_clarifying_question():
    assert runner.classify_turn("Bedoel je de prijs van Klai Chat of Klai Knowledge?", None) == ("clarifying_question")


def test_classify_turn_no_sources_no_question_mark_is_answer_without_sources():
    assert runner.classify_turn("General knowledge - not from our help articles.", None) == ("answer_without_sources")


def test_classify_turn_strips_whitespace_before_matching_refusal():
    text = "  Ik kan dit niet betrouwbaar beantwoorden op basis van de beschikbare kennisbronnen.  \n"
    assert runner.classify_turn(text, None) == "canned_refusal"


# -- load_questions ---------------------------------------------------------


def test_load_questions_rejects_unknown_category(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "questions:\n  - {id: x1, category: nonsense, language: nl, text: 'hoi'}\n",
        encoding="utf-8",
    )
    try:
        runner.load_questions(bad)
    except ValueError as exc:
        assert "x1" in str(exc)
    else:
        raise AssertionError("expected ValueError for unknown category")


def test_load_questions_loads_shipped_question_set():
    questions = runner.load_questions(runner.DEFAULT_QUESTIONS_FILE)
    counts: dict[str, int] = {}
    for q in questions:
        counts[q.category] = counts.get(q.category, 0) + 1
    assert counts["vague"] == 30
    assert counts["answerable"] == 30
    assert counts["not_in_kb"] == 20


# -- build_summary ---------------------------------------------------------


def test_build_summary_counts_per_category_and_outcome():
    rows = [
        {"category": "vague", "outcome": "canned_refusal", "question_id": "v1", "content": ""},
        {"category": "vague", "outcome": "clarifying_question", "question_id": "v2", "content": "Wat bedoel je?"},
        {"category": "answerable", "outcome": "answer_with_sources", "question_id": "a1", "content": "Antwoord [1]."},
    ]
    summary = runner.build_summary(rows)
    assert "| vague | 2 |" in summary
    assert "| answerable | 1 |" in summary
    assert "v2" in summary  # clarifying_question turn listed for manual review
    assert "a1" not in summary.split("Manual review")[1]  # answer_with_sources is not
