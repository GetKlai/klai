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
