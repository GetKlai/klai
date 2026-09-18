"""Offline support-gap evaluation CLI (scripts/evaluate_support_gaps.py).

Loaded from its file path so the test does not depend on the scripts package
being importable, matching the standalone nature of the eval script itself.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_support_gaps.py"
_SPEC = importlib.util.spec_from_file_location("evaluate_support_gaps", _SCRIPT)
assert _SPEC and _SPEC.loader
esg = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(esg)


def finding(diagnosis, question="secret-finding-text"):
    return {"question": question, "diagnosis": diagnosis, "message_ids": ["m1"]}


def reference(questions, content_hash="h1", complete=True, reviewed_by="alice"):
    return {
        "content_hash": content_hash,
        "complete": complete,
        "questions": questions,
        "reviewed_by": reviewed_by,
        "reviewed_at": "2026-01-01T00:00:00Z",
    }


def case(cid="c1", content_hash="h1", analysis_revision=1, status="analyzed", analysis=None, ref=None):
    return {
        "id": cid,
        "content_hash": content_hash,
        "analysis_revision": analysis_revision,
        "status": status,
        "analysis": analysis or [],
        "reference": ref,
    }


def alignment(cid="c1", content_hash="h1", analysis_revision=1, reviewed_by="alice", matches=None):
    return {
        "case_id": cid,
        "content_hash": content_hash,
        "analysis_revision": analysis_revision,
        "reviewed_by": reviewed_by,
        "matches": matches or [],
    }


def test_unreviewed_case_is_unscored_never_scores_precision_one():
    # A finding with no human reference must not count as a correct prediction.
    report = esg.evaluate([case(analysis=[finding("missing")], ref=None)], None)
    assert report["scored_cases"] == 0
    assert report["fully_reference_reviewed"] == 0
    assert report["unscored"] == [{"case_id": "c1", "reason": "no reference"}]
    assert report["aggregate"]["precision"] is None
    assert report["aggregate"]["recall"] is None


def test_reviewed_positives_with_no_prediction_recall_zero_precision_none():
    ref = reference([finding("missing"), finding("incomplete")])
    report = esg.evaluate([case(analysis=[], ref=ref)], None)
    row = report["per_case"][0]
    assert row["true_positives"] == 0 and row["false_negatives"] == 2
    assert row["recall"] == 0.0
    assert row["precision"] is None


def test_unmatched_prediction_is_false_positive():
    # Prediction present, reference has zero questions: no alignment needed.
    ref = reference([])
    report = esg.evaluate([case(analysis=[finding("missing")], ref=ref)], None)
    row = report["per_case"][0]
    assert row["false_positives"] == 1
    assert row["precision"] == 0.0
    assert row["recall"] is None


def test_matched_confusion_matrix():
    analysis = [finding("missing"), finding("uncertain"), finding("audience")]
    ref = reference([finding("missing"), finding("outdated"), finding("covered")])
    align = alignment(
        matches=[
            {"finding_index": 0, "reference_index": 0},  # actionable vs actionable -> TP
            {"finding_index": 1, "reference_index": 1},  # uncertain vs actionable -> FN
            {"finding_index": 2, "reference_index": 2},  # actionable vs covered   -> FP
        ]
    )
    report = esg.evaluate([case(analysis=analysis, ref=ref)], [align])
    row = report["per_case"][0]
    assert (row["true_positives"], row["false_positives"], row["false_negatives"]) == (1, 1, 1)
    assert row["precision"] == 0.5 and row["recall"] == 0.5
    assert report["abstention_count"] == 1


def test_two_sided_case_without_alignment_is_unscored():
    ref = reference([finding("missing")])
    report = esg.evaluate([case(analysis=[finding("missing")], ref=ref)], None)
    assert report["fully_reference_reviewed"] == 1
    assert report["scored_cases"] == 0
    assert report["unscored"][0]["reason"] == "two-sided case without alignment"


def test_incomplete_reference_is_unscored():
    ref = reference([finding("missing")], complete=False)
    report = esg.evaluate([case(analysis=[], ref=ref)], None)
    assert report["unscored"][0]["reason"] == "reference incomplete"
    assert report["fully_reference_reviewed"] == 0


def test_stale_reviewed_reference_raises():
    ref = reference([finding("missing")], content_hash="OLD")
    with pytest.raises(esg.EvaluationError, match="stale"):
        esg.evaluate([case(content_hash="NEW", analysis=[], ref=ref)], None)


def test_alignment_index_out_of_range_raises():
    ref = reference([finding("missing")])
    align = alignment(matches=[{"finding_index": 5, "reference_index": 0}])
    with pytest.raises(esg.EvaluationError, match="out of range"):
        esg.evaluate([case(analysis=[finding("missing")], ref=ref)], [align])


def test_alignment_not_one_to_one_raises():
    ref = reference([finding("missing"), finding("incomplete")])
    analysis = [finding("missing"), finding("incomplete")]
    align = alignment(
        matches=[
            {"finding_index": 0, "reference_index": 0},
            {"finding_index": 1, "reference_index": 0},
        ]
    )
    with pytest.raises(esg.EvaluationError, match="one-to-one"):
        esg.evaluate([case(analysis=analysis, ref=ref)], [align])


def test_alignment_hash_or_reviewer_mismatch_raises():
    ref = reference([finding("missing")])
    c = case(analysis=[finding("missing")], ref=ref)
    with pytest.raises(esg.EvaluationError, match="content_hash"):
        esg.evaluate([c], [alignment(content_hash="other")])
    with pytest.raises(esg.EvaluationError, match="reviewed_by"):
        esg.evaluate([c], [alignment(reviewed_by="  ")])


def test_no_scored_cases_yields_none_not_zero():
    report = esg.evaluate([case(analysis=[finding("missing")], ref=None)], None)
    assert report["scored_cases"] == 0
    assert report["aggregate"]["precision"] is None
    assert report["aggregate"]["recall"] is None


def test_report_leaks_no_raw_question_text():
    ref = reference([finding("missing", question="secret-customer-question")])
    report = esg.evaluate(
        [case(analysis=[finding("missing", question="secret-customer-question")], ref=ref)],
        [alignment(matches=[{"finding_index": 0, "reference_index": 0}])],
    )
    assert "secret-customer-question" not in json.dumps(report)
    assert "secret-finding-text" not in json.dumps(report)


def test_main_reads_files_and_reports(tmp_path, capsys):
    ref = reference([finding("missing")])
    inp = tmp_path / "cases.json"
    inp.write_text(json.dumps([case(analysis=[], ref=ref)]))
    assert esg.main(["--input", str(inp)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["total_cases"] == 1 and out["scored_cases"] == 1


def test_main_nonzero_on_malformed_input(tmp_path, capsys):
    inp = tmp_path / "bad.json"
    inp.write_text("{ not json")
    assert esg.main(["--input", str(inp)]) != 0
    assert capsys.readouterr().err.strip() != ""
