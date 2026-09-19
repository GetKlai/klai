"""Score a private intake snapshot using the existing RAGAS YAML suite format.

reference_answer must be a literal source span; expected_chunks must contain one
unique document URL/path marker. Chunks use chunk_id, text and RAGAS metadata.
Run with --suite PATH --snapshot PATH. Only aggregate results go to stdout.
This measures answer coverage, not end-to-end answer quality.
"""

import argparse
import json
from pathlib import Path

from knowledge_ingest.eval.ragas_runner import _expected_chunk_canary
from knowledge_ingest.eval.suite_loader import load_suite

_METRICS = ("answer_in_one_indexed_chunk", "answer_in_returned_context", "answer_chunk_retrieved")


def _norm(text: str) -> str:
    return " ".join(text.split())


def _gold_chunks(chunks: object, marker: str) -> list[dict]:
    if not isinstance(chunks, list) or any(
        not isinstance(c, dict)
        or not isinstance(c.get("chunk_id"), str)
        or not c["chunk_id"]
        or not isinstance(c.get("text"), str)
        for c in chunks
    ):
        raise ValueError("Snapshot chunks require string chunk_id and text fields")
    return [c for c in chunks if _expected_chunk_canary([marker], [c])["passed"]]


def evaluate_suite(suite_path: Path, snapshot: dict) -> dict:
    suite = load_suite(suite_path, require_reference_answer=True)
    if not suite.queries or len({q.id for q in suite.queries}) != len(suite.queries):
        raise ValueError("Suite requires nonempty, unique query ids")
    if not isinstance(snapshot, dict):
        raise ValueError("Snapshot must be an object keyed by suite query id")
    counts = dict.fromkeys(_METRICS, 0)
    for query in suite.queries:
        markers = query.expected_chunks
        if len(markers) != 1 or not isinstance(markers[0], str) or not markers[0].strip():
            raise ValueError("Each query requires one document URL/path marker")
        entry = snapshot.get(query.id)
        if not isinstance(entry, dict) or not isinstance(entry.get("source_text"), str):
            raise ValueError("Every query requires a snapshot with source_text")
        reference = _norm(query.reference_answer)
        if reference not in _norm(entry["source_text"]):
            raise ValueError("reference_answer must be a verbatim span of source_text")
        indexed = _gold_chunks(entry.get("indexed_chunks"), markers[0])
        returned = _gold_chunks(entry.get("retrieved_chunks"), markers[0])
        answer_ids = {c["chunk_id"] for c in indexed if reference in _norm(c["text"])}
        counts["answer_in_one_indexed_chunk"] += bool(answer_ids)
        # A returned parent may contain an answer split across indexed children.
        counts["answer_in_returned_context"] += any(reference in _norm(c["text"]) for c in returned)
        counts["answer_chunk_retrieved"] += any(c["chunk_id"] in answer_ids for c in returned)
    total = len(suite.queries)
    return {
        "queries": total,
        **{
            name: {"count": count, "fraction": round(count / total, 4)}
            for name, count in counts.items()
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True, type=Path)
    parser.add_argument("--snapshot", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = evaluate_suite(args.suite, json.loads(args.snapshot.read_text()))
    except (ValueError, KeyError, TypeError):
        # The shared loader can include private query ids in its exceptions.
        raise SystemExit("Invalid intake suite or snapshot; check the private input") from None
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
