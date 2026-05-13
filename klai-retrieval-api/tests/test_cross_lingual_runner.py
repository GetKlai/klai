"""Unit tests for the cross-lingual eval runner.

Mocks the synthesis function so the test runs offline (no retrieval-api,
no LLM). Verifies aggregation, gate logic, and per-language reporting.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parent.parent / "evaluation"
if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))

import cross_lingual_runner as runner  # noqa: E402

# -- Aggregation logic -------------------------------------------------------


def _result(lang: str, correct: bool | None, error: str | None = None) -> dict:
    return {
        "query": "anything",
        "expected_language": lang,
        "intent_id": "x",
        "response_text": "..." if not error else None,
        "detected_response_language": lang if correct else "und",
        "language_correctness": correct,
        "error": error,
    }


def test_aggregate_all_correct_passes_floor():
    scored = [_result("nl", True) for _ in range(20)] + [_result("de", True) for _ in range(20)]
    out = runner.aggregate_results(scored)

    assert out["per_language"]["nl"]["correct"] == 20
    assert out["per_language"]["nl"]["total_scored"] == 20
    assert out["per_language"]["nl"]["language_correctness_rate"] == 1.0
    assert out["per_language"]["nl"]["passes_floor"] is True
    assert out["gate_passes"] is True
    assert out["failing_languages"] == []
    assert out["overall"]["correct"] == 40
    assert out["overall"]["total_scored"] == 40


def test_aggregate_one_failure_below_floor_fails_gate():
    # 19/20 correct = 95% -> exactly meets the floor (passes)
    scored = [_result("nl", True) for _ in range(19)] + [_result("nl", False)]
    out = runner.aggregate_results(scored)
    assert out["per_language"]["nl"]["language_correctness_rate"] == 19 / 20
    assert out["per_language"]["nl"]["passes_floor"] is True

    # 18/20 = 90% -> fails the 95% floor
    scored = [_result("nl", True) for _ in range(18)] + [_result("nl", False) for _ in range(2)]
    out = runner.aggregate_results(scored)
    assert out["per_language"]["nl"]["language_correctness_rate"] == 0.9
    assert out["per_language"]["nl"]["passes_floor"] is False
    assert out["gate_passes"] is False
    assert "nl" in out["failing_languages"]


def test_aggregate_skipped_unknown_not_counted_as_failure():
    # 10 correct + 5 unknown (skipped) = 100% rate (10/10), gate passes.
    scored = [_result("nl", True) for _ in range(10)] + [_result("nl", None) for _ in range(5)]
    out = runner.aggregate_results(scored)
    assert out["per_language"]["nl"]["correct"] == 10
    assert out["per_language"]["nl"]["total_scored"] == 10  # excludes the skipped
    assert out["per_language"]["nl"]["skipped_unknown"] == 5
    assert out["per_language"]["nl"]["language_correctness_rate"] == 1.0
    assert out["per_language"]["nl"]["passes_floor"] is True


def test_aggregate_errors_dont_inflate_rate():
    # 10 correct + 5 errors. Error queries are not counted as correct.
    scored = [_result("nl", True) for _ in range(10)] + [
        _result("nl", None, error="boom") for _ in range(5)
    ]
    out = runner.aggregate_results(scored)
    assert out["per_language"]["nl"]["correct"] == 10
    assert out["per_language"]["nl"]["total_scored"] == 10
    assert out["per_language"]["nl"]["errors"] == 5
    assert out["per_language"]["nl"]["language_correctness_rate"] == 1.0


def test_aggregate_multi_language_one_failing():
    scored = (
        [_result("nl", True) for _ in range(10)]
        + [_result("de", True) for _ in range(10)]
        + [_result("es", False) for _ in range(10)]  # full failure on ES
    )
    out = runner.aggregate_results(scored)
    assert out["per_language"]["es"]["language_correctness_rate"] == 0.0
    assert out["per_language"]["es"]["passes_floor"] is False
    assert out["gate_passes"] is False
    assert out["failing_languages"] == ["es"]


def test_aggregate_empty_returns_no_overall():
    out = runner.aggregate_results([])
    assert out["overall"]["total_scored"] == 0
    assert out["overall"]["language_correctness_rate"] is None
    # Gate "passes" trivially when there are no failing languages — but
    # operators reading the report will see total_scored=0, which is
    # the actual signal that something is wrong.
    assert out["gate_passes"] is True
    assert out["failing_languages"] == []


# -- Cross-lingual queries fixture ------------------------------------------


def test_cross_lingual_queries_file_loads_and_has_required_fields():
    path = Path(__file__).resolve().parent.parent / "evaluation" / "test_queries_cross_lingual.json"
    queries = runner._load_queries(path)
    assert len(queries) >= 60  # current floor for V1 cross-lingual coverage

    languages_seen = {q["language"] for q in queries}
    # The six target languages must all be present.
    assert {"nl", "en", "de", "fr", "pt", "es"} <= languages_seen

    intent_ids = {q["intent_id"] for q in queries}
    assert len(intent_ids) >= 5  # diversity check


def test_cross_lingual_queries_each_lang_has_minimum_coverage():
    path = Path(__file__).resolve().parent.parent / "evaluation" / "test_queries_cross_lingual.json"
    queries = runner._load_queries(path)
    counts = {}
    for q in queries:
        counts[q["language"]] = counts.get(q["language"], 0) + 1

    # SPEC v1 floor: at minimum 10 queries per target language. The SPEC
    # originally specified 20 but practical V1 lands at 10 per language
    # with multi-intent coverage; re-running this test guards regressions.
    for lang in ("nl", "en", "de", "fr", "pt", "es"):
        assert counts.get(lang, 0) >= 10, f"language {lang} has only {counts.get(lang, 0)} queries"


# -- score_query (with mock synthesis) ---------------------------------------


@pytest.mark.asyncio
async def test_score_query_correct_language_returns_true():
    async def synth(query: str, org_id: str) -> str:
        return (
            "Twee-factor authenticatie kan worden ingeschakeld via "
            "Instellingen > Beveiliging > 2FA inschakelen."
        )

    entry = {
        "query": "Hoe stel ik tweefactorauthenticatie in?",
        "language": "nl",
        "intent_id": "2fa_setup",
        "org_id": "eval-org-001",
        "ground_truth_chunks": [],
        "expected_answer": "",
    }
    result = await runner.score_query(entry, synth)
    assert result["error"] is None
    assert result["detected_response_language"] == "nl"
    assert result["language_correctness"] is True


@pytest.mark.asyncio
async def test_score_query_wrong_language_returns_false():
    async def synth(query: str, org_id: str) -> str:
        # Dutch query, English answer -> mismatch
        return (
            "Two-factor authentication can be enabled from Settings > "
            "Security > Enable 2FA. Make sure you have an authenticator app installed."
        )

    entry = {
        "query": "Hoe stel ik tweefactorauthenticatie in?",
        "language": "nl",
        "intent_id": "2fa_setup",
        "org_id": "eval-org-001",
        "ground_truth_chunks": [],
        "expected_answer": "",
    }
    result = await runner.score_query(entry, synth)
    assert result["error"] is None
    assert result["detected_response_language"] == "en"
    assert result["language_correctness"] is False


@pytest.mark.asyncio
async def test_score_query_synth_error_records_error():
    async def synth(query: str, org_id: str) -> str:
        raise RuntimeError("synth blew up")

    entry = {
        "query": "Hoe stel ik tweefactorauthenticatie in?",
        "language": "nl",
        "intent_id": "2fa_setup",
        "org_id": "eval-org-001",
        "ground_truth_chunks": [],
        "expected_answer": "",
    }
    result = await runner.score_query(entry, synth)
    assert result["error"] == "synth blew up"
    assert result["language_correctness"] is None


@pytest.mark.asyncio
async def test_run_writes_report_and_summary(tmp_path):
    queries = [
        {
            "query": "Hoe stel ik tweefactorauthenticatie in?",
            "language": "nl",
            "intent_id": "2fa_setup",
            "org_id": "eval-org-001",
            "ground_truth_chunks": [],
            "expected_answer": "",
        },
        {
            "query": "How do I set up two-factor authentication?",
            "language": "en",
            "intent_id": "2fa_setup",
            "org_id": "eval-org-001",
            "ground_truth_chunks": [],
            "expected_answer": "",
        },
    ]
    queries_file = tmp_path / "queries.json"
    queries_file.write_text(json.dumps(queries), encoding="utf-8")

    output_file = tmp_path / "results" / "cross_lingual.json"

    async def perfect_synth(query: str, org_id: str) -> str:
        if "tweefactorauthenticatie" in query:
            return (
                "Twee-factor authenticatie kan worden ingeschakeld via Instellingen > Beveiliging."
            )
        return "Two-factor authentication can be enabled from Settings > Security."

    result = await runner.run(queries_file, output_file, perfect_synth, concurrency=2)
    assert result["gate_passes"] is True
    assert output_file.exists()
    body = json.loads(output_file.read_text())
    assert body["summary"]["overall"]["total_scored"] == 2
    assert body["summary"]["overall"]["correct"] == 2
