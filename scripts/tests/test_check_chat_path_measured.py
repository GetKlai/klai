import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "gate", Path(__file__).parents[1] / "check-chat-path-measured.py"
)
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

CHAT_FILE = "klai-portal/backend/app/services/partner_chat.py"
ROW = "+| 9 | 3 okt | fewer damaged answers | 12 of 40 damaged became 3 | live |"


def _plan_diff(*added: str, section: str = "## 8. Uitkomsten per stap") -> str:
    return "\n".join([" ## 7. Plan", " | 1 | old row |", f" {section}", " | Stap | Datum |", " |---|---|", *added])


def test_a_chat_path_change_without_a_result_row_is_refused():
    assert gate.check([CHAT_FILE], _plan_diff()) is not None


def test_a_chat_path_change_with_a_result_row_passes():
    assert gate.check([CHAT_FILE], _plan_diff(ROW)) is None


def test_a_row_added_to_another_section_does_not_count():
    assert gate.check([CHAT_FILE], _plan_diff(ROW, section="## 7. Plan")) is not None


def test_a_change_outside_the_chat_path_needs_no_row():
    assert gate.check(["klai-portal/backend/app/api/admin_widgets.py", "docs/x.md"], "") is None
