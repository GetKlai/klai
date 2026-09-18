"""Offline precision/recall for support-gap analysis against reviewed references.

SPEC-RAG-SUPPORT-GAP. Reads exported case-detail records (the analysis the model
produced plus the human reference disposition) and, optionally, an explicit
human alignment between predicted findings and reference questions, then reports
how well the actionable gap diagnoses (missing, incomplete, outdated,
contradictory, findability, audience) match the reviewed truth. It never matches
questions by string similarity or by asking a model — matching is human input
only, so an unmatched finding or reference is counted as unmatched, not guessed.

Only cases whose reference is complete, reviewed and current (its content_hash
equals the case content_hash) are scorable; everything else is reported as
unscored with a reason so the evaluated subset is never confused with the total.
No raw question or customer text is emitted, only ids, hashes, counts and ratios.

Usage:

    python scripts/evaluate_support_gaps.py --input cases.json [--alignment matches.json]

``--input`` is a JSON array of case records; ``--alignment`` is an optional JSON
array of per-case human matchings. Malformed input exits non-zero with a clear
message. The report is printed as JSON on stdout.
"""

from __future__ import annotations

import argparse
import json
import sys

# The six actionable gap diagnoses; the rest (covered, non_knowledge, uncertain)
# are non-gap outcomes. An uncertain prediction opposite an actionable truth is a
# miss, which falls out of the "not actionable vs actionable = FN" rule below.
ACTIONABLE = frozenset({"missing", "incomplete", "outdated", "contradictory", "findability", "audience"})


class EvaluationError(ValueError):
    """Raised on malformed input or an alignment that does not fit its case."""


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def _index_alignments(cases: list, alignments: list | None) -> dict:
    """Validate each alignment against its case and return case_id -> matches."""
    by_id = {c["id"]: c for c in cases}
    result: dict = {}
    for a in alignments or []:
        cid = a["case_id"]
        if cid not in by_id:
            raise EvaluationError(f"alignment references unknown case {cid!r}")
        if cid in result:
            raise EvaluationError(f"duplicate alignment for case {cid!r}")
        case = by_id[cid]
        if a.get("content_hash") != case.get("content_hash"):
            raise EvaluationError(f"alignment for case {cid!r} has a stale content_hash")
        if a.get("analysis_revision") != case.get("analysis_revision"):
            raise EvaluationError(f"alignment for case {cid!r} has a stale analysis_revision")
        if not str(a.get("reviewed_by") or "").strip():
            raise EvaluationError(f"alignment for case {cid!r} has a blank reviewed_by")
        n_findings = len(case.get("analysis") or [])
        ref = case.get("reference")
        n_questions = len(ref["questions"]) if ref else 0
        seen_findings: set = set()
        seen_questions: set = set()
        for m in a.get("matches") or []:
            fi, ri = m["finding_index"], m["reference_index"]
            if not 0 <= fi < n_findings:
                raise EvaluationError(f"alignment for case {cid!r}: finding_index {fi} out of range")
            if not 0 <= ri < n_questions:
                raise EvaluationError(f"alignment for case {cid!r}: reference_index {ri} out of range")
            if fi in seen_findings or ri in seen_questions:
                raise EvaluationError(f"alignment for case {cid!r}: matches must be one-to-one")
            seen_findings.add(fi)
            seen_questions.add(ri)
        result[cid] = a.get("matches") or []
    return result


def _score_case(findings: list, questions: list, matches: list) -> tuple[int, int, int]:
    tp = fp = fn = 0
    matched_findings: set = set()
    matched_questions: set = set()
    for m in matches:
        fi, ri = m["finding_index"], m["reference_index"]
        matched_findings.add(fi)
        matched_questions.add(ri)
        predicted = findings[fi]["diagnosis"] in ACTIONABLE
        truth = questions[ri]["diagnosis"] in ACTIONABLE
        if predicted and truth:
            tp += 1
        elif predicted:
            fp += 1
        elif truth:
            fn += 1
    fp += sum(1 for i, f in enumerate(findings) if i not in matched_findings and f["diagnosis"] in ACTIONABLE)
    fn += sum(1 for j, q in enumerate(questions) if j not in matched_questions and q["diagnosis"] in ACTIONABLE)
    return tp, fp, fn


def evaluate(cases: list, alignments: list | None = None) -> dict:
    aligned = _index_alignments(cases, alignments)
    statuses: dict = {}
    total_findings = abstentions = fully_reviewed = 0
    unscored: list = []
    per_case: list = []
    agg_tp = agg_fp = agg_fn = 0
    for case in cases:
        cid = case["id"]
        statuses[case["status"]] = statuses.get(case["status"], 0) + 1
        findings = case.get("analysis") or []
        total_findings += len(findings)
        abstentions += sum(1 for f in findings if f["diagnosis"] == "uncertain")
        ref = case.get("reference")
        if ref is None:
            unscored.append({"case_id": cid, "reason": "no reference"})
            continue
        if not str(ref.get("reviewed_by") or "").strip() or not ref.get("reviewed_at"):
            unscored.append({"case_id": cid, "reason": "reference not reviewed"})
            continue
        if not ref.get("complete", False):
            unscored.append({"case_id": cid, "reason": "reference incomplete"})
            continue
        if ref.get("content_hash") != case.get("content_hash"):
            raise EvaluationError(f"case {cid!r}: reviewed reference is stale (content_hash mismatch)")
        fully_reviewed += 1
        questions = ref["questions"]
        matches = aligned.get(cid)
        if findings and questions and matches is None:
            unscored.append({"case_id": cid, "reason": "two-sided case without alignment"})
            continue
        tp, fp, fn = _score_case(findings, questions, matches or [])
        agg_tp, agg_fp, agg_fn = agg_tp + tp, agg_fp + fp, agg_fn + fn
        per_case.append(
            {
                "case_id": cid,
                "true_positives": tp,
                "false_positives": fp,
                "false_negatives": fn,
                "precision": _ratio(tp, tp + fp),
                "recall": _ratio(tp, tp + fn),
            }
        )
    return {
        "source_statuses": statuses,
        "total_cases": len(cases),
        "total_findings": total_findings,
        "abstention_count": abstentions,
        "fully_reference_reviewed": fully_reviewed,
        "scored_cases": len(per_case),
        "unscored": unscored,
        "per_case": per_case,
        "aggregate": {
            "true_positives": agg_tp,
            "false_positives": agg_fp,
            "false_negatives": agg_fn,
            "precision": _ratio(agg_tp, agg_tp + agg_fp),
            "recall": _ratio(agg_tp, agg_tp + agg_fn),
        },
    }


def _load_json(path: str) -> object:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationError(f"cannot read {path}: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline precision/recall for support-gap analysis.")
    parser.add_argument("--input", required=True, help="JSON array of case-detail records")
    parser.add_argument("--alignment", help="optional JSON array of human finding/reference matchings")
    args = parser.parse_args(argv)
    try:
        cases = _load_json(args.input)
        if not isinstance(cases, list):
            raise EvaluationError("input must be a JSON array of case records")
        alignments = _load_json(args.alignment) if args.alignment else None
        if alignments is not None and not isinstance(alignments, list):
            raise EvaluationError("alignment must be a JSON array of match records")
        report = evaluate(cases, alignments)
    except EvaluationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
