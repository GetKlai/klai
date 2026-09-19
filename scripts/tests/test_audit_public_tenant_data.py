"""The guard has to catch the table that leaked, and nothing else.

Every finding below came from a review that probed the first version and got it
through. A guard that blocks every Markdown table is switched off within a week,
so the negative cases matter as much as the positive ones.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_MODULE = Path(__file__).resolve().parents[1] / "audit-public-tenant-data.py"
_spec = importlib.util.spec_from_file_location("audit_public_tenant_data", _MODULE)
audit = importlib.util.module_from_spec(_spec)
sys.modules["audit_public_tenant_data"] = audit
_spec.loader.exec_module(audit)


@pytest.fixture(autouse=True)
def _no_git(monkeypatch):
    monkeypatch.setattr(audit, "_known_librechat_names", lambda: {"getklai", "digest"})


def _diff(*lines: str, path: str = "docs/x.md") -> str:
    return f"+++ b/{path}\n@@ -1,0 +1,9 @@\n" + "\n".join(lines) + "\n"


def test_the_table_that_leaked_is_blocked():
    problems = audit.check(_diff("+| Tenant | Antwoorden |", "+|---|---|", "+| privacy1 | 23 |"))

    assert len(problems) == 1
    assert problems[0].startswith("docs/x.md:")


def test_a_finding_never_repeats_the_customer_row():
    """CI logs of a public repository are public.

    Echoing the match would copy the very data the guard blocks into a second,
    permanent place.
    """
    problems = audit.check(_diff("+| Tenant | Antwoorden |", "+|---|---|", "+| privacy1 | 23 |"))

    assert "privacy1" not in problems[0]
    assert "23" not in problems[0].split(":")[-1]


def test_a_row_added_under_an_untouched_header_is_caught():
    """The header is context, not an added line; without it the row is invisible."""
    problems = audit.check(_diff(" | Tenant | Antwoorden |", " |---|---|", "+| acme | 23 |"))

    assert len(problems) == 1


def test_numbers_that_are_not_bare_are_still_numbers():
    """72% and "23 chats" are usage data as much as a bare count is."""
    for cell in ("72%", "23 chats", "~23", "23 (12%)", "`23`"):
        assert audit.check(_diff("+| Tenant | Gebruik |", "+|---|---|", f"+| acme | {cell} |")), cell


def test_a_table_without_outer_pipes_is_a_table():
    problems = audit.check(_diff("+Tenant | Gebruik", "+--- | ---", "+acme | 23"))

    assert len(problems) == 1


def test_table_state_does_not_leak_into_the_next_file():
    diff = "+++ b/a.md\n@@ -1,0 +1,2 @@\n+| Tenant | Gebruik |\n+++ b/b.md\n@@ -1,0 +1,2 @@\n+| Snelheid | 4.0 s |\n"

    assert audit.check(diff) == []


def test_a_database_name_on_a_header_line_is_still_reported():
    """The header branch used to return before the name scan ran."""
    problems = audit.check(_diff("+| tenant | librechat-brandnew |"))

    assert len(problems) == 1
    assert "database name" in problems[0]


def test_a_database_name_already_on_the_default_branch_is_left_alone():
    assert audit.check(_diff("+  MONGO_URI: mongodb://librechat-getklai@mongo/librechat-getklai")) == []


def test_a_table_comparing_the_two_paths_is_not_blocked():
    """The measurements this repo exists to record must stay writable."""
    diff = _diff("+| | Widget | Interne chat |", "+|---|---|---|", "+| Onbewezen | 75% | 85% |")

    assert audit.check(diff) == []


def test_describing_the_distribution_in_words_is_not_blocked():
    """The escape route the message points at has to actually work."""
    assert audit.check(_diff("+Een tenant had vrijwel alle antwoorden met bronverwijzing.")) == []


def test_a_separator_row_is_not_a_finding():
    assert audit.check(_diff("+| Tenant | Aantal |", "+|---|---|")) == []


def test_this_guards_own_tests_are_exempt():
    """This file holds the shapes the guard detects, so scanning it blocks every
    change to it. The exemption is one exact path: anything wider would let a
    real leak hide in a file with "test" in its name.
    """
    leak = "+| Tenant | Gebruik |\n+|---|---|\n+| privacy1 | 23 |"
    header = "+++ b/scripts/tests/test_audit_public_tenant_data.py\n@@ -1,0 +1,3 @@\n"

    assert audit.check(header + leak + "\n") == []
    assert audit.check("+++ b/scripts/tests/test_other.py\n@@ -1,0 +1,3 @@\n" + leak + "\n")


# ---- A customer name next to a number, in prose (added 2026-09-19) ----
#
# The table rule alone let "Acme Telecom had 272 answers" through. The names come
# from a private list at run time; a made-up one stands in for it here.

_NAMES = ["Acme Telecom", "Privacy9"]


def test_a_customer_count_in_a_sentence_is_blocked():
    assert audit.check(_diff("+Acme Telecom had 272 answers in thirty days."), _NAMES)


def test_a_sentence_wrapped_over_two_lines_is_still_one_sentence():
    """Markdown in this repo wraps at about a hundred characters, so the name and
    the number of the leaked sentence were on different lines."""
    problems = audit.check(_diff("+Over thirty days Acme Telecom had", "+272 answers with a citation."), _NAMES)

    assert problems == ["docs/x.md:1: a customer name next to a number"]


def test_a_customer_row_is_caught_whatever_the_header_says():
    """A table headed "| | Answers |" names no tenant, so the table rule missed it."""
    assert audit.check(_diff("+| | Answers |", "+|---|---|", "+| Acme Telecom | 272 |"), _NAMES)


def test_pr_numbers_dates_years_and_spec_ids_are_not_counts():
    line = "+Acme Telecom widget fix (#1520) on 18 sep 2026, see SPEC-ACME-TELECOM-001 and v2."
    assert audit.check(_diff(line), _NAMES) == []


def test_a_digit_inside_the_name_is_not_a_count():
    assert audit.check(_diff("+Privacy9 asked for an export."), _NAMES) == []


def test_code_is_checked_in_its_comments_only():
    """A fixture with an org slug and an id is not a leak; a comment reporting that
    org's volume is."""
    code = "klai-portal/backend/app/x.py"
    assert audit.check(_diff('+ORG = ("Acme Telecom", 42)', path=code), _NAMES) == []
    assert audit.check(_diff("+# Acme Telecom sends 40 tickets a day.", path=code), _NAMES)


def test_without_a_name_list_only_the_shape_rules_run():
    assert audit.check(_diff("+Acme Telecom had 272 answers."), []) == []


def test_plain_text_is_checked_like_added_lines():
    """Commit messages and pull-request bodies are not diffs."""
    problems = audit.check(audit.as_diff("Title\n\nAcme Telecom had 272 answers.", "pull-request"), _NAMES)

    assert problems == ["pull-request:3: a customer name next to a number"]


def test_names_come_from_the_environment_before_the_file(monkeypatch):
    monkeypatch.setenv("KLAI_TENANT_NAMES", "# comment\nAcme Telecom\n\n")

    assert audit.load_names() == ["Acme Telecom"]
