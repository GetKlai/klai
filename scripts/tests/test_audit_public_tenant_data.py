"""The guard has to catch the table that actually leaked, and nothing else.

A guard that blocks every markdown table would be turned off within a week, so
the negative cases matter as much as the positive one.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_MODULE = Path(__file__).resolve().parents[1] / "audit-public-tenant-data.py"
_spec = importlib.util.spec_from_file_location("audit_public_tenant_data", _MODULE)
audit = importlib.util.module_from_spec(_spec)
sys.modules["audit_public_tenant_data"] = audit
_spec.loader.exec_module(audit)


LEAK = """+++ b/docs/specs/SPEC-X/evolutie.md
+| Tenant | Antwoorden | Met bronverwijzing |
+|---|---|---|
+| privacy1 | 23 | 0 |
+| panography | 21 | 0 |
"""

MEASUREMENT = """+++ b/docs/specs/SPEC-X/evolutie.md
+| | Widget (80 beurten) | Interne chat (80 beurten) |
+|---|---|---|
+| Minstens 1 onbewezen uitspraak | 75% | 85% |
"""

PROSE = """+++ b/docs/specs/SPEC-X/evolutie.md
+Over dertig dagen had een tenant vrijwel alle antwoorden met bronverwijzing.
"""


def test_the_table_that_leaked_is_blocked(monkeypatch):
    monkeypatch.setattr(audit, "_known_librechat_names", lambda: set())

    problems = audit.check(LEAK)

    assert len(problems) == 2
    assert any("privacy1" in p for p in problems)
    assert any("panography" in p for p in problems)


def test_a_table_comparing_the_two_paths_is_not_blocked(monkeypatch):
    """The measurements this repo exists to record must stay writable."""
    monkeypatch.setattr(audit, "_known_librechat_names", lambda: set())

    assert audit.check(MEASUREMENT) == []


def test_describing_the_distribution_in_words_is_not_blocked(monkeypatch):
    """The escape route the message points at has to actually work."""
    monkeypatch.setattr(audit, "_known_librechat_names", lambda: set())

    assert audit.check(PROSE) == []


def test_a_customer_database_name_that_is_new_here_is_blocked(monkeypatch):
    monkeypatch.setattr(audit, "_known_librechat_names", lambda: {"getklai", "digest"})

    problems = audit.check("+++ b/deploy/x.yml\n+  MONGO_URI: mongodb://librechat-acme:pw@mongo/librechat-acme\n")

    assert len(problems) == 1
    assert "librechat-acme" in problems[0]


def test_a_database_name_already_on_the_default_branch_is_left_alone(monkeypatch):
    """Existing content is never re-judged; only what a commit adds."""
    monkeypatch.setattr(audit, "_known_librechat_names", lambda: {"getklai"})

    assert audit.check("+++ b/deploy/x.yml\n+  MONGO_URI: mongodb://librechat-getklai:pw@mongo/librechat-getklai\n") == []


def test_the_table_separator_row_is_not_a_finding(monkeypatch):
    monkeypatch.setattr(audit, "_known_librechat_names", lambda: set())

    assert audit.check("+++ b/docs/x.md\n+| Tenant | Aantal |\n+|---|---|\n") == []
