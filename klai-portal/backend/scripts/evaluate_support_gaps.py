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
    python scripts/evaluate_support_gaps.py --input first.json --repeat-input second.json \
        --kb-snapshot snapshot-1 --repeat-kb-snapshot snapshot-1

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

_SOURCE_TO_CHANNEL = {"audio": "phone", "hubspot": "hubspot"}
_EXPLICIT_CHANNELS = frozenset({"phone", "hubspot", "librechat", "webchat"})
_CHANNEL_UNKNOWN = "unknown"


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


def _source_of(case: dict) -> str | None:
    payload = case.get("payload")
    source = payload.get("source") if isinstance(payload, dict) else None
    return source or None


def _resolve_channel(case: dict) -> str:
    """Map a case to one stable channel key, failing loudly on a source/channel conflict."""
    from_source = _SOURCE_TO_CHANNEL.get(_source_of(case) or "")
    explicit = case.get("channel")
    from_explicit = explicit if explicit in _EXPLICIT_CHANNELS else None
    if from_source and from_explicit and from_source != from_explicit:
        raise EvaluationError(
            f"case {case['id']!r}: explicit channel {explicit!r} conflicts with source-derived {from_source!r}"
        )
    return from_source or from_explicit or _CHANNEL_UNKNOWN


def _new_channel_bucket() -> dict:
    return {
        "total": 0,
        "scored": 0,
        "fully_reference_reviewed": 0,
        "abstention_count": 0,
        "source_status": {},
        "tp": 0,
        "fp": 0,
        "fn": 0,
    }


def _new_repeatability_bucket() -> dict:
    return {
        "first_findings": 0,
        "repeat_findings": 0,
        "stable": 0,
        "changed": 0,
        "diagnosis_changed": 0,
        "evidence_changed": 0,
        "missing_from_repeat": 0,
        "new_in_repeat": 0,
    }


def _repeatability_result(bucket: dict) -> dict:
    compared = bucket["stable"] + bucket["changed"] + bucket["missing_from_repeat"] + bucket["new_in_repeat"]
    return {**bucket, "exact_repeatability": _ratio(bucket["stable"], compared)}


def _finding_identity(finding: dict, *, case_id: object) -> str:
    question = finding.get("question")
    if not isinstance(question, str) or not question.strip():
        raise EvaluationError(f"case {case_id!r}: finding has no question identity")
    return " ".join(question.split()).casefold()


def _index_findings(case: dict) -> dict[str, dict]:
    findings = case.get("analysis")
    if case.get("status") in {"failed", "pending"} or not isinstance(findings, list):
        raise EvaluationError(f"case {case['id']!r}: analysis unavailable for repeatability")
    indexed: dict[str, dict] = {}
    for finding in findings:
        identity = _finding_identity(finding, case_id=case["id"])
        if identity in indexed:
            raise EvaluationError(f"case {case['id']!r}: duplicate normalized question identity")
        indexed[identity] = finding
    return indexed


def _evidence_signature(finding: dict) -> str:
    evidence = {key: value for key, value in finding.items() if key not in {"question", "diagnosis", "review"}}
    return json.dumps(evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _compare_case(first: dict, repeat: dict) -> dict:
    first_findings = _index_findings(first)
    repeat_findings = _index_findings(repeat)
    bucket = _new_repeatability_bucket()
    bucket["first_findings"] = len(first_findings)
    bucket["repeat_findings"] = len(repeat_findings)
    for identity in first_findings.keys() & repeat_findings.keys():
        before, after = first_findings[identity], repeat_findings[identity]
        diagnosis_changed = before.get("diagnosis") != after.get("diagnosis")
        evidence_changed = _evidence_signature(before) != _evidence_signature(after)
        if diagnosis_changed or evidence_changed:
            bucket["changed"] += 1
            bucket["diagnosis_changed"] += diagnosis_changed
            bucket["evidence_changed"] += evidence_changed
        else:
            bucket["stable"] += 1
    bucket["missing_from_repeat"] = len(first_findings.keys() - repeat_findings.keys())
    bucket["new_in_repeat"] = len(repeat_findings.keys() - first_findings.keys())
    return bucket


def evaluate_repeatability(first: list, repeat: list, first_kb_snapshot: str, repeat_kb_snapshot: str) -> dict:
    if not first_kb_snapshot.strip() or first_kb_snapshot != repeat_kb_snapshot:
        raise EvaluationError("repeatability requires the same non-blank KB snapshot identity")
    first_by_id = {case["id"]: case for case in first}
    repeat_by_id = {case["id"]: case for case in repeat}
    if len(first_by_id) != len(first) or len(repeat_by_id) != len(repeat):
        raise EvaluationError("repeatability inputs contain duplicate case ids")
    if first_by_id.keys() != repeat_by_id.keys():
        raise EvaluationError("repeatability inputs contain different case ids")

    aggregate = _new_repeatability_bucket()
    channels: dict[str, dict] = {}
    for case_id, before in first_by_id.items():
        after = repeat_by_id[case_id]
        if not before.get("content_hash") or before.get("content_hash") != after.get("content_hash"):
            raise EvaluationError(f"case {case_id!r}: content_hash mismatch")
        channel = _resolve_channel(before)
        if channel != _resolve_channel(after):
            raise EvaluationError(f"case {case_id!r}: channel mismatch")
        comparison = _compare_case(before, after)
        channel_bucket = channels.setdefault(channel, _new_repeatability_bucket())
        for key, value in comparison.items():
            aggregate[key] += value
            channel_bucket[key] += value
    return {
        "measurement": "model_output_repeatability_not_accuracy",
        "kb_snapshot": first_kb_snapshot,
        "matching": "casefolded, whitespace-collapsed question; wording changes count as missing and new",
        "total_cases": len(first),
        "aggregate": _repeatability_result(aggregate),
        "by_channel": {channel: _repeatability_result(bucket) for channel, bucket in channels.items()},
    }


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
    channels: dict = {}
    agg_tp = agg_fp = agg_fn = 0
    for case in cases:
        cid = case["id"]
        statuses[case["status"]] = statuses.get(case["status"], 0) + 1
        findings = case.get("analysis") or []
        total_findings += len(findings)
        case_abstentions = sum(1 for f in findings if f["diagnosis"] == "uncertain")
        abstentions += case_abstentions
        bucket = channels.setdefault(_resolve_channel(case), _new_channel_bucket())
        bucket["total"] += 1
        bucket["abstention_count"] += case_abstentions
        status_key = case["status"]
        bucket["source_status"][status_key] = bucket["source_status"].get(status_key, 0) + 1
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
        bucket["fully_reference_reviewed"] += 1
        questions = ref["questions"]
        matches = aligned.get(cid)
        if findings and questions and matches is None:
            unscored.append({"case_id": cid, "reason": "two-sided case without alignment"})
            continue
        tp, fp, fn = _score_case(findings, questions, matches or [])
        agg_tp, agg_fp, agg_fn = agg_tp + tp, agg_fp + fp, agg_fn + fn
        bucket["scored"] += 1
        bucket["tp"] += tp
        bucket["fp"] += fp
        bucket["fn"] += fn
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
        "by_channel": {
            channel: {
                "total": b["total"],
                "scored": b["scored"],
                "fully_reference_reviewed": b["fully_reference_reviewed"],
                "abstention_count": b["abstention_count"],
                "source_status": b["source_status"],
                "true_positives": b["tp"],
                "false_positives": b["fp"],
                "false_negatives": b["fn"],
                "precision": _ratio(b["tp"], b["tp"] + b["fp"]),
                "recall": _ratio(b["tp"], b["tp"] + b["fn"]),
            }
            for channel, b in channels.items()
        },
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
    parser.add_argument("--repeat-input", help="second case-detail export for repeatability measurement")
    parser.add_argument("--kb-snapshot", help="explicit KB snapshot identity for --input")
    parser.add_argument("--repeat-kb-snapshot", help="explicit KB snapshot identity for --repeat-input")
    args = parser.parse_args(argv)
    try:
        cases = _load_json(args.input)
        if not isinstance(cases, list):
            raise EvaluationError("input must be a JSON array of case records")
        if args.repeat_input:
            if args.alignment:
                raise EvaluationError("alignment is only valid for precision/recall evaluation")
            if args.kb_snapshot is None or args.repeat_kb_snapshot is None:
                raise EvaluationError("repeatability requires a KB snapshot identity for each input")
            repeat = _load_json(args.repeat_input)
            if not isinstance(repeat, list):
                raise EvaluationError("repeat input must be a JSON array of case records")
            report = evaluate_repeatability(cases, repeat, args.kb_snapshot, args.repeat_kb_snapshot)
        else:
            if args.kb_snapshot is not None or args.repeat_kb_snapshot is not None:
                raise EvaluationError("KB snapshot arguments require --repeat-input")
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
