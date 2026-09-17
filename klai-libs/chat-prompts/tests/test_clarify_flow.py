"""Tests for the SPEC-RAG-CLARIFY-FLOW-001 REQ-1 shared building blocks.

These are pure decision helpers with no I/O: the answer-claims classification
prompt/schema/parser (beslissing 2) and ``should_clarify`` (beslissing 1).
Wiring an actual model call is REQ-2 through REQ-5, not tested here.
"""

from __future__ import annotations

import pytest

from klai_chat_prompts import (
    CLARIFY_TURN_ADDENDUM,
    AnswerClaims,
    answer_claims_response_format,
    may_show_model_text_without_sources,
    parse_answer_claims,
    should_clarify,
)

# ─── parse_answer_claims ──────────────────────────────────────────────────


def test_parse_answer_claims_valid_no_claims() -> None:
    assert parse_answer_claims('{"category": "no_claims"}') == "no_claims"


def test_parse_answer_claims_valid_claims() -> None:
    assert parse_answer_claims('{"category": "claims"}') == "claims"


@pytest.mark.parametrize(
    "content",
    [
        None,
        "",
        "not json at all",
        '{"category": "maybe"}',
        '{"category": "no_claims", "extra": "field"}',
    ],
)
def test_parse_answer_claims_failure_returns_none(content: str | None) -> None:
    # The code does not branch differently between these failure shapes — a
    # missing/empty body, invalid JSON, an unlisted category, and a
    # strict-schema-rejected extra field all take the same except-and-return
    # path, so one parametrization covers all of them.
    assert parse_answer_claims(content) is None


# ─── may_show_model_text_without_sources ──────────────────────────────────


def test_may_show_true_only_for_no_claims() -> None:
    assert may_show_model_text_without_sources("no_claims") is True
    assert may_show_model_text_without_sources("claims") is False
    assert may_show_model_text_without_sources(None) is False


# ─── should_clarify ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("band", "has_direct_evidence", "expected"),
    [
        ("low", False, True),
        ("unknown", False, True),
        ("low", True, False),
        ("unknown", True, False),
        ("medium", False, False),
        ("high", False, False),
        ("medium", True, False),
        (None, False, False),
    ],
)
def test_should_clarify_branches(band: object, has_direct_evidence: bool, expected: bool) -> None:
    assert should_clarify(band, has_direct_evidence=has_direct_evidence) is expected


# ─── response_format helper matches the Pydantic model ────────────────────


def test_answer_claims_response_format_matches_model_schema() -> None:
    response_format = answer_claims_response_format()
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["schema"] == AnswerClaims.model_json_schema()


# ─── CLARIFY_TURN_ADDENDUM: no prompt-text snapshotting, but a real word ban ──


def test_clarify_turn_addendum_has_external_and_internal_variants() -> None:
    assert set(CLARIFY_TURN_ADDENDUM) == {"external", "internal"}
    for text in CLARIFY_TURN_ADDENDUM.values():
        assert isinstance(text, str)
        assert "[This turn]" in text


def test_clarify_turn_addendum_external_never_says_knowledge_base() -> None:
    # The external (help-page visitor) variant must never leak the internal
    # "kennisbank"/"knowledge base" vocabulary — a requirement that can
    # regress on a future edit, unlike the exact wording, which is not
    # snapshot-tested.
    external = CLARIFY_TURN_ADDENDUM["external"].lower()
    assert "kennisbank" not in external
    assert "knowledge base" not in external
