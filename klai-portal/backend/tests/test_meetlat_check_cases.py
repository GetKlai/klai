"""``meetlat check-cases`` proves a widget case is newly written, not copied from a stored visitor message.

synthetic-data: generator=hand-written seed=0 (invented questions, example.com addresses, no real visitors).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import meetlat

STORED = "please tell me how long the blue widget warranty lasts after the purchase date"


def _case(case_id: str, *contents: str) -> dict:
    roles = ("user", "assistant")
    messages = [{"role": roles[i % 2], "content": text} for i, text in enumerate(contents)]
    return {"id": case_id, "messages": messages}


def _run(tmp_path: Path, monkeypatch, capsys, cases: list[dict], stored: list[str]) -> tuple[int, str]:
    path = tmp_path / "cases.jsonl"
    path.write_text("\n".join(json.dumps(c) for c in cases))

    async def visitor_texts(slug: str) -> list[str]:
        return stored

    monkeypatch.setattr(meetlat, "_visitor_texts", visitor_texts)
    code = meetlat.main(["check-cases", "--org", "acme", "--cases", str(path)])
    return code, capsys.readouterr().out


def test_case_with_an_email_address_fails_and_names_the_case(tmp_path, monkeypatch, capsys):
    code, out = _run(tmp_path, monkeypatch, capsys, [_case("mail-1", "Write me at jane.doe@example.com please")], [])
    assert code != 0
    assert "mail-1" in out
    assert "jane.doe@example.com" not in out


def test_case_with_a_long_digit_run_fails_even_when_separated(tmp_path, monkeypatch, capsys):
    code, out = _run(tmp_path, monkeypatch, capsys, [_case("num-1", "My number is 555 01-99.23 thanks")], [])
    assert code != 0
    assert "num-1" in out


def test_six_shared_words_fail_and_five_pass(tmp_path, monkeypatch, capsys):
    six = "How long the blue widget warranty lasts?"  # six words shared with STORED; case and punctuation differ
    five = "How long the blue widget works?"
    code, out = _run(tmp_path, monkeypatch, capsys, [_case("case-six", six), _case("case-five", five)], [STORED])
    assert code != 0
    assert "case-six" in out
    assert "case-five" not in out


def test_output_of_a_failing_overlap_never_contains_the_stored_text(tmp_path, monkeypatch, capsys):
    _, out = _run(
        tmp_path, monkeypatch, capsys, [_case("case-six", "how long the blue widget warranty lasts")], [STORED]
    )
    assert "case-six" in out
    assert "warranty" not in out
    assert "blue" not in out


def test_clean_case_passes_and_internal_cases_are_skipped(tmp_path, monkeypatch, capsys):
    cases = [_case("clean", "Can I return a gadget?", "Yes, within a month.", "And how do I send it back?")]
    cases.append({"cid": "abcd1234", "turn": 0})
    code, out = _run(tmp_path, monkeypatch, capsys, cases, [STORED])
    assert code == 0
    assert "1 internal" in out
