"""The clarify-gate labelling script scores correctly and never prints a question.

scripts/clarify_gate_eval.py reads real questions. Everything here is synthetic
(fictional product "Alpha phone app", example.com).

synthetic-data: generator=hand-written seed=0
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest
import respx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import clarify_gate_eval as evaluation


def _labelled(
    row_id: str, fired: bool, label: str, *, weak: bool = False, axis: str = "", label_axis: str = ""
) -> dict:
    return {
        "id": row_id,
        "question": f"question {row_id}",
        "weak": weak,
        "fired": fired,
        "axis": axis or None,
        "label_should_ask": label,
        "label_axis": label_axis,
    }


def test_score_counts_precision_recall_weak_fires_and_axis_matches():
    rows = [
        _labelled("1", True, "y", axis="device", label_axis="device"),
        _labelled("2", True, "y", axis="edition", label_axis="device"),
        _labelled("3", True, "n", weak=True),
        _labelled("4", False, "y"),
        _labelled("5", False, "n", weak=True),
        _labelled("6", True, ""),
    ]

    result = evaluation.score(rows, evaluation.owner_truth(rows))

    assert (result["true_positive"], result["false_positive"], result["false_negative"]) == (2, 1, 1)
    assert result["precision"] == pytest.approx(2 / 3)
    assert result["recall"] == pytest.approx(2 / 3)
    assert (result["weak_fired"], result["weak"]) == (1, 2)
    assert (result["axis_matched"], result["axis_labelled"]) == (1, 2)
    assert result["judged"] == 5


def test_reviewed_cases_count_ask_and_options_as_ask_and_leave_either_out():
    rows = [_labelled(str(i), fired, "") for i, fired in enumerate([True, False, True, True])]
    cases = [
        {"question": "question 0", "kind": "ask"},
        {"question": "question 1", "kind": "options"},
        {"question": "question 2", "kind": "direct"},
        {"question": "question 3", "kind": "either"},
    ]

    assert evaluation.case_truth(rows, cases) == {"0": True, "1": True, "2": False}


def test_the_gate_mode_prints_counts_and_writes_the_questions_outside_the_repository(tmp_path, monkeypatch, capsys):
    question = "I can't call with the Alpha phone app"
    replay = tmp_path / "replay.jsonl"
    replay.write_text(
        json.dumps({"id": "q1", "source": "widget", "top": 0.9, "titles": ["Alpha app iPhone", "Alpha app Android"]})
        + "\n"
    )
    pool = tmp_path / "pool.json"
    pool.write_text(json.dumps([{"id": "q1", "question": question, "history": []}]))
    monkeypatch.setenv("KLAI_CLARIFY_OUT", str(tmp_path / "out"))

    evaluation.main(["clarify_gate_eval.py", "gate", str(replay), str(pool)])

    printed = capsys.readouterr().out
    assert "questions 1, gate fired 1/1" in printed
    assert question not in printed and "iPhone" not in printed
    (row,) = [json.loads(line) for line in (tmp_path / "out" / "labels.jsonl").read_text().splitlines()]
    assert (row["question"], row["options"]) == (question, ["iPhone", "Android"])


async def test_the_sample_retrieval_is_a_background_call_and_is_cached(tmp_path, monkeypatch):
    """An evaluation run is not a tenant's question: without ``purpose`` every
    sampled question counted as a knowledge query in the tenant's usage."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "knowledge_retrieve_url", "http://retrieval.example.com")
    pack = {"items": [{"title": "Alpha app iPhone", "reranker_score": 0.9}]}
    with respx.mock() as router:
        route = router.post("http://retrieval.example.com/retrieve").mock(
            return_value=httpx.Response(200, json={"evidence_pack": pack})
        )
        first = await evaluation._evidence_pack(tmp_path, "w-1", "I can't call", "zorg-acme", ["help"], 8)
        second = await evaluation._evidence_pack(tmp_path, "w-1", "I can't call", "zorg-acme", ["help"], 8)

    assert first == second == pack
    assert route.call_count == 1
    body = json.loads(route.calls[0].request.content)
    assert (body["purpose"], body["telemetry_level"]) == ("background", "off")


def test_the_gate_mode_rescores_a_sample_folder_from_its_cached_packs_and_keeps_the_labels(
    tmp_path, monkeypatch, capsys
):
    sample = tmp_path / "sample"
    (sample / "packs").mkdir(parents=True)
    labelled = {"id": "w-1", "surface": "widget", "question": "I can't call", "context": [], "label_should_ask": "y"}
    (sample / "labels.jsonl").write_text(json.dumps(labelled) + "\n")
    items = [
        {
            "title": f"Alpha phone app for {platform} troubleshooter",
            "heading_path": "Troubleshooter > I can't call",
            "source_url": f"https://help.example.com/{platform.lower()}",
            "reranker_score": score,
        }
        for platform, score in (("iPhone", 0.9), ("Android", 0.8))
    ]
    (sample / "packs" / "w-1.json").write_text(json.dumps({"items": items}))
    monkeypatch.setenv("KLAI_CLARIFY_OUT", str(tmp_path / "out"))

    evaluation.main(["clarify_gate_eval.py", "gate", str(sample)])

    assert "questions 1, gate fired 1/1" in capsys.readouterr().out
    (row,) = [json.loads(line) for line in (tmp_path / "out" / "labels.jsonl").read_text().splitlines()]
    assert (row["options"], row["top"], row["label_should_ask"]) == (["iPhone", "Android"], 0.9, "y")
