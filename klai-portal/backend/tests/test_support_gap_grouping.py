"""Acceptance tests for support-gap grouping.

Contract: docs/architecture/support-gap-detection.md § "Existing inbox".
``group_findings`` folds a new finding into an existing open group only when the
judge verifies the same reusable need (same diagnosis, language, audience). The
single LiteLLM boundary is mocked here; every test asserts an observable outcome
(a stamped or absent ``group_question_key``, the exact model input, or a raised
error), never merely that nothing crashed. ``group_question_key`` is internal —
it originates only from a verified candidate key, never from an input payload.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import settings
from app.services import support_cases
from app.services import support_gap_grouping as grp
from app.services.support_case_analysis import SupportCaseAnalysisError


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(settings, "litellm_base_url", "http://litellm:4000", raising=False)
    monkeypatch.setattr(settings, "litellm_master_key", "master-key", raising=False)
    monkeypatch.setattr(settings, "conversation_judge_model", "klai-medium", raising=False)


class _Recorder:
    """Records the grouping model call and returns a scripted response."""

    def __init__(self, response: object) -> None:
        self.response = response
        self.calls = 0
        self.last_user: str | None = None

    async def __call__(self, *, system: str, user: str) -> str:
        self.calls += 1
        self.last_user = user
        return json.dumps(self.response)


def _patch_llm(monkeypatch, response: object) -> _Recorder:
    rec = _Recorder(response)
    monkeypatch.setattr(grp, "_call_llm", rec)
    return rec


def _finding(question: str, *, diagnosis: str = "missing", language: str = "en", audience: str = "customer") -> dict:
    return {
        "question": question,
        "language": language,
        "diagnosis": diagnosis,
        "rationale": "r",
        "missing_information": "m",
        "proposed_change": "c",
        "audience": audience,
        "message_ids": ["m1"],
        "articles": [],
        "search_queries": [question],
        "comparison_limitations": [],
        "gap_type": None,
        "top_score": None,
    }


def _candidate(key: str, question: str, *, diagnosis="missing", language="en", audience="customer") -> dict:
    return {
        "question_key": key,
        "question": question,
        "diagnosis": diagnosis,
        "language": language,
        "audience": audience,
    }


async def test_paraphrased_same_need_merges_into_existing_group(monkeypatch):
    rec = _patch_llm(monkeypatch, {"assignments": [{"index": 0, "group_question_key": "grp-port"}]})
    findings = [_finding("How do I move my number over?")]
    candidates = [_candidate("grp-port", "How do I port my phone number?")]

    result = await group_and_assert_copy(findings, candidates)
    assert result[0]["group_question_key"] == "grp-port"
    assert rec.calls == 2


async def test_same_need_in_one_case_shares_one_group_and_keeps_evidence(monkeypatch):
    findings = [
        _finding("How do I port my phone number?"),
        _finding("Can I move my existing number over?"),
        _finding("What is the process for transferring my number?"),
    ]
    for index, finding in enumerate(findings):
        finding["message_ids"] = [f"source-{index}"]
    first_key = support_cases._question_key(
        question=findings[0]["question"],
        diagnosis="missing",
        language="en",
        kb_slug="products",
        audience="customer",
    )
    second_key = support_cases._question_key(
        question=findings[1]["question"],
        diagnosis="missing",
        language="en",
        kb_slug="products",
        audience="customer",
    )
    _patch_llm(
        monkeypatch,
        {
            "assignments": [
                {"index": 0, "group_question_key": None},
                {"index": 1, "group_question_key": first_key},
                {"index": 2, "group_question_key": second_key},
            ]
        },
    )

    with patch.object(support_cases, "_open_group_candidates", AsyncMock(return_value=[])):
        result = await support_cases._grouped_findings(
            AsyncMock(), org_id=7, kb_slug="products", exclude_case_id=11, findings=findings
        )

    rows = [
        support_cases._finding_gap(
            org_id=7,
            user_id="user",
            kb_slug="products",
            case_id=11,
            finding=finding,
            occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
        )
        for finding in result
    ]
    assert {row.question_key for row in rows} == {first_key}
    assert [row.evidence["message_ids"] for row in rows] == [["source-0"], ["source-1"], ["source-2"]]


async def test_same_batch_self_match_keeps_other_merges(monkeypatch):
    key = "local-port"
    _patch_llm(
        monkeypatch,
        {
            "assignments": [
                {"index": 0, "group_question_key": key},
                {"index": 1, "group_question_key": key},
            ]
        },
    )
    findings = [_finding("How do I port my number?"), _finding("Can I move my number over?")]
    candidate = _candidate(key, "How do I port my number?")
    candidate["finding_index"] = 0

    result = await grp.group_findings(findings, [candidate])

    assert "group_question_key" not in result[0]
    assert result[1]["group_question_key"] == key


async def test_metadata_cohorts_are_judged_without_competing_keys(monkeypatch):
    def decisions(*items):
        return {"assignments": [{"index": index, "group_question_key": key} for index, key in items]}

    responses = iter(
        [
            decisions((1, None), (2, "missing-0")),
            decisions((1, None), (2, "missing-0")),
            decisions((3, None), (4, "incomplete-0")),
            decisions((3, None), (4, None)),
        ]
    )
    submitted = []

    async def call(*, system: str, user: str) -> str:
        submitted.append(json.loads(user))
        return json.dumps(next(responses))

    monkeypatch.setattr(grp, "_call_llm", call)
    findings = [
        _finding("Comment modifier mon profil ?", language="fr"),
        _finding("How do I port a number in?"),
        _finding("Bring my number into the service"),
        _finding("Show my caller ID", diagnosis="incomplete"),
        _finding("Hide my caller ID", diagnosis="incomplete"),
    ]
    candidates = [
        {**_candidate("missing-0", findings[1]["question"]), "finding_index": 1},
        {**_candidate("incomplete-0", findings[3]["question"], diagnosis="incomplete"), "finding_index": 3},
    ]

    result = await grp.group_findings(findings, candidates)

    assert [[finding["index"] for finding in call["findings"]] for call in submitted] == [[1, 2]] * 2 + [[3, 4]] * 2
    assert result[2]["group_question_key"] == "missing-0"
    assert "group_question_key" not in result[4]


async def test_metadata_cohorts_share_one_timeout_budget(monkeypatch):
    monkeypatch.setattr(grp, "_GROUPING_TIMEOUT_S", 0.03)

    async def call(*, system: str, user: str) -> str:
        await asyncio.sleep(0.02)
        submitted = json.loads(user)
        return json.dumps(
            {
                "assignments": [
                    {"index": finding["index"], "group_question_key": None} for finding in submitted["findings"]
                ]
            }
        )

    monkeypatch.setattr(grp, "_call_llm", call)
    findings = [_finding("Missing answer"), _finding("Incomplete answer", diagnosis="incomplete")]
    candidates = [
        _candidate("missing", "Existing gap"),
        _candidate("incomplete", "Existing gap", diagnosis="incomplete"),
    ]

    with pytest.raises(TimeoutError):
        await grp.group_findings(findings, candidates)


@pytest.mark.parametrize("source_index", [1, False, -1])
async def test_same_batch_match_rejects_future_boolean_and_unknown_indexes(monkeypatch, source_index):
    _patch_llm(
        monkeypatch,
        {"assignments": [{"index": 0, "group_question_key": "other"}, {"index": 1, "group_question_key": None}]},
    )
    candidate = {**_candidate("other", "Other request"), "finding_index": source_index}

    with pytest.raises(SupportCaseAnalysisError, match="same-batch"):
        await grp.group_findings(
            [_finding("First request"), _finding("Other request")],
            [candidate, _candidate("eligible", "An existing request")],
        )


async def test_different_device_stays_separate(monkeypatch):
    rec = _patch_llm(monkeypatch, {"assignments": [{"index": 0, "group_question_key": None}]})
    findings = [_finding("How do I set up voicemail on my desk phone?")]
    candidates = [_candidate("grp-mobile", "How do I set up voicemail on my mobile app?")]

    result = await group_and_assert_copy(findings, candidates)
    assert "group_question_key" not in result[0]
    assert rec.calls == 1


async def test_empty_candidates_skips_the_model(monkeypatch):
    rec = _patch_llm(monkeypatch, {"assignments": []})
    findings = [_finding("How do I port my number?")]

    result = await group_and_assert_copy(findings, [])
    assert "group_question_key" not in result[0]
    assert rec.calls == 0


async def test_non_gap_findings_are_never_grouped(monkeypatch):
    rec = _patch_llm(monkeypatch, {"assignments": []})
    findings = [
        _finding("Already answered?", diagnosis="covered"),
        _finding("Refund my charge?", diagnosis="non_knowledge"),
        _finding("Unclear outcome?", diagnosis="uncertain"),
    ]
    candidates = [_candidate("grp-x", "Anything")]

    result = await group_and_assert_copy(findings, candidates)
    assert all("group_question_key" not in f for f in result)
    assert rec.calls == 0  # nothing groupable, so the batch never runs


async def test_only_gap_findings_are_submitted_to_the_model(monkeypatch):
    rec = _patch_llm(monkeypatch, {"assignments": [{"index": 1, "group_question_key": "grp-port"}]})
    findings = [
        _finding("Already answered?", diagnosis="covered"),
        _finding("How do I move my number over?"),  # index 1, groupable
    ]
    candidates = [_candidate("grp-port", "How do I port my number?")]

    result = await group_and_assert_copy(findings, candidates)
    assert rec.last_user is not None
    submitted = json.loads(rec.last_user)
    assert [f["index"] for f in submitted["findings"]] == [1]  # covered finding withheld
    assert result[1]["group_question_key"] == "grp-port"
    assert "group_question_key" not in result[0]


async def test_fabricated_group_key_is_rejected(monkeypatch):
    _patch_llm(monkeypatch, {"assignments": [{"index": 0, "group_question_key": "not-a-real-key"}]})
    findings = [_finding("How do I port my number?")]
    candidates = [_candidate("grp-port", "How do I port my number?")]

    with pytest.raises(SupportCaseAnalysisError):
        await grp.group_findings(findings, candidates)


async def test_match_across_different_diagnosis_is_rejected(monkeypatch):
    # The model returned a whitelisted key, but for a candidate whose diagnosis
    # differs — a wrong merge the server must refuse.
    _patch_llm(monkeypatch, {"assignments": [{"index": 0, "group_question_key": "grp-port"}]})
    findings = [_finding("How do I port my number?", diagnosis="missing")]
    candidates = [
        _candidate("grp-port", "How do I port my number?", diagnosis="incomplete"),
        _candidate("eligible", "How do I change my number?"),
    ]

    with pytest.raises(SupportCaseAnalysisError):
        await grp.group_findings(findings, candidates)


async def test_wrong_decision_count_is_rejected(monkeypatch):
    _patch_llm(monkeypatch, {"assignments": []})  # one groupable finding, zero decisions
    findings = [_finding("How do I port my number?")]
    candidates = [_candidate("grp-port", "How do I port my number?")]

    with pytest.raises(SupportCaseAnalysisError):
        await grp.group_findings(findings, candidates)


@pytest.mark.parametrize("second_index", [0, 7, True])
async def test_batch_must_decide_each_actual_finding_once(monkeypatch, second_index):
    _patch_llm(
        monkeypatch,
        {
            "assignments": [
                {"index": 0, "group_question_key": None},
                {"index": second_index, "group_question_key": "grp-port"},
            ]
        },
    )
    with pytest.raises(SupportCaseAnalysisError):
        await grp.group_findings(
            [_finding("How do I change my voicemail?"), _finding("How do I port my number?")],
            [_candidate("grp-port", "How do I port my number?")],
        )


async def test_unmatched_question_does_not_hide_a_later_match(monkeypatch):
    _patch_llm(
        monkeypatch,
        {
            "assignments": [
                {"index": 0, "group_question_key": None},
                {"index": 1, "group_question_key": "grp-port"},
            ]
        },
    )
    result = await grp.group_findings(
        [_finding("How do I change my voicemail?"), _finding("How do I port my number?")],
        [_candidate("grp-port", "How do I port my number?")],
    )
    assert "group_question_key" not in result[0]
    assert result[1]["group_question_key"] == "grp-port"


async def test_too_many_candidates_is_rejected(monkeypatch):
    _patch_llm(monkeypatch, {"assignments": []})
    findings = [_finding("How do I port my number?")]
    candidates = [_candidate(f"grp-{i}", f"Question {i}") for i in range(grp._MAX_CANDIDATES + 1)]

    with pytest.raises(SupportCaseAnalysisError):
        await grp.group_findings(findings, candidates)


async def test_findings_are_copied_not_mutated(monkeypatch):
    _patch_llm(monkeypatch, {"assignments": [{"index": 0, "group_question_key": "grp-port"}]})
    findings = [_finding("How do I port my number?")]
    candidates = [_candidate("grp-port", "How do I port my number?")]

    result = await grp.group_findings(findings, candidates)
    assert result[0]["group_question_key"] == "grp-port"
    assert "group_question_key" not in findings[0]  # caller's input dict untouched
    assert result[0] is not findings[0]


async def group_and_assert_copy(findings: list[dict], candidates: list[dict]) -> list[dict]:
    """Run grouping and assert the caller's findings were never mutated in place."""
    before = json.dumps(findings, sort_keys=True)
    result = await grp.group_findings(findings, candidates)
    assert json.dumps(findings, sort_keys=True) == before
    return result
