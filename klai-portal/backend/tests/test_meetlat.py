"""The meetlat CLI says which code ran, refuses an overlay that did not load, and classifies rounds the same way every time.

scripts/meetlat.py holds no case data. Everything here is synthetic (fictional
conversation ids, example.com) and every Mongo, database and model call is
replaced.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest
from sqlalchemy import create_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import meetlat
import replay_internal_chat as replay

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_BANNER_MODULES = (
    "app.services.internal_turn",
    "app.services.widget_turn",
    "app.services.retrieval_context",
    "app.services.passage_selection",
    "klai_chat_prompts",
)


def _row(cid="aaaaaaaa", u=0, *, decision="answer", sources=1, text="Open the settings and pick the option.", **extra):
    signals = {"decision": decision, "sources_count": sources, **extra.pop("signals", {})}
    return {
        "cid": cid,
        "k": 2 * u,
        "u": u,
        "mode": "open",
        "ask": "q",
        "text": text,
        "signals": signals,
        "total_ms": 1000,
        **extra,
    }


def _write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def _point_modules_at(monkeypatch, directory: Path, *, except_for: str | None = None, elsewhere: Path | None = None):
    directory.mkdir(parents=True, exist_ok=True)
    for name in _BANNER_MODULES:
        target = (elsewhere or directory) if name == except_for else directory
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{name}.py").write_text("# candidate code\n")
        monkeypatch.setattr(importlib.import_module(name), "__file__", str(target / f"{name}.py"))


# --- overlay guard ---------------------------------------------------------------------------------------------


def test_overlay_guard_passes_when_every_module_loads_from_inside_the_overlay(monkeypatch, tmp_path):
    overlay = tmp_path / "ov"
    _point_modules_at(monkeypatch, overlay)

    meetlat.check_overlay(meetlat.code_under_test(), overlay)


def test_overlay_guard_refuses_when_one_module_loads_from_outside_the_overlay(monkeypatch, tmp_path):
    overlay = tmp_path / "ov"
    _point_modules_at(monkeypatch, overlay, except_for="app.services.passage_selection", elsewhere=tmp_path / "main")

    with pytest.raises(meetlat.OverlayError) as refused:
        meetlat.check_overlay(meetlat.code_under_test(), overlay)

    assert "app.services.passage_selection" in str(refused.value)
    assert "app.services.internal_turn" not in str(refused.value)


def test_overlay_guard_does_not_accept_a_sibling_directory_with_the_same_prefix(monkeypatch, tmp_path):
    _point_modules_at(monkeypatch, tmp_path / "ov-old")

    with pytest.raises(meetlat.OverlayError):
        meetlat.check_overlay(meetlat.code_under_test(), tmp_path / "ov")


def test_run_prints_the_banner_and_exits_2_before_any_turn_when_the_overlay_did_not_load(monkeypatch, tmp_path, capsys):
    _point_modules_at(monkeypatch, tmp_path / "main")
    (tmp_path / "ov").mkdir()
    cases = _write_jsonl(tmp_path / "cases.jsonl", [{"cid": "aaaaaaaa", "turn": 0}])
    called = []
    monkeypatch.setattr(replay, "_load_org", lambda *_: called.append("org"))

    code = meetlat.main(
        ["run", "--surface", "internal", "--org", "brightwater", "--cases", str(cases), "--out", str(tmp_path / "r.jsonl"),
         "--overlay", str(tmp_path / "ov")]
    )  # fmt: skip

    captured = capsys.readouterr()
    assert code == 2
    assert called == []
    assert str(tmp_path / "main" / "app.services.internal_turn.py") in captured.out
    assert "refus" in captured.err.lower()
    assert not (tmp_path / "r.jsonl").exists()


def test_widget_surface_exits_2_and_says_it_is_not_built(tmp_path, capsys):
    cases = _write_jsonl(tmp_path / "cases.jsonl", [{"cid": "aaaaaaaa", "turn": 0}])

    code = meetlat.main(
        [
            "run",
            "--surface",
            "widget",
            "--org",
            "brightwater",
            "--cases",
            str(cases),
            "--out",
            str(tmp_path / "r.jsonl"),
        ]
    )

    assert code == 2
    assert "widget" in capsys.readouterr().err


# --- run -------------------------------------------------------------------------------------------------------


def _stub_run_environment(monkeypatch, tmp_path, threads, answered):
    overlay = tmp_path / "ov"
    _point_modules_at(monkeypatch, overlay)
    org = type("Org", (), {"id": 7, "slug": "brightwater", "zitadel_org_id": "zorg"})()

    async def load_org(slug):
        return org

    async def fetch_threads(_org, prefixes, _days):
        return {p: threads[p] for p in prefixes if p in threads}

    async def new_answer(_org, _profile, messages):
        answered.append(messages[-1]["content"])
        return {
            "text": f"answer to {messages[-1]['content']}",
            "ttft_ms": 5,
            "total_ms": 1200,
            "signals": {"decision": "answer", "sources_count": 2},
        }

    monkeypatch.setattr(replay, "_load_org", load_org)
    monkeypatch.setattr(replay, "_new_answer", new_answer)
    monkeypatch.setattr(replay, "_route_logs_to", lambda _path: None)
    monkeypatch.setattr(meetlat, "_fetch_threads", fetch_threads)
    monkeypatch.setattr(meetlat, "_apply_measurement_patches", lambda: None)
    return overlay


def _thread(full_id: str, asks: list[str]) -> meetlat.Thread:
    turns = []
    for ask in asks:
        turns += [{"role": "user", "content": ask}, {"role": "assistant", "content": f"earlier reply to {ask}"}]
    return meetlat.Thread(full_id, object(), "open", turns)


def _run(tmp_path, cases_rows, overlay, *extra):
    cases = _write_jsonl(tmp_path / "cases.jsonl", cases_rows)
    return meetlat.main(
        ["run", "--surface", "internal", "--org", "brightwater", "--cases", str(cases), "--out", str(tmp_path / "r.jsonl"),
         "--overlay", str(overlay), *extra]
    )  # fmt: skip


def test_run_replays_each_named_user_turn_with_its_history_and_writes_one_row_per_turn(monkeypatch, tmp_path, capsys):
    answered: list[str] = []
    threads = {"aaaaaaaa": _thread("aaaaaaaa-1111", ["first question", "x" * 500, "third"])}
    overlay = _stub_run_environment(monkeypatch, tmp_path, threads, answered)

    code = _run(tmp_path, [{"cid": "aaaaaaaa", "turn": 1}, {"cid": "aaaaaaaa", "turn": 2}], overlay)

    rows = [json.loads(line) for line in (tmp_path / "r.jsonl").read_text().splitlines()]
    assert code == 0
    assert [(r["cid"], r["u"], r["k"]) for r in rows] == [("aaaaaaaa", 1, 2), ("aaaaaaaa", 2, 4)]
    assert rows[0]["ask"] == "x" * 400
    assert rows[1]["text"] == "answer to third"
    assert rows[1]["signals"] == {"decision": "answer", "sources_count": 2}
    assert rows[1]["mode"] == "open"
    assert rows[1]["total_ms"] == 1200
    meta = json.loads((tmp_path / "r.jsonl.meta.json").read_text())
    assert next(iter(meta)) == "banner"
    assert meta["cases"] == 2
    assert meta["org"] == "brightwater"
    assert meta["modules"]["app.services.internal_turn"].startswith(str(overlay))
    assert "app.services.internal_turn" in capsys.readouterr().out


def test_run_resumes_rows_already_in_the_output_and_replays_only_the_rest(monkeypatch, tmp_path):
    answered: list[str] = []
    threads = {
        "aaaaaaaa": _thread("aaaaaaaa-1111", ["first", "second"]),
        "bbbbbbbb": _thread("bbbbbbbb-2222", ["other"]),
    }
    overlay = _stub_run_environment(monkeypatch, tmp_path, threads, answered)
    cases = [{"cid": "aaaaaaaa", "turn": 0}, {"cid": "aaaaaaaa", "turn": 1}, {"cid": "bbbbbbbb", "turn": 0}]
    assert _run(tmp_path, cases[:1], overlay) == 0
    first_row = (tmp_path / "r.jsonl").read_text()
    answered.clear()

    code = _run(tmp_path, cases, overlay)

    rows = [json.loads(line) for line in (tmp_path / "r.jsonl").read_text().splitlines()]
    assert code == 0
    assert answered == ["second", "other"]
    assert (tmp_path / "r.jsonl").read_text().startswith(first_row)
    assert [(r["cid"], r["u"]) for r in rows] == [("aaaaaaaa", 0), ("aaaaaaaa", 1), ("bbbbbbbb", 0)]


def test_run_records_an_error_row_for_a_conversation_that_cannot_be_found_and_goes_on(monkeypatch, tmp_path):
    answered: list[str] = []
    overlay = _stub_run_environment(monkeypatch, tmp_path, {"bbbbbbbb": _thread("bbbbbbbb-2", ["other"])}, answered)

    code = _run(tmp_path, [{"cid": "aaaaaaaa", "turn": 0}, {"cid": "bbbbbbbb", "turn": 0}], overlay)

    rows = [json.loads(line) for line in (tmp_path / "r.jsonl").read_text().splitlines()]
    assert code == 0
    assert rows[0]["cid"] == "aaaaaaaa"
    assert rows[0]["error"]
    assert rows[1]["text"] == "answer to other"


def test_run_refuses_to_resume_rows_that_another_code_tree_produced(monkeypatch, tmp_path, capsys):
    answered: list[str] = []
    overlay = _stub_run_environment(monkeypatch, tmp_path, {"aaaaaaaa": _thread("aaaaaaaa-1", ["first"])}, answered)
    assert _run(tmp_path, [{"cid": "aaaaaaaa", "turn": 0}], overlay) == 0
    _point_modules_at(monkeypatch, tmp_path / "ov2")

    code = _run(tmp_path, [{"cid": "aaaaaaaa", "turn": 0}], tmp_path / "ov2")

    assert code == 2
    assert "other code" in capsys.readouterr().err


def test_run_refuses_to_resume_when_a_module_file_changed_under_the_same_path(monkeypatch, tmp_path, capsys):
    answered: list[str] = []
    overlay = _stub_run_environment(monkeypatch, tmp_path, {"aaaaaaaa": _thread("aaaaaaaa-1", ["first"])}, answered)
    assert _run(tmp_path, [{"cid": "aaaaaaaa", "turn": 0}], overlay) == 0
    meta = json.loads((tmp_path / "r.jsonl.meta.json").read_text())
    assert set(meta["module_hashes"]) == set(_BANNER_MODULES)
    assert all(len(h) == 12 for h in meta["module_hashes"].values())
    (overlay / "app.services.internal_turn.py").write_text("# replaced after the first rows\n")

    code = _run(tmp_path, [{"cid": "aaaaaaaa", "turn": 0}, {"cid": "aaaaaaaa", "turn": 1}], overlay)

    assert code == 2
    assert "other code" in capsys.readouterr().err


def test_a_negative_turn_in_the_cases_file_is_refused(tmp_path, capsys):
    code = _compare(tmp_path, [{"cid": "aaaaaaaa", "turn": -1, "class": "weigering"}], [[_row()]])

    assert code == 2
    assert "turn" in capsys.readouterr().err


# --- compare ---------------------------------------------------------------------------------------------------


def _compare(tmp_path, cases, rounds, *flags):
    case_path = _write_jsonl(tmp_path / "cases.jsonl", cases)
    paths = [str(_write_jsonl(tmp_path / f"round{i}.jsonl", rows)) for i, rows in enumerate(rounds, 1)]
    return meetlat.main(["compare", "--cases", str(case_path), *flags, *paths])


def _json_report(tmp_path, capsys, cases, rounds):
    _compare(tmp_path, cases, rounds, "--json")
    return json.loads(capsys.readouterr().out)


def _verdict_of(report, klass, round_index=0):
    return report["rounds"][round_index]["verdicts"][klass]


@pytest.mark.parametrize(
    ("row", "ok"),
    [
        (_row(decision="refusal", sources=0, text="Not found in the knowledge base."), True),
        (_row(decision="answer"), False),
    ],
)
def test_weigering_is_a_refusal_decision(tmp_path, capsys, row, ok):
    report = _json_report(tmp_path, capsys, [{"cid": "aaaaaaaa", "turn": 0, "class": "weigering"}], [[row]])

    assert _verdict_of(report, "weigering") == {"ok": int(ok), "of": 1}


@pytest.mark.parametrize(
    ("row", "ok"),
    [
        (_row(decision="answer", sources=2), True),
        (_row(decision="partial_answer", sources=1), True),
        (_row(decision="answer", sources=0), False),
        (_row(decision="refusal", sources=2), False),
        (_row(decision="answer", sources=2, text="Which phone do you use?"), False),
        (_row(decision="answer", sources=2, text="Details. " * 40 + "Which phone do you use?"), True),
    ],
    ids=["answer", "partial", "no-sources", "refusal", "short-question", "question-longer-than-the-short-limit"],
)
def test_antwoord_kb_needs_sources_and_must_not_be_a_short_question(tmp_path, capsys, row, ok):
    report = _json_report(tmp_path, capsys, [{"cid": "aaaaaaaa", "turn": 0, "class": "antwoord_kb"}], [[row]])

    assert _verdict_of(report, "antwoord_kb") == {"ok": int(ok), "of": 1}


@pytest.mark.parametrize(
    ("text", "variants", "ok"),
    [
        ("On Android tap Settings. On iPhone open Preferences.", None, True),
        ("On Android tap Settings. On iOS open Preferences.", None, True),
        ("Only Android is described here.", None, False),
        ("This works in all scenarios on Android.", None, False),
        ("The desktop app and the web app both do it.", ["desktop", "web"], True),
        ("The desktop app does it.", ["desktop", "web"], False),
    ],
    ids=["both", "ios-alias", "one-platform", "ios-inside-a-word", "custom-variants", "custom-one-missing"],
)
def test_dekkend_antwoord_names_every_variant_of_the_case(tmp_path, capsys, text, variants, ok):
    case = {"cid": "aaaaaaaa", "turn": 0, "class": "dekkend_antwoord"}
    if variants:
        case["variants"] = variants

    report = _json_report(tmp_path, capsys, [case], [[_row(text=text)]])

    assert _verdict_of(report, "dekkend_antwoord") == {"ok": int(ok), "of": 1}


@pytest.mark.parametrize(
    ("row", "ok"),
    [
        (_row(decision="general_knowledge", sources=0), True),
        (_row(decision="general_knowledge", sources=0, text=""), False),
        (_row(decision="answer", sources=0, text="Restart the router."), False),
        (_row(decision="answer", sources=2, text="Uit algemene kennis: start de router opnieuw."), True),
        (_row(decision="answer", sources=2, text="Based on general knowledge, restart it."), True),
        (_row(decision="answer", sources=2, text="Restart the router."), False),
        (_row(decision="refusal", sources=0, text="Not found."), False),
    ],
    ids=[
        "decision",
        "decision-without-text",
        "unlabelled-answer-without-sources",
        "dutch-phrase",
        "english-phrase",
        "grounded",
        "refusal",
    ],
)
def test_antwoord_algemeen_gelabeld_needs_a_general_knowledge_label(tmp_path, capsys, row, ok):
    case = {"cid": "aaaaaaaa", "turn": 0, "class": "antwoord_algemeen_gelabeld"}

    report = _json_report(tmp_path, capsys, [case], [[row]])

    assert _verdict_of(report, "antwoord_algemeen_gelabeld") == {"ok": int(ok), "of": 1}


@pytest.mark.parametrize("klass", ["weigering", "antwoord_kb", "dekkend_antwoord", "antwoord_algemeen_gelabeld"])
@pytest.mark.parametrize("kind", ["empty", "error"])
def test_empty_text_and_error_rows_fail_every_automatic_class(tmp_path, capsys, klass, kind):
    row = (
        _row(decision="refusal", text="  ") if kind == "empty" else {"cid": "aaaaaaaa", "u": 0, "error": "TimeoutError"}
    )
    if kind == "empty" and klass != "weigering":
        row = _row(decision="general_knowledge", text="")

    report = _json_report(tmp_path, capsys, [{"cid": "aaaaaaaa", "turn": 0, "class": klass}], [[row]])

    assert _verdict_of(report, klass) == {"ok": 0, "of": 1}


def test_rounds_with_the_same_file_name_in_different_directories_stay_separate(tmp_path, capsys):
    cases = [{"cid": "aaaaaaaa", "turn": 0, "class": "weigering"}]
    paths = []
    for directory, decision in (("main", "answer"), ("candidate", "refusal")):
        (tmp_path / directory).mkdir()
        paths.append(str(_write_jsonl(tmp_path / directory / "round.jsonl", [_row(decision=decision)])))
    case_path = _write_jsonl(tmp_path / "cases.jsonl", cases)

    assert meetlat.main(["compare", "--cases", str(case_path), "--json", *paths]) == 0

    report = json.loads(capsys.readouterr().out)
    assert [r["round"] for r in report["rounds"]] == paths
    assert [r["verdicts"]["weigering"]["ok"] for r in report["rounds"]] == [0, 1]
    assert meetlat.main(["compare", "--cases", str(case_path), paths[0], paths[0]]) == 2


def test_weigering_feiten_gets_no_automatic_verdict_and_is_counted_for_reading(tmp_path, capsys):
    cases = [
        {"cid": "aaaaaaaa", "turn": 0, "class": "weigering_feiten"},
        {"cid": "aaaaaaaa", "turn": 1, "class": "weigering"},
    ]
    rows = [_row(u=0, decision="answer"), _row(u=1, decision="refusal", sources=0)]

    report = _json_report(tmp_path, capsys, cases, [rows])

    summary = report["rounds"][0]
    assert "weigering_feiten" not in summary["verdicts"]
    assert summary["to_read"] == 1
    assert summary["total"] == {"ok": 1, "of": 1}


def test_a_case_without_a_row_counts_as_missing_not_as_ok(tmp_path, capsys):
    cases = [{"cid": "aaaaaaaa", "turn": 0, "class": "weigering"}, {"cid": "aaaaaaaa", "turn": 1, "class": "weigering"}]

    report = _json_report(tmp_path, capsys, cases, [[_row(u=0, decision="refusal", sources=0)]])

    assert _verdict_of(report, "weigering") == {"ok": 1, "of": 2}


def test_compare_summarises_decisions_unsupported_hard_facts_latency_errors_and_selection(tmp_path, capsys):
    cases = [{"cid": "aaaaaaaa", "turn": 0, "class": "antwoord_kb"}]
    rows = [
        _row(u=0, decision="answer", total_ms=1000, signals={"unsupported": 2, "hard_facts_outside_kb": {"url": 1, "phone": 2}, "selection": {"verdict": "found"}}),
        _row(u=1, decision="refusal", sources=0, total_ms=3000, signals={"unsupported": 0, "selection": {"failed": "no_verdict_after_retry"}}),
        _row(u=2, decision="answer", total_ms=2000, signals={"unsupported": 1}),
        {"cid": "aaaaaaaa", "u": 3, "error": "TimeoutError"},
        _row(u=4, text="", total_ms=2000),
    ]  # fmt: skip
    report = _json_report(tmp_path, capsys, cases, [rows])

    summary = report["rounds"][0]
    assert summary["decisions"] == {"answer": 3, "refusal": 1}
    assert summary["unsupported"] == {"sum": 3, "answers_with": 2, "of": 3}
    assert summary["hard_facts"] == {"answers": 1, "facts": 3}
    assert summary["median_total_ms"] == 2000
    assert summary["errors_or_empty"] == 2
    assert summary["selection"] == {"found": 1, "failed": 1}


def test_compare_json_round_trips_the_same_report_the_text_form_prints(tmp_path, capsys):
    cases = [
        {"cid": "aaaaaaaa", "turn": 0, "class": "weigering_feiten"},
        {"cid": "bbbbbbbb", "turn": 0, "class": "weigering"},
    ]
    rounds = [
        [_row("aaaaaaaa", 0, text="Mail info@example.com or see https://example.com/help, it costs €12,50."), _row("bbbbbbbb", 0, decision="refusal", sources=0)],
        [_row("aaaaaaaa", 0, decision="refusal", sources=0, text="Not found."), _row("bbbbbbbb", 0)],
    ]  # fmt: skip

    report = _json_report(tmp_path, capsys, cases, rounds)

    assert json.loads(json.dumps(report)) == report
    assert [r["round"] for r in report["rounds"]] == [str(tmp_path / "round1.jsonl"), str(tmp_path / "round2.jsonl")]
    reading = report["reading"][0]
    assert (reading["cid"], reading["turn"], reading["class"]) == ("aaaaaaaa", 0, "weigering_feiten")
    first = reading["rounds"][0]
    assert first["decision"] == "answer"
    assert first["contacts"]["emails"] == ["info@example.com"]
    assert first["contacts"]["urls"] == ["https://example.com/help"]
    assert first["contacts"]["amounts"] == ["€12,50"]
    assert reading["rounds"][1]["decision"] == "refusal"

    _compare(tmp_path, cases, rounds)
    text = capsys.readouterr().out
    assert f"{tmp_path / 'round1.jsonl'}:" in text
    assert "weigering 1/1" in text
    assert "info@example.com" in text


def test_reading_text_is_cut_at_260_characters(tmp_path, capsys):
    cases = [{"cid": "aaaaaaaa", "turn": 0, "class": "dekkend_antwoord"}]

    report = _json_report(tmp_path, capsys, cases, [[_row(text="y" * 400)]])

    assert report["reading"][0]["rounds"][0]["text"] == "y" * 260


def test_a_case_without_an_expectation_class_is_refused(tmp_path, capsys):
    code = _compare(tmp_path, [{"cid": "aaaaaaaa", "turn": 0}], [[_row()]])

    assert code == 2
    assert "class" in capsys.readouterr().err


# --- importing the replay script -------------------------------------------------------------------------------


def test_importing_replay_internal_chat_leaves_sys_path_alone(monkeypatch):
    backend_root = str(Path(replay.BACKEND_ROOT))
    monkeypatch.setattr(sys, "path", [p for p in sys.path if p != backend_root])
    before = list(sys.path)

    for name in ("replay_internal_chat", "simulate_conversations"):
        spec = importlib.util.spec_from_file_location(f"{name}_probe", SCRIPTS / f"{name}.py")
        assert spec is not None
        assert spec.loader is not None
        probe = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, probe)  # dataclasses look their module up here
        spec.loader.exec_module(probe)

    assert sys.path == before


def test_measurement_patches_open_every_database_connection_read_only():
    class FakeCursor:
        def __init__(self):
            self.statements = []

        def execute(self, statement):
            self.statements.append(statement)

        def close(self):
            pass

    class FakeConnection:
        cursor_ = FakeCursor()

        def cursor(self):
            return self.cursor_

    engine = create_engine("sqlite://")

    before = list(engine.pool.dispatch.connect)

    meetlat._make_database_read_only(types.SimpleNamespace(sync_engine=engine))

    (listener,) = [fn for fn in engine.pool.dispatch.connect if fn not in before]
    connection = FakeConnection()
    listener(connection, None)
    assert connection.cursor_.statements == ["SET default_transaction_read_only = on"]


def test_rows_may_sit_next_to_an_overlay_but_not_inside_a_checkout(tmp_path, monkeypatch):
    """On the production host the overlay sits under /out with no .git; the
    guard protects a checkout with a public remote, not every parent directory."""
    import replay_internal_chat as replay

    monkeypatch.setattr(replay, "REPO_ROOT", tmp_path)
    assert not meetlat._rows_inside_checkout(tmp_path / "rows.jsonl")
    (tmp_path / ".git").mkdir()
    assert meetlat._rows_inside_checkout(tmp_path / "rows.jsonl")
    assert not meetlat._rows_inside_checkout(tmp_path.parent / "elsewhere.jsonl")
