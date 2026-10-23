"""The meetlat CLI says which code ran, refuses an overlay that did not load, and classifies rounds the same way every time.

scripts/meetlat.py holds no case data. Everything here is synthetic (fictional
conversation ids, example.com) and every Mongo, database and model call is
replaced.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import json
import sys
import types
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import meetlat
import replay_internal_chat as replay

from app.services.chat_profile import ChatProfile

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


def test_widget_surface_needs_a_widget_file(tmp_path, capsys):
    cases = _write_jsonl(tmp_path / "cases.jsonl", [{"id": "w-1", "messages": [{"role": "user", "content": "hi"}]}])

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
    assert "--widget" in capsys.readouterr().err


# --- run -------------------------------------------------------------------------------------------------------


def _stub_run_environment(monkeypatch, tmp_path, threads, answered):
    overlay = tmp_path / "ov"
    _point_modules_at(monkeypatch, overlay)
    org = type("Org", (), {"id": 7, "slug": "brightwater", "zitadel_org_id": "zorg"})()

    async def load_org(slug):
        return org

    async def fetch_threads(_org, prefixes, _days):
        return {p: threads[p] for p in prefixes if p in threads}

    async def new_answer(_org, _profile, messages, _conversation_id):
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
    return meetlat.Thread(
        full_id, ChatProfile(surface="internal", kb_mode="open", user_id="sub-employee"), "open", turns
    )


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
    # "first" again, for the case memory "second" reads; its row is not written twice.
    assert answered == ["first", "second", "other"]
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


def test_run_replays_only_the_cases_of_its_org_and_those_without_an_org(monkeypatch, tmp_path, capsys):
    answered: list[str] = []
    threads = {
        "aaaaaaaa": _thread("aaaaaaaa-1", ["first"]),
        "bbbbbbbb": _thread("bbbbbbbb-2", ["other org"]),
        "cccccccc": _thread("cccccccc-3", ["no org field"]),
    }
    overlay = _stub_run_environment(monkeypatch, tmp_path, threads, answered)
    cases = [
        {"cid": "aaaaaaaa", "turn": 0, "org": "brightwater"},
        {"cid": "bbbbbbbb", "turn": 0, "org": "otherorg"},
        {"cid": "cccccccc", "turn": 0},
    ]

    code = _run(tmp_path, cases, overlay)

    rows = [json.loads(line) for line in (tmp_path / "r.jsonl").read_text().splitlines()]
    assert code == 0
    assert [r["cid"] for r in rows] == ["aaaaaaaa", "cccccccc"]
    assert answered == ["first", "no org field"]
    assert "2 of 3 cases are for this org" in capsys.readouterr().out
    assert json.loads((tmp_path / "r.jsonl.meta.json").read_text())["cases"] == 2


# --- a restart during the round --------------------------------------------------------------------------------


def _answer(text="answer after the restart", signals=None, stream_error=None):
    return {
        "text": text,
        "ttft_ms": 5,
        "total_ms": 900,
        "signals": signals or {"decision": "answer"},
        "stream_error": stream_error,
    }


def _replay_with_outcomes(monkeypatch, tmp_path, outcomes):
    """One case whose replay attempts give ``outcomes`` in turn (an exception is raised); the row and the pauses."""
    overlay = _stub_run_environment(monkeypatch, tmp_path, {"aaaaaaaa": _thread("aaaaaaaa-1", ["first"])}, [])
    remaining = list(outcomes)
    pauses: list[float] = []

    async def new_answer(_org, _profile, _messages, _conversation_id):
        outcome = remaining.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def sleep(seconds):
        pauses.append(seconds)

    monkeypatch.setattr(replay, "_new_answer", new_answer)
    monkeypatch.setattr(meetlat.asyncio, "sleep", sleep)
    assert _run(tmp_path, [{"cid": "aaaaaaaa", "turn": 0}], overlay) == 0
    (row,) = [json.loads(line) for line in (tmp_path / "r.jsonl").read_text().splitlines()]
    return row, pauses, remaining


# The internal surface streams, so a gateway that is gone never raises out of
# _new_answer: it ends the stream with an error frame, before or after the first
# tokens. What does raise is a connection that fails before the stream starts.
@pytest.mark.parametrize(
    "failure",
    [httpx.ConnectError("connection refused"), httpx.ReadTimeout("timed out")],
    ids=["connection_error_before_the_stream", "timeout_before_the_stream"],
)
def test_a_turn_that_meets_a_restart_is_replayed_after_growing_pauses(monkeypatch, tmp_path, capsys, failure):
    row, pauses, _ = _replay_with_outcomes(monkeypatch, tmp_path, [failure, failure, _answer()])

    assert row["text"] == "answer after the restart"
    assert "error" not in row
    assert pauses == [10, 30]
    out = capsys.readouterr().out
    assert "attempt 1 of 4" in out
    assert "attempt 2 of 4" in out


@pytest.mark.parametrize(
    "outage",
    [
        _answer(text="", stream_error="Chat service unavailable"),
        _answer(text="Zie het hand", stream_error="Chat service unavailable"),
        _answer(text="  "),
        _answer(text="De kennisbank is tijdelijk niet bereikbaar.", signals={"retrieval_failure": "HTTP 503"}),
        _answer(
            text="Dat staat niet in de kennisbank.",
            signals={"decision": "refusal", "selection": {"failed": "no_verdict_after_retry"}},
        ),
    ],
    ids=[
        "gateway_gone_before_the_first_token",
        "gateway_gone_after_partial_text",
        "empty_answer",
        "retrieval_503_behind_a_strict_refusal",
        "selection_without_a_verdict_behind_a_strict_refusal",
    ],
)
def test_an_answer_that_measured_a_restart_is_replayed(monkeypatch, tmp_path, outage):
    row, pauses, _ = _replay_with_outcomes(monkeypatch, tmp_path, [outage, _answer()])

    assert row["text"] == "answer after the restart"
    assert pauses == [10]


def test_a_failure_that_is_not_a_restart_is_an_error_row_at_once(monkeypatch, tmp_path):
    row, pauses, remaining = _replay_with_outcomes(monkeypatch, tmp_path, [KeyError("decision"), _answer()])

    assert row["error"] == "KeyError"
    assert pauses == []
    assert len(remaining) == 1


def test_a_turn_that_keeps_failing_becomes_an_error_row_after_three_retries(monkeypatch, tmp_path, capsys):
    row, pauses, remaining = _replay_with_outcomes(monkeypatch, tmp_path, [httpx.ConnectError("down")] * 4)

    assert row["error"] == "ConnectError"
    assert pauses == [10, 30, 60]
    assert remaining == []
    assert "1 error row(s)" in capsys.readouterr().out


# --- run, widget ----------------------------------------------------------------------------------------------


_WIDGET = {
    "wgt_id": "wgt_1",
    "org_id": 7,
    "kb_ids": [3],
    "slug": "brightwater",
    "origin": "https://brightwater.example.com",
}


def _widget_answer(text="Open the settings and pick the option.", status=200, **extra):
    return {
        "status": status,
        "text": text,
        "sources": ["https://help.example.com/settings"],
        "appointment": False,
        "signals": {"decision": "answer", "sources_count": 1},
        "total_ms": 1500,
        **extra,
    }


def _stub_widget_environment(monkeypatch, tmp_path, outcomes):
    """A widget round whose turns give ``outcomes`` in order (an exception is raised); the overlay and the sent messages."""
    overlay = tmp_path / "ov"
    _point_modules_at(monkeypatch, overlay)
    sent: list[list[dict]] = []
    remaining = list(outcomes)

    async def widget_answer(widget, messages, _session_id):
        assert widget == _WIDGET
        sent.append(messages)
        outcome = remaining.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def sleep(_seconds):
        return None

    monkeypatch.setattr(meetlat, "_widget_answer", widget_answer)
    monkeypatch.setattr(meetlat, "_apply_measurement_patches", lambda: None)
    monkeypatch.setattr(meetlat.asyncio, "sleep", sleep)
    monkeypatch.setattr(replay, "_route_logs_to", lambda _path: None)
    (tmp_path / "widget.json").write_text(json.dumps(_WIDGET))
    return overlay, sent


def _run_widget(tmp_path, cases_rows, overlay):
    cases = _write_jsonl(tmp_path / "cases.jsonl", cases_rows)
    return meetlat.main(
        ["run", "--surface", "widget", "--org", "brightwater", "--widget", str(tmp_path / "widget.json"),
         "--cases", str(cases), "--out", str(tmp_path / "r.jsonl"), "--overlay", str(overlay)]
    )  # fmt: skip


def _widget_case(case_id="w-1", asks=("first question",), expect="antwoord"):
    messages = []
    for ask in asks:
        messages += [{"role": "user", "content": ask}, {"role": "assistant", "content": f"earlier reply to {ask}"}]
    return {"id": case_id, "messages": messages[:-1], "expect": expect}


def test_run_replays_each_widget_case_with_its_own_conversation_and_keys_the_row_by_case_id(
    monkeypatch, tmp_path, capsys
):
    outcomes = [_widget_answer("For the memory."), _widget_answer(), _widget_answer("Second.")]
    overlay, sent = _stub_widget_environment(monkeypatch, tmp_path, outcomes)

    code = _run_widget(tmp_path, [_widget_case("w-1", ("first", "x" * 500)), _widget_case("w-2")], overlay)

    rows = [json.loads(line) for line in (tmp_path / "r.jsonl").read_text().splitlines()]
    assert code == 0
    assert [m["content"] for m in sent[0]] == ["first"]
    assert [m["content"] for m in sent[1]] == ["first", "earlier reply to first", "x" * 500]
    assert [(r["cid"], r["u"]) for r in rows] == [("w-1", 1), ("w-2", 0)]
    assert rows[0]["ask"] == "x" * 400
    assert rows[0]["sources"] == ["https://help.example.com/settings"] and rows[0]["appointment"] is False
    assert rows[1]["text"] == "Second." and rows[1]["signals"] == {"decision": "answer", "sources_count": 1}
    assert "2 of 2 cases are for this org" in capsys.readouterr().out


def test_a_widget_turn_that_meets_a_gateway_restart_is_replayed(monkeypatch, tmp_path):
    overlay, sent = _stub_widget_environment(monkeypatch, tmp_path, [_widget_answer(status=503), _widget_answer()])

    assert _run_widget(tmp_path, [_widget_case()], overlay) == 0

    (row,) = [json.loads(line) for line in (tmp_path / "r.jsonl").read_text().splitlines()]
    assert len(sent) == 2 and "error" not in row and row["text"].startswith("Open")


def test_a_widget_http_error_that_is_not_a_restart_is_an_error_row_at_once(monkeypatch, tmp_path):
    overlay, sent = _stub_widget_environment(monkeypatch, tmp_path, [_widget_answer(status=401, text="")])

    assert _run_widget(tmp_path, [_widget_case()], overlay) == 0

    (row,) = [json.loads(line) for line in (tmp_path / "r.jsonl").read_text().splitlines()]
    assert len(sent) == 1 and row["error"] == "HTTP 401"


@pytest.mark.parametrize(
    "messages",
    [
        [{"role": "assistant", "content": "x"}],
        [{"role": "user", "content": "q"}, {"role": "assistant", "content": "x"}],
    ],
    ids=["no_user_turn", "ends_on_a_reply"],
)
def test_a_widget_case_that_does_not_end_in_a_user_turn_is_refused(tmp_path, capsys, messages):
    cases = _write_jsonl(tmp_path / "cases.jsonl", [{"id": "w-1", "messages": messages}])
    (tmp_path / "widget.json").write_text(json.dumps(_WIDGET))

    code = meetlat.main(
        ["run", "--surface", "widget", "--org", "brightwater", "--widget", str(tmp_path / "widget.json"),
         "--cases", str(cases), "--out", str(tmp_path / "r.jsonl")]
    )  # fmt: skip

    assert code == 2 and "user turn" in capsys.readouterr().err


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
    assert len(meta["code_hash"]) == 12
    assert meta["code_files"] >= len(_BANNER_MODULES)
    (overlay / "app.services.internal_turn.py").write_text("# replaced after the first rows\n")

    code = _run(tmp_path, [{"cid": "aaaaaaaa", "turn": 0}, {"cid": "aaaaaaaa", "turn": 1}], overlay)

    assert code == 2
    assert "other code" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("module", "filename"),
    [("app.services.partner_chat_probe", "partner_chat.py"), ("klai_chat_prompts.language_probe", "language.py")],
)
def test_run_refuses_to_resume_when_any_loaded_app_or_prompt_module_changed(
    monkeypatch, tmp_path, capsys, module, filename
):
    source = tmp_path / "probe" / filename
    source.parent.mkdir()
    source.write_text("VERSION = 1\n")
    probe = types.ModuleType(module)
    probe.__file__ = str(source)
    monkeypatch.setitem(sys.modules, module, probe)
    answered: list[str] = []
    overlay = _stub_run_environment(monkeypatch, tmp_path, {"aaaaaaaa": _thread("aaaaaaaa-1", ["first"])}, answered)
    assert _run(tmp_path, [{"cid": "aaaaaaaa", "turn": 0}], overlay) == 0
    source.write_text("VERSION = 2\n")

    code = _run(tmp_path, [{"cid": "aaaaaaaa", "turn": 0}, {"cid": "aaaaaaaa", "turn": 1}], overlay)

    assert code == 2
    assert "other code" in capsys.readouterr().err


def test_a_variants_entry_that_is_not_a_regex_is_refused_with_a_message(tmp_path, capsys):
    case = {"cid": "aaaaaaaa", "turn": 0, "class": "dekkend_antwoord", "variants": ["android", "iphone("]}

    code = _compare(tmp_path, [case], [[_row()]])

    assert code == 2
    assert "variants" in capsys.readouterr().err


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


@pytest.mark.parametrize(
    ("klass", "row", "ok"),
    [
        ("antwoord", _row("w-1", 0, decision="answer"), True),
        ("antwoord", _row("w-1", 0, decision="human_request", text="I will connect you; use the button below."), True),
        ("antwoord", _row("w-1", 0, decision="answer", text="Which phone do you use?"), False),
        ("antwoord", _row("w-1", 0, decision="refusal", sources=0, text="Not in our help articles."), False),
        ("vraag", _row("w-1", 0, decision="answer", text="Which phone do you use?"), True),
        ("vraag", _row("w-1", 0, decision="answer"), False),
        ("niet_gevonden", _row("w-1", 0, decision="refusal", sources=0, text="Not in our help articles."), True),
        ("niet_gevonden", _row("w-1", 0, decision="answer", signals={"refused": True}), True),
        ("niet_gevonden", _row("w-1", 0, decision="answer"), False),
        ("doorverwijzing", _row("w-1", 0, decision="off_topic", sources=0, text="Please use the button below."), True),
        ("doorverwijzing", _row("w-1", 0, decision="answer"), False),
    ],
)
def test_widget_classes_read_the_kind_of_reply_the_visitor_got(tmp_path, capsys, klass, row, ok):
    report = _json_report(tmp_path, capsys, [_widget_case("w-1", expect=klass)], [[row]])

    assert _verdict_of(report, klass) == {"ok": int(ok), "of": 1}


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


@pytest.mark.parametrize(
    "row",
    [
        _row(sources=0, text="On Android and on iPhone open Settings."),
        _row(sources=2, text="Do you use Android or iPhone?"),
    ],
    ids=["no-sources", "short-clarifying-question"],
)
def test_dekkend_antwoord_needs_sources_and_must_not_be_a_short_question(tmp_path, capsys, row):
    report = _json_report(tmp_path, capsys, [{"cid": "aaaaaaaa", "turn": 0, "class": "dekkend_antwoord"}], [[row]])

    assert _verdict_of(report, "dekkend_antwoord") == {"ok": 0, "of": 1}


def test_a_refusal_that_mentions_general_knowledge_is_not_a_labelled_general_answer(tmp_path, capsys):
    row = _row(
        decision="refusal", sources=0, text="That is not in the knowledge base; I will not use general knowledge."
    )
    case = {"cid": "aaaaaaaa", "turn": 0, "class": "antwoord_algemeen_gelabeld"}

    report = _json_report(tmp_path, capsys, [case], [[row]])

    assert _verdict_of(report, "antwoord_algemeen_gelabeld") == {"ok": 0, "of": 1}


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


def test_measurement_patches_start_every_database_connection_read_only_through_the_engine_arguments(monkeypatch):
    created = []

    def create_engine_stub(url, **kwargs):
        created.append((url, kwargs))
        return "read-only engine"

    monkeypatch.setattr(meetlat, "create_async_engine", create_engine_stub)
    sessions = async_sessionmaker(bind=None)
    db = types.SimpleNamespace(engine="app engine", AsyncSessionLocal=sessions)

    meetlat._make_database_read_only(db)

    ((url, kwargs),) = created
    assert url == replay.settings.database_url
    assert kwargs["connect_args"] == {"server_settings": {"default_transaction_read_only": "on"}}
    assert kwargs["pool_size"] == replay.settings.db_pool_size
    assert db.engine == "read-only engine"
    assert sessions.kw["bind"] == "read-only engine"


def test_the_gap_event_stand_in_is_a_plain_function_like_the_one_it_replaces():
    assert not inspect.iscoroutinefunction(meetlat._skip_gap_event)
    assert not inspect.iscoroutinefunction(meetlat.retrieval_context._schedule_gap_event)
    assert meetlat._skip_gap_event(org_id=1, query_text="q") is None


def _measuring_with_memory(monkeypatch, tmp_path) -> tuple[Path, list[str]]:
    """The real measurement patches, undone after the test, and a database that fails any session; the overlay and
    the sessions that were opened."""
    from app.api import partner
    from app.services import answer_grounding, widget_audit, widget_turn

    for owner, name in (
        (partner, "record_internal_turn"),
        (partner, "record_widget_turn"),
        (partner, "last_internal_case"),
        (partner, "keep_internal_case"),
        (partner, "last_case"),
        (widget_turn, "write_retrieval_log"),
        (meetlat.retrieval_context, "_schedule_gap_event"),
        (answer_grounding, "_CHECK_TIMEOUT_SECONDS"),
        (answer_grounding, "_INTERNAL_CHECK_TIMEOUT_SECONDS"),
    ):
        monkeypatch.setattr(owner, name, getattr(owner, name))
    monkeypatch.setattr(meetlat, "_internal_cases", {})
    monkeypatch.setattr(meetlat, "_widget_cases", {})
    monkeypatch.setattr(meetlat, "_make_database_read_only", lambda _db: None)
    monkeypatch.setattr(replay, "_route_logs_to", lambda _path: None)
    opened: list[str] = []

    def no_database(*_args, **_kwargs):
        opened.append("session")
        raise AssertionError("a replay reached the case store in the database")

    for name in ("tenant_scoped_session", "cross_org_session"):
        monkeypatch.setattr(widget_audit, name, no_database)
    overlay = tmp_path / "ov"
    _point_modules_at(monkeypatch, overlay)
    return overlay, opened


def test_a_named_internal_turn_reads_the_case_its_replayed_earlier_turns_stored(monkeypatch, tmp_path):
    """Turn 2 is named, so turns 0 and 1 are replayed first for their memory
    only, with LibreChat's conversation id in the header as production sends it."""
    from contextlib import asynccontextmanager

    from starlette.responses import StreamingResponse

    from app.api import partner
    from app.services.chat_profile import ChatProfile

    overlay, opened = _measuring_with_memory(monkeypatch, tmp_path)
    profile = ChatProfile(surface="internal", kb_mode="open", user_id="sub-employee")
    thread = meetlat.Thread(
        "aaaaaaaa-0000-4000-8000-000000000001", profile, "open", _thread("x", ["first", "second", "third"]).turns
    )
    org = type("Org", (), {"id": 7, "slug": "brightwater", "zitadel_org_id": "zorg"})()
    seen: list[dict | None] = []

    async def load_org(_slug):
        return org

    async def fetch_threads(_org, _prefixes, _days):
        return {"aaaaaaaa": thread}

    @asynccontextmanager
    async def no_session(_org_id):
        yield None

    async def chat_completions(*, request, http_request, auth, db, profile):
        """The route's memory steps around a fixed reply."""
        conversation = partner._internal_conversation(http_request, request, auth, profile)
        assert conversation is not None
        stored = await partner.last_internal_case(
            org_id=auth.org_id, user_id=conversation.user_id, conversation_id=conversation.id
        )
        seen.append(stored.case if stored else None)
        ask = request.messages[-1]["content"]
        case = types.SimpleNamespace(model_dump=lambda: {"problem": f"after {ask}"})
        partner._keep_internal_case(auth.org_id, conversation, types.SimpleNamespace(case=case, kept_passages=[]))
        frame = json.dumps({"choices": [{"index": 0, "delta": {"content": f"answer to {ask}"}}]})

        async def frames():
            yield f"data: {frame}\n\n".encode()

        return StreamingResponse(frames(), media_type="text/event-stream")

    monkeypatch.setattr(replay, "_load_org", load_org)
    monkeypatch.setattr(meetlat, "_fetch_threads", fetch_threads)
    monkeypatch.setattr(replay, "tenant_scoped_session", no_session)
    monkeypatch.setattr(partner, "chat_completions", chat_completions)

    assert _run(tmp_path, [{"cid": "aaaaaaaa", "turn": 2}], overlay) == 0

    rows = [json.loads(line) for line in (tmp_path / "r.jsonl").read_text().splitlines()]
    assert [(r["cid"], r["u"], r["text"]) for r in rows] == [("aaaaaaaa", 2, "answer to third")]
    assert seen == [None, {"problem": "after first"}, {"problem": "after second"}]
    assert opened == []


def test_a_named_widget_turn_reads_the_case_its_replayed_earlier_turns_stored(monkeypatch, tmp_path):
    """The case's own messages are the history, so the stored turn stands for
    the reply in them: the route's check that the case belongs to this
    conversation (case_file.stored_case_for) then lets it through."""
    from app.api import partner
    from app.services import case_file, widget_audit

    overlay, opened = _measuring_with_memory(monkeypatch, tmp_path)
    (tmp_path / "widget.json").write_text(json.dumps(_WIDGET))
    seen: list[dict | None] = []
    sessions: set[str] = set()

    async def widget_answer(widget, messages, session_id):
        """The route's memory steps around a fixed reply, under the session key the route derives from the token."""
        sessions.add(session_id)
        key = widget_audit.session_key_from_claims(org_id=widget["org_id"], wgt_id=widget["wgt_id"], jti=session_id)
        stored = case_file.stored_case_for(messages, await partner.last_case(widget_id="w-row", session_key=key))
        seen.append(stored.case if stored else None)
        ask = messages[-1]["content"]
        signals = {"case": {"problem": f"after {ask}"}, "decision": "answer"}
        await partner.record_widget_turn(
            widget_id="w-row", session_key=key, role="assistant", content=f"replayed {ask}", answer_signals=signals
        )
        return _widget_answer(f"replayed {ask}", signals=signals)

    monkeypatch.setattr(meetlat, "_widget_answer", widget_answer)

    assert _run_widget(tmp_path, [_widget_case("w-1", ("first", "second", "third"))], overlay) == 0

    rows = [json.loads(line) for line in (tmp_path / "r.jsonl").read_text().splitlines()]
    assert [(r["cid"], r["u"], r["text"]) for r in rows] == [("w-1", 2, "replayed third")]
    assert seen == [None, {"problem": "after first"}, {"problem": "after second"}]
    assert len(sessions) == 1
    assert opened == []


def test_a_named_internal_turn_after_a_failed_memory_turn_says_so_and_compare_counts_it(monkeypatch, tmp_path, capsys):
    """A turn replayed only for its memory writes no row, so its failure was
    invisible: the next row looked like a measured follow-up, without the case."""
    threads = {"aaaaaaaa": _thread("aaaaaaaa-1111", ["first", "second"])}
    overlay = _stub_run_environment(monkeypatch, tmp_path, threads, [])

    async def new_answer(_org, _profile, messages, _conversation_id):
        if messages[-1]["content"] == "first":
            raise ValueError("synthetic failure")
        return {"text": "Open the settings.", "ttft_ms": 5, "total_ms": 900, "signals": {"decision": "answer"}}

    monkeypatch.setattr(replay, "_new_answer", new_answer)
    cases = [{"cid": "aaaaaaaa", "turn": 1, "class": "antwoord_kb"}]

    assert _run(tmp_path, cases, overlay) == 0

    (row,) = [json.loads(line) for line in (tmp_path / "r.jsonl").read_text().splitlines()]
    assert row["prefix_errors"] == [{"u": 0, "error": "ValueError"}]
    capsys.readouterr()
    assert _json_report(tmp_path, capsys, cases, [[row]])["rounds"][0]["prefix_errors"] == 1


def test_a_named_widget_turn_after_a_failed_memory_turn_says_so(monkeypatch, tmp_path):
    overlay, _ = _stub_widget_environment(monkeypatch, tmp_path, [ValueError("synthetic failure"), _widget_answer()])

    assert _run_widget(tmp_path, [_widget_case("w-1", ("first", "second"))], overlay) == 0

    (row,) = [json.loads(line) for line in (tmp_path / "r.jsonl").read_text().splitlines()]
    assert row["prefix_errors"] == [{"u": 0, "error": "ValueError"}]


def test_a_retried_internal_turn_reads_the_case_of_the_turn_before_not_its_own_first_attempt(monkeypatch, tmp_path):
    """The route stores the case before the answer streams, so an attempt that
    met a restart had already replaced the turn before's case with its own."""
    from contextlib import asynccontextmanager

    from starlette.responses import StreamingResponse

    from app.api import partner

    overlay, _ = _measuring_with_memory(monkeypatch, tmp_path)
    thread = meetlat.Thread(
        "aaaaaaaa-0000-4000-8000-000000000001",
        ChatProfile(surface="internal", kb_mode="open", user_id="sub-employee"),
        "open",
        _thread("x", ["first", "second"]).turns,
    )
    org = type("Org", (), {"id": 7, "slug": "brightwater", "zitadel_org_id": "zorg"})()
    seen: list[dict | None] = []
    attempts: dict[str, int] = {}

    async def load_org(_slug):
        return org

    async def fetch_threads(_org, _prefixes, _days):
        return {"aaaaaaaa": thread}

    @asynccontextmanager
    async def no_session(_org_id):
        yield None

    async def sleep(_seconds):
        return None

    async def chat_completions(*, request, http_request, auth, db, profile):
        """The route's memory steps; the first attempt at "second" stores its case and then loses its stream."""
        conversation = partner._internal_conversation(http_request, request, auth, profile)
        assert conversation is not None
        stored = await partner.last_internal_case(
            org_id=auth.org_id, user_id=conversation.user_id, conversation_id=conversation.id
        )
        seen.append(stored.case if stored else None)
        ask = request.messages[-1]["content"]
        attempts[ask] = attempts.get(ask, 0) + 1
        case = types.SimpleNamespace(model_dump=lambda: {"problem": f"after {ask}, attempt {attempts[ask]}"})
        partner._keep_internal_case(auth.org_id, conversation, types.SimpleNamespace(case=case, kept_passages=[]))
        text = "" if ask == "second" and attempts[ask] == 1 else f"answer to {ask}"
        frame = json.dumps({"choices": [{"index": 0, "delta": {"content": text}}]})

        async def frames():
            yield f"data: {frame}\n\n".encode()

        return StreamingResponse(frames(), media_type="text/event-stream")

    monkeypatch.setattr(replay, "_load_org", load_org)
    monkeypatch.setattr(meetlat, "_fetch_threads", fetch_threads)
    monkeypatch.setattr(replay, "tenant_scoped_session", no_session)
    monkeypatch.setattr(partner, "chat_completions", chat_completions)
    monkeypatch.setattr(meetlat.asyncio, "sleep", sleep)

    assert _run(tmp_path, [{"cid": "aaaaaaaa", "turn": 1}], overlay) == 0

    (row,) = [json.loads(line) for line in (tmp_path / "r.jsonl").read_text().splitlines()]
    assert row["text"] == "answer to second"
    assert seen == [None, {"problem": "after first, attempt 1"}, {"problem": "after first, attempt 1"}]


# --- retrieval -------------------------------------------------------------------------------------------------


_ART = "https://help.example.com/articles"


def test_stage_hits_tell_a_candidate_lost_in_the_rerank_from_one_that_stayed():
    stages = {"search": [f"{_ART}/a", f"{_ART}/b"], "rerank": [f"{_ART}/b"]}

    lost = meetlat.stage_hits([f"{_ART}/a"], stages, [f"{_ART}/b"])
    kept = meetlat.stage_hits([f"{_ART}/b"], stages, [f"{_ART}/c", f"{_ART}/b"])
    absent = meetlat.stage_hits([f"{_ART}/z"], stages, [f"{_ART}/b"])

    assert lost == {"search": True, "rerank": False, "served": None}
    assert kept == {"search": True, "rerank": True, "served": 2}
    assert absent == {"search": False, "rerank": False, "served": None}


def test_stage_hits_rank_the_best_of_several_expected_articles_among_the_served():
    served = [f"{_ART}/x", f"{_ART}/b", f"{_ART}/a"]

    assert meetlat.stage_hits([f"{_ART}/a", f"{_ART}/b"], {}, served)["served"] == 2


def test_expected_articles_are_keyed_like_the_evidence_pack_keys_a_source():
    case = {"id": "w-1", "expect": "antwoord", "articles": ["https://Help.example.com/articles/a/", "not a url"]}

    assert meetlat._expected_sources(case) == [f"{_ART}/a"]
    assert meetlat._distinct_keys(["https://www.help.example.com/articles/a", None, f"{_ART}/a/"]) == [f"{_ART}/a"]


@pytest.mark.parametrize(
    "klass", ["niet_gevonden", "vraag", "doorverwijzing", "weigering", "antwoord_algemeen_gelabeld"]
)
def test_a_case_whose_answer_is_not_an_article_expects_none(klass):
    assert meetlat._expected_sources({"id": "w-1", "expect": klass, "articles": [f"{_ART}/a"]}) == []


def test_an_internal_case_without_articles_expects_the_links_of_the_reply_recorded_for_its_turn():
    thread = _thread("aaaaaaaa-1", ["first", "second"])
    thread.turns[3]["content"] = f"See [the guide]({_ART}/a/) and {_ART}/b."
    case = {"cid": "aaaaaaaa", "turn": 1, "expect": "antwoord_kb"}

    assert meetlat._expected_sources(case, meetlat._recorded_reply(thread, 1)) == [f"{_ART}/a", f"{_ART}/b"]
    assert meetlat._expected_sources(case, meetlat._recorded_reply(thread, 0)) == []
    assert meetlat._recorded_reply(thread, 5) == ""


def _stage_rows():
    def row(search, rerank, served, ms):
        return {
            "cid": "c",
            "u": 0,
            "search": search,
            "rerank": rerank,
            "served": served,
            "retrieve_ms": ms,
            "api_ms": ms,
        }

    return [
        row(True, True, 1, 100),
        row(True, False, None, 200),
        row(True, True, 4, 300),
        row(False, False, None, 400),
        {"cid": "c", "u": 1, "skipped": "no_expected_source"},
        {"cid": "c", "u": 2, "error": "HTTPException"},
    ]


def test_the_summary_gives_recall_per_stage_and_the_mrr_of_the_served_list():
    summary = meetlat.summarize_stages(_stage_rows())

    assert summary["cases"] == 4 and summary["skipped"] == 1 and summary["errors"] == 1
    assert summary["failed"] == 0 and summary["not_searched"] == 0
    assert (summary["recall_search"], summary["recall_rerank"], summary["recall_served"]) == (0.75, 0.5, 0.5)
    assert summary["mrr_served"] == round((1 + 1 / 4) / 4, 3)
    assert summary["retrieve_ms"] == {"p50": 200, "p95": 400}


def test_a_summary_without_measured_rows_has_no_shares():
    summary = meetlat.summarize_stages([{"cid": "c", "u": 0, "skipped": "no_expected_source"}])

    assert summary["cases"] == 0 and summary["recall_served"] is None and summary["mrr_served"] is None


class _PostedRetrieval:
    """Stands in for httpx.AsyncClient around retrieve_context: keeps the bodies posted, answers with ``payload``."""

    def __init__(self, monkeypatch, payload):
        self.bodies: list[dict] = []
        outer = self

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return payload

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

            async def post(self, url, json=None, headers=None):
                outer.bodies.append(json)
                return Response()

        monkeypatch.setattr("app.services.retrieval_context.httpx.AsyncClient", lambda timeout: Client())


def _retrieval_payload():
    items = [
        {"chunk_id": "c1", "evidence_id": "E1", "text": "passage", "title": "One", "source_url": f"{_ART}/a/"},
        {"chunk_id": "c2", "evidence_id": "E2", "text": "passage", "title": "Two", "source_url": f"{_ART}/b"},
    ]
    return {
        "chunks": [{"chunk_id": "c1", "product": "orbit"}, {"chunk_id": "c2", "product": "zephyr"}],
        "metadata": {"retrieval_ms": 321.0},
        "evidence_pack": {"items": items, "sources": []},
        "debug_stages": {"search": [f"{_ART}/a", f"{_ART}/b", f"{_ART}/c"], "rerank": [f"{_ART}/a"]},
    }


async def _retrieve_with_probe(monkeypatch, *, armed: bool, product: str = ""):
    from app.services import retrieval_context
    from app.services.retrieval_context import KnowledgeTurn

    posted = _PostedRetrieval(monkeypatch, _retrieval_payload())
    # Restored at teardown: the probe replaces these two module functions.
    for name in ("_call_retrieval", "_choose_and_prompt"):
        monkeypatch.setattr(retrieval_context, name, getattr(retrieval_context, name))
    monkeypatch.setattr(retrieval_context, "_schedule_gap_event", lambda **_: None)

    async def no_paraphrases(*_args, **_kwargs):
        return []

    monkeypatch.setattr(retrieval_context, "first_question_variants", no_paraphrases)
    meetlat._install_retrieval_probe()
    if armed:
        meetlat._arm()
    settings = types.SimpleNamespace(
        knowledge_retrieve_url="http://retrieval-api",
        retrieval_api_internal_secret="s",
        internal_secret="s",
        kb_images_base_url="",
    )
    turn = KnowledgeTurn()
    result = await retrieval_context.retrieve_context(
        42,
        "zorg",
        ["kb"],
        [{"role": "user", "content": "How do I do this?"}],
        settings,
        top_k=8,
        knowledge_turn=turn,
        product=product,
    )
    return posted, turn, result, meetlat._take()


@pytest.mark.asyncio
async def test_the_probe_asks_for_the_stages_keeps_what_the_chat_would_receive_and_stops_the_turn_there(monkeypatch):
    async def selection_must_not_run(*_args, **_kwargs):
        raise AssertionError("the selection step ran in a retrieval round")

    monkeypatch.setattr("app.services.retrieval_context.select_passages", selection_must_not_run)

    posted, turn, result, record = await _retrieve_with_probe(monkeypatch, armed=True)

    assert posted.bodies[0]["debug_stages"] is True
    assert record["stages"] == {"search": [f"{_ART}/a", f"{_ART}/b", f"{_ART}/c"], "rerank": [f"{_ART}/a"]}
    assert record["served"] == [f"{_ART}/a", f"{_ART}/b"]
    assert (record["top_k"], record["api_ms"]) == (8, 321.0) and record["retrieve_ms"] >= 0
    assert result == ([], "", [], False)
    assert turn.not_in_passages and turn.refusal is not None


@pytest.mark.asyncio
async def test_the_probe_keeps_the_questions_product_and_the_product_of_each_served_item(monkeypatch):
    posted, _, _, record = await _retrieve_with_probe(monkeypatch, armed=True, product="Orbit")

    assert posted.bodies[0]["product"] == "Orbit"
    assert (record["product"], record["served_products"]) == ("Orbit", ["orbit", "zephyr"])


def test_the_summary_counts_served_items_of_another_product_than_the_questions():
    record = {
        "top_k": 8,
        "retrieve_ms": 10,
        "api_ms": 9.0,
        "stages": {"search": [f"{_ART}/a"], "rerank": [f"{_ART}/a"]},
        "served": [f"{_ART}/a"],
        "product": "Orbit",
        "served_products": ["orbit", "zephyr", "", "Zephyr"],
    }
    known = meetlat._stage_row({"cid": "c", "u": 0}, [f"{_ART}/a"], record)
    unknown = meetlat._stage_row({"cid": "c", "u": 1}, [f"{_ART}/a"], {**record, "product": ""})

    summary = meetlat.summarize_stages([known, unknown])

    assert known["other_product"] == 2
    assert "other_product" not in unknown
    assert (summary["product_known"], summary["other_product_served"]) == (1, 2)


@pytest.mark.asyncio
async def test_a_turn_the_probe_is_not_armed_for_is_asked_and_answered_as_in_production(monkeypatch):
    async def no_selection(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.services.retrieval_context.select_passages", no_selection)

    posted, turn, result, record = await _retrieve_with_probe(monkeypatch, armed=False)

    assert "debug_stages" not in posted.bodies[0]
    assert record is None
    assert turn.refusal is None
    assert [chunk["chunk_id"] for chunk in result[0]] == ["c1", "c2"]


def _retrieval_answer(monkeypatch, *, searches=True):
    """A replayed turn that leaves a record in the probe when it is armed, as the stubs around retrieve_context do."""
    asked: list[tuple[str, bool]] = []

    async def new_answer(_org, _profile, messages, _conversation_id):
        asked.append((messages[-1]["content"], meetlat._probe["armed"]))
        if meetlat._probe["armed"] and searches:
            meetlat._probe["record"] = {
                "top_k": 20,
                "retrieve_ms": 250,
                "api_ms": 200.0,
                "stages": {"search": [f"{_ART}/a", f"{_ART}/b"], "rerank": [f"{_ART}/b"]},
                "served": [f"{_ART}/c", f"{_ART}/a"],
            }
        return {"text": meetlat._PROBE_REPLY, "ttft_ms": 5, "total_ms": 900, "signals": {"decision": "refusal"}}

    monkeypatch.setattr(replay, "_new_answer", new_answer)
    monkeypatch.setattr(meetlat, "_install_retrieval_probe", lambda: None)
    return asked


def _retrieval(tmp_path, cases_rows, overlay, surface="internal"):
    cases = _write_jsonl(tmp_path / "cases.jsonl", cases_rows)
    extra = ["--widget", str(tmp_path / "widget.json")] if surface == "widget" else []
    return meetlat.main(
        ["retrieval", "--surface", surface, "--org", "brightwater", "--cases", str(cases), "--out", str(tmp_path / "r.jsonl"),
         "--overlay", str(overlay), *extra]
    )  # fmt: skip


def test_a_retrieval_round_stops_every_internal_turn_after_its_retrieval_and_measures_the_named_one(
    monkeypatch, tmp_path, capsys
):
    thread = _thread("aaaaaaaa-1111", ["first", "second"])
    thread.turns[3]["content"] = f"See {_ART}/a for this."
    overlay = _stub_run_environment(monkeypatch, tmp_path, {"aaaaaaaa": thread}, [])
    asked = _retrieval_answer(monkeypatch)
    cases = [
        {"cid": "aaaaaaaa", "turn": 1, "expect": "antwoord_kb"},
        {"cid": "aaaaaaaa", "turn": 0, "expect": "weigering"},
    ]

    code = _retrieval(tmp_path, cases, overlay)

    rows = {r["u"]: r for r in map(json.loads, (tmp_path / "r.jsonl").read_text().splitlines())}
    assert code == 0
    # Turn 0 is replayed for the case turn 1 reads; both stop after their retrieval, only turn 1 gets a row.
    assert asked == [("first", True), ("second", True)]
    assert rows[0] == {"cid": "aaaaaaaa", "u": 0, "skipped": "no_expected_source"}
    assert {k: rows[1][k] for k in ("search", "rerank", "served", "expected", "top_k", "retrieve_ms")} == {
        "search": True, "rerank": False, "served": 2, "expected": 1, "top_k": 20, "retrieve_ms": 250,
    }  # fmt: skip
    assert "text" not in rows[1]
    summary = json.loads((tmp_path / "r.jsonl.summary.json").read_text())
    assert (summary["cases"], summary["skipped"], summary["recall_served"], summary["mrr_served"]) == (1, 1, 1.0, 0.5)
    assert json.loads(capsys.readouterr().out.split("files: ")[1].split("\n", 1)[1]) == summary
    assert json.loads((tmp_path / "r.jsonl.meta.json").read_text())["command"] == "retrieval"


def test_a_retrieval_round_that_resumes_summarises_the_rows_of_both_runs(monkeypatch, tmp_path):
    thread = _thread("aaaaaaaa-1111", ["first", "second"])
    thread.turns[1]["content"] = f"See {_ART}/a."
    thread.turns[3]["content"] = f"See {_ART}/b."
    overlay = _stub_run_environment(monkeypatch, tmp_path, {"aaaaaaaa": thread}, [])
    _retrieval_answer(monkeypatch)
    cases = [{"cid": "aaaaaaaa", "turn": u, "expect": "antwoord_kb"} for u in (0, 1)]
    assert _retrieval(tmp_path, cases[:1], overlay) == 0

    assert _retrieval(tmp_path, cases, overlay) == 0

    assert json.loads((tmp_path / "r.jsonl.summary.json").read_text())["cases"] == 2


def test_a_turn_that_searched_nothing_is_a_miss_at_every_stage_when_its_case_expects_an_article(monkeypatch, tmp_path):
    thread = _thread("aaaaaaaa-1111", ["first"])
    thread.turns[1]["content"] = f"See {_ART}/a."
    overlay = _stub_run_environment(monkeypatch, tmp_path, {"aaaaaaaa": thread}, [])
    _retrieval_answer(monkeypatch, searches=False)

    assert _retrieval(tmp_path, [{"cid": "aaaaaaaa", "turn": 0, "expect": "antwoord_kb"}], overlay) == 0

    (row,) = map(json.loads, (tmp_path / "r.jsonl").read_text().splitlines())
    assert (row["search"], row["rerank"], row["served"], row["not_searched"]) == (False, False, None, True)
    summary = json.loads((tmp_path / "r.jsonl.summary.json").read_text())
    assert (summary["cases"], summary["not_searched"], summary["recall_search"], summary["recall_served"]) == (
        1,
        1,
        0.0,
        0.0,
    )


def test_a_retrieval_api_that_answers_without_stages_is_an_error_not_a_miss():
    record = {"top_k": 8, "retrieve_ms": 10, "api_ms": 9.0, "stages": None, "served": [f"{_ART}/a"]}

    row = meetlat._stage_row({"cid": "c", "u": 0, "mode": "open"}, [f"{_ART}/a"], record)

    assert row == {"cid": "c", "u": 0, "mode": "open", "error": "retrieval_api_without_debug_stages"}


def test_a_retrieval_round_on_the_widget_reads_the_expected_articles_from_the_case(monkeypatch, tmp_path):
    overlay, _sent = _stub_widget_environment(monkeypatch, tmp_path, [])
    armed: list[bool] = []

    async def widget_answer(_widget, _messages, _session_id):
        armed.append(meetlat._probe["armed"])
        if meetlat._probe["armed"]:
            meetlat._probe["record"] = {
                "top_k": 8,
                "retrieve_ms": 120,
                "api_ms": 90.0,
                "stages": {"search": [f"{_ART}/a"], "rerank": [f"{_ART}/a"]},
                "served": [f"{_ART}/a"],
            }
        return _widget_answer(meetlat._PROBE_REPLY)

    monkeypatch.setattr(meetlat, "_widget_answer", widget_answer)
    monkeypatch.setattr(meetlat, "_install_retrieval_probe", lambda: None)
    cases = [
        {**_widget_case("w-1", ("first", "second")), "articles": [f"{_ART}/a/"]},
        {**_widget_case("w-2"), "articles": []},
        {**_widget_case("w-3", expect="niet_gevonden"), "articles": [f"{_ART}/a"]},
    ]

    code = _retrieval(tmp_path, cases, overlay, surface="widget")

    rows = {r["cid"]: r for r in map(json.loads, (tmp_path / "r.jsonl").read_text().splitlines())}
    assert code == 0
    assert armed == [True, True]
    assert (rows["w-1"]["search"], rows["w-1"]["rerank"], rows["w-1"]["served"]) == (True, True, 1)
    assert rows["w-2"]["skipped"] == rows["w-3"]["skipped"] == "no_expected_source"


def test_an_explicitly_empty_article_list_expects_none_while_a_missing_one_falls_back_to_the_recorded_reply():
    reply = f"See {_ART}/a."

    assert (
        meetlat._expected_sources({"cid": "aaaaaaaa", "turn": 0, "expect": "antwoord_kb", "articles": []}, reply) == []
    )
    assert meetlat._expected_sources({"cid": "aaaaaaaa", "turn": 0, "expect": "antwoord_kb"}, reply) == [f"{_ART}/a"]


@pytest.mark.asyncio
async def test_a_lost_knowledge_base_in_a_retrieval_round_is_a_miss_and_ends_the_turn_before_a_writer(
    monkeypatch,
):
    from app.services import retrieval_context
    from app.services.retrieval_context import KnowledgeTurn

    async def knowledge_base_lost(_turn, _body):
        return [], "prompt for an Open turn without passages", [], False

    monkeypatch.setattr(retrieval_context, "_call_retrieval", knowledge_base_lost)
    monkeypatch.setattr(retrieval_context, "_choose_and_prompt", retrieval_context._choose_and_prompt)
    meetlat._install_retrieval_probe()
    turn = types.SimpleNamespace(turn=KnowledgeTurn())
    meetlat._arm()

    result = await retrieval_context._call_retrieval(turn, {"top_k": 20})
    record = meetlat._take()

    assert result[0] == []
    assert record == {"failed": True}
    assert turn.turn.refusal is not None
    row = meetlat._stage_row({"cid": "c", "u": 0}, [f"{_ART}/a"], record)
    assert row == {"cid": "c", "u": 0, "search": False, "rerank": False, "served": None, "failed": True}
    summary = meetlat.summarize_stages([row, *_stage_rows()[:1]])
    assert (summary["cases"], summary["failed"], summary["recall_served"]) == (2, 1, 0.5)


def _record(search, served=()):
    return {"top_k": 20, "retrieve_ms": 10, "api_ms": 9.0, "stages": {"search": search, "rerank": []}, "served": served}


def test_links_of_a_recorded_reply_outside_the_knowledge_base_do_not_become_expected_articles():
    """A web search citation or a product URL in the reply is not an article of the knowledge base."""
    kb, elsewhere = f"{_ART}/a", "https://vendor.example.net/pricing"
    record = _record([kb, f"{_ART}/z"])

    row = meetlat._stage_row({"cid": "c", "u": 0}, [elsewhere, kb], record, kb_hosts_only=True)
    only_elsewhere = meetlat._stage_row({"cid": "c", "u": 0}, [elsewhere], record, kb_hosts_only=True)

    assert (row["expected"], row["search"]) == (1, True)
    assert only_elsewhere == {"cid": "c", "u": 0, "skipped": "no_expected_source"}


def test_the_articles_a_case_names_itself_are_kept_whatever_their_host():
    record = _record([f"{_ART}/z"])

    row = meetlat._stage_row({"cid": "c", "u": 0}, ["https://vendor.example.net/a"], record)

    assert (row["expected"], row["search"]) == (1, False)


@pytest.mark.parametrize("host", ["retrieval-api", "klai-core-retrieval-api-1"])
def test_a_round_refuses_to_start_against_the_live_retrieval_api(monkeypatch, tmp_path, capsys, host):
    """The live service rewrites follow-up questions on klai-fast, which the portal cannot patch."""
    answered: list[str] = []
    overlay = _stub_run_environment(monkeypatch, tmp_path, {"aaaaaaaa": _thread("aaaaaaaa", ["first"])}, answered)
    monkeypatch.setattr(replay.settings, "knowledge_retrieve_url", f"http://{host}:8040/retrieve")

    code = _run(tmp_path, [{"cid": "aaaaaaaa", "turn": 0}], overlay)

    assert code == 2
    assert "retrieval-api-oneoff.sh" in capsys.readouterr().err
    assert answered == []
    assert not (tmp_path / "r.jsonl").exists()


def test_a_round_starts_against_a_retrieval_api_one_off(monkeypatch, tmp_path):
    answered: list[str] = []
    overlay = _stub_run_environment(monkeypatch, tmp_path, {"aaaaaaaa": _thread("aaaaaaaa", ["first"])}, answered)
    monkeypatch.setattr(replay.settings, "knowledge_retrieve_url", "http://ra-depth60:8040/retrieve")

    assert _run(tmp_path, [{"cid": "aaaaaaaa", "turn": 0}], overlay) == 0
    assert answered == ["first"]


# A round runs on the Vibe allowance, never on the keys real visitors use.
_VIBE_ALLOWANCE = {"klai-ingest", "klai-judge"}


@pytest.mark.parametrize("surface", ["internal", "widget"])
@pytest.mark.asyncio
async def test_a_replayed_turn_calls_no_model_outside_the_vibe_allowance(monkeypatch, tmp_path, surface):
    """On 23 Oct 2026 a round answered on klai-primary and checked on klai-medium and klai-fast, and spent the
    Mistral workspaces' monthly limit that live chat shares. The multi-part question is the one the internal
    Strict rule would move to klai-medium, if the request were klai-primary."""
    from app.api import partner
    from tests.test_one_pipeline_answer_decision import _internal, _LiteLLM, _pack, _turn

    _measuring_with_memory(monkeypatch, tmp_path)
    for name in ("extraction_model", "answer_grounding_model", "retrieval_paraphrase_model"):
        monkeypatch.setattr(replay.settings, name, getattr(replay.settings, name))
    monkeypatch.setattr(replay, "_REQUEST_MODEL", replay._REQUEST_MODEL)
    monkeypatch.setattr(partner, "_ALLOWED_MODELS", partner._ALLOWED_MODELS)
    meetlat._apply_measurement_patches()
    llm = _LiteLLM()
    widget = surface == "widget"

    turn = await _turn(
        monkeypatch,
        llm,
        profile=ChatProfile(surface="widget") if widget else _internal(),
        question="Hoe vraag ik verlof aan? En wat kost dat?",
        retrieval={"evidence_pack": _pack(), "confidence_band": "high"},
        key_id="wgt_acme" if widget else "key-internal",
        stream=not widget,
        request_body=meetlat._widget_body if widget else replay._body,
        models_from_settings=True,
    )

    assert turn.text
    assert {_LiteLLM._kind(body) for body in llm.calls} >= {"answer", "passage_selection", "grounding_check"}
    assert {body["model"] for body in llm.calls} <= _VIBE_ALLOWANCE
    # A round runs on the measurement key, which LiteLLM masks only for the tenant a call names.
    assert all((body.get("metadata") or {}).get("_klai_delegated_org_id") for body in llm.calls)
    # The paraphrases run on first widget questions only, so the turn above may not have made that call.
    assert replay.settings.retrieval_paraphrase_model in _VIBE_ALLOWANCE
