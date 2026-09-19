from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from knowledge_ingest.eval.intake_quality import evaluate_suite

_ANSWER = "Refund requests must be filed within thirty calendar days of purchase."
_GOLD_MARKER = "https://kb.example.com/gold/returns"


def _query(qid: str, marker: str = "returns-policy") -> dict[str, Any]:
    return {
        "id": qid,
        "org_zitadel_id": "org-example",
        "query": "Wat is de refundtermijn?",
        "reference_answer": _ANSWER,
        "expected_chunks": [marker],
    }


def _chunk(chunk_id: str, text: str, doc: str = "kb/returns-policy.md") -> dict[str, Any]:
    return {"chunk_id": chunk_id, "text": text, "metadata": {"path": doc, "title": "Returns"}}


def _suite(tmp_path: Path, queries: list[dict[str, Any]]) -> Path:
    path = tmp_path / "suite.yaml"
    path.write_text(
        yaml.safe_dump({"suite": "intake", "description": "tmp", "queries": queries}),
        encoding="utf-8",
    )
    return path


def test_split_child_answer_is_covered_by_returned_parent(tmp_path: Path) -> None:
    half = len(_ANSWER) // 2
    report = evaluate_suite(
        _suite(tmp_path, [_query("q1")]),
        {
            "q1": {
                "source_text": _ANSWER,
                "indexed_chunks": [_chunk("c1", _ANSWER[:half]), _chunk("c2", _ANSWER[half:])],
                "retrieved_chunks": [_chunk("p1", _ANSWER)],
            }
        },
    )

    assert report["queries"] == 1
    assert report["answer_in_one_indexed_chunk"]["count"] == 0
    assert report["answer_in_returned_context"]["count"] == 1


def test_identical_quote_on_other_source_does_not_count(tmp_path: Path) -> None:
    gold = _chunk("c-gold", _ANSWER, doc=_GOLD_MARKER)
    other = _chunk("c-other", _ANSWER, doc="https://kb.example.com/other/faq")
    report = evaluate_suite(
        _suite(tmp_path, [_query("q-gold", _GOLD_MARKER), _query("q-mis", _GOLD_MARKER)]),
        {
            "q-gold": {
                "source_text": _ANSWER,
                "indexed_chunks": [gold],
                "retrieved_chunks": [gold],
            },
            "q-mis": {
                "source_text": _ANSWER,
                "indexed_chunks": [other],
                "retrieved_chunks": [other],
            },
        },
    )

    assert report["queries"] == 2
    assert report["answer_in_one_indexed_chunk"]["count"] == 1
    assert report["answer_in_returned_context"]["count"] == 1
    assert report["answer_chunk_retrieved"]["count"] == 1
    assert report["answer_chunk_retrieved"]["fraction"] == 0.5


def test_reference_span_missing_from_source_is_rejected(tmp_path: Path) -> None:
    snapshot = {
        "q1": {
            "source_text": "Onze winkel is geopend van maandag tot zaterdag.",
            "indexed_chunks": [_chunk("c1", _ANSWER)],
            "retrieved_chunks": [],
        }
    }

    with pytest.raises(ValueError) as excinfo:
        evaluate_suite(_suite(tmp_path, [_query("q1")]), snapshot)

    assert _ANSWER not in str(excinfo.value)
    assert "c1" not in str(excinfo.value)


def test_empty_suite_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        evaluate_suite(_suite(tmp_path, []), {})
