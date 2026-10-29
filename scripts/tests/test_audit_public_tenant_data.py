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
    problems = audit.check(_diff("+| Tenant | Antwoorden |", "+|---|---|", "+| acme-labs | 23 |"))

    assert len(problems) == 1
    assert problems[0].startswith("docs/x.md:")


def test_a_finding_never_repeats_the_customer_row():
    """CI logs of a public repository are public.

    Echoing the match would copy the very data the guard blocks into a second,
    permanent place.
    """
    problems = audit.check(_diff("+| Tenant | Antwoorden |", "+|---|---|", "+| acme-labs | 23 |"))

    assert "acme-labs" not in problems[0]
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
    leak = "+| Tenant | Gebruik |\n+|---|---|\n+| acme-labs | 23 |"
    header = "+++ b/scripts/tests/test_audit_public_tenant_data.py\n@@ -1,0 +1,3 @@\n"

    assert audit.check(header + leak + "\n") == []
    assert audit.check("+++ b/scripts/tests/test_other.py\n@@ -1,0 +1,3 @@\n" + leak + "\n")


# ---- A customer name, with or without a number ----
#
# Rule 3 let "Acme Telecom had 272 answers" through at first; rule 4 (2026-10-29)
# blocks the name alone, because a customer's wiki hostname then sat in 46 files
# of tests, fixtures and comments with no number beside it. The names come from a
# private list at run time; made-up ones stand in for it here.

_NAMES = ["Acme Telecom", "Privacy9", "wiki.example-customer.cloud"]


def test_a_customer_count_in_a_sentence_is_blocked():
    assert audit.check(_diff("+Acme Telecom had 272 answers in thirty days."), _NAMES)


def test_a_bare_customer_name_is_blocked_whatever_the_file_or_line():
    """Prose, a comment, a fixture, a string in code: the name alone is the leak."""
    for path, line in (
        ("docs/x.md", "+Acme Telecom asked for an export."),
        ("klai-portal/backend/app/x.py", "+# Acme Telecom sends tickets in bursts."),
        ("klai-portal/backend/app/x.py", '+ORG = {"name": "Acme Telecom"}'),
        ("klai-portal/frontend/src/x.tsx", "+const org = 'acme telecom'"),
    ):
        assert audit.check(_diff(line, path=path), _NAMES) == [f"{path}:1: customer-name"]


def test_a_customer_name_in_a_commit_message_is_blocked():
    problems = audit.check(audit.as_diff("fix: widget\n\nAcme Telecom hit it first.", "commit-messages"), _NAMES)

    assert problems == ["commit-messages:3: customer-name"]


def test_a_name_that_is_a_hostname_matches_inside_urls_and_dotted_hosts():
    for line in (
        "+curl https://wiki.example-customer.cloud/api/v1",
        "+host = 'sso.wiki.example-customer.cloud'",
        "+see wiki.example-customer.cloud.",
        "+mail admin@wiki.example-customer.cloud",
    ):
        assert audit.check(_diff(line), _NAMES) == ["docs/x.md:1: customer-name"], line


def test_a_bare_slug_is_found_as_one_label_of_a_host():
    assert audit.check(_diff("+https://wiki.privacy9.example.com/x"), _NAMES)


def test_a_name_inside_a_longer_word_is_not_a_match():
    for line in ("+Privacy9x asked", "+my-privacy9 asked", "+privacy9_tenant", "+xAcme Telecom"):
        assert audit.check(_diff(line), _NAMES) == [], line


def test_removed_lines_and_context_lines_are_not_findings():
    diff = "+++ b/docs/x.md\n@@ -1,2 +1,1 @@\n-Acme Telecom was here\n Privacy9 stays\n"

    assert audit.check(diff, _NAMES) == []


def test_a_finding_for_a_name_never_repeats_it():
    problems = audit.check(_diff("+Acme Telecom had 272 answers."), _NAMES)

    assert "acme" not in problems[0].lower()


def test_without_a_name_list_only_the_shape_rules_run():
    assert audit.check(_diff("+Acme Telecom had 272 answers."), []) == []


def test_plain_text_is_checked_like_added_lines():
    """Commit messages and pull-request bodies are not diffs."""
    problems = audit.check(audit.as_diff("Title\n\nAcme Telecom had 272 answers.", "pull-request"), _NAMES)

    assert problems == ["pull-request:3: customer-name"]


def test_names_come_from_the_environment_before_the_file(monkeypatch):
    monkeypatch.setenv("KLAI_TENANT_NAMES", "# comment\nAcme Telecom\n\n  \n# another\nwiki.example-customer.cloud\n")

    assert audit.load_names() == ["Acme Telecom", "wiki.example-customer.cloud"]


def test_the_warning_without_a_list_says_rules_3_and_4_are_off(monkeypatch, capsys):
    monkeypatch.delenv("KLAI_TENANT_NAMES", raising=False)
    monkeypatch.setattr(audit, "_NAMES_FILE", Path("/nonexistent/tenant-names.txt"))
    monkeypatch.setattr(sys, "argv", ["audit", "--text", "x"])
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO("nothing here\n"))

    assert audit.main() == 0
    assert "rules 3 and 4 are OFF" in capsys.readouterr().err


# ---- A real hostname in a test or fixture (rule 5, no list needed) ----

_TEST = "klai-portal/backend/tests/test_x.py"


def _host(line: str, path: str = _TEST) -> list[str]:
    return audit.check(_diff(line, path=path))


def test_a_real_looking_hostname_in_a_test_is_blocked_in_every_shape():
    for line in (
        '+URL = "https://wiki.example-customer.cloud/api"',
        "+base = https://wiki.example-customer.cloud",
        "+owner = 'jane@mail.example-customer.nl'",
        "+# talks to intranet.example-customer.cloud",
        "+host: shop.example-customer.com:8443",
    ):
        assert _host(line) == [f"{_TEST}:1: real-hostname"], line


def test_the_rule_covers_every_test_and_fixture_location():
    line = "+url = 'https://wiki.example-customer.cloud'"
    for path in (
        "svc/tests/a.py",
        "svc/test/a.py",
        "web/src/__tests__/a.ts",
        "web/src/a.test.tsx",
        "web/src/a.spec.ts",
        "web/e2e/prod-tenant/a.ts",
        "web/src/components/Button.stories.tsx",
        "svc/fixtures/a.json",
        "svc/seeds/a.sql",
        "svc/app/foo_test.py",
        "deploy/scripts/tests/a.test.sh",
    ):
        assert _host(line, path) == [f"{path}:1: real-hostname"], path


def test_production_code_is_not_judged_by_the_hostname_rule():
    assert _host("+URL = 'https://wiki.example-customer.cloud'", "svc/app/client.py") == []


def test_a_hostname_finding_never_repeats_the_hostname():
    problems = _host("+URL = 'https://wiki.example-customer.cloud'")

    assert "example-customer" not in problems[0]


def test_reserved_and_local_hosts_pass():
    for line in (
        "+url = 'https://example.com/x'",
        "+url = 'https://api.example.org'",
        "+url = 'https://deep.sub.example.net/x?y=1'",
        "+mail = 'jane@example.com'",
        "+url = 'http://localhost:8000/health'",
        "+url = 'http://wiki.localhost'",
        "+url = 'https://kb.customer.test'",
        "+url = 'https://kb.customer.example'",
        "+url = 'https://kb.customer.invalid'",
        "+url = 'http://127.0.0.1:8080'",
        "+url = 'http://[::1]:8080'",
        "+url = 'http://10.0.0.5/x'",
    ):
        assert _host(line) == [], line


def test_a_domain_in_the_allow_file_passes_with_its_subdomains():
    assert _host("+url = 'https://api.getklai.com/x'") == []
    assert _host("+url = 'https://raw.githubusercontent.com/x'") == []
    assert _host("+image = ghcr.io/org/app:1") == []
    assert _host("+url = 'https://notgetklai.com'") == [f"{_TEST}:1: real-hostname"]


def test_shell_escapes_and_parameter_defaults_do_not_glue_onto_a_host():
    """``$'\\tghcr.io'`` and ``${IMAGE:-ghcr.io/x}`` name the allowed ghcr.io, not tghcr.io or -ghcr.io."""
    assert _host("+check $'\\timage\\tghcr.io/org/app:1\\tdeployed$'", "deploy/scripts/tests/a.test.sh") == []
    assert _host('+run "${IMAGE:-ghcr.io/org/app:1}"', "deploy/scripts/tests/a.test.sh") == []
    assert _host('+run "${URL:-https://wiki.example-customer.cloud}"') == [f"{_TEST}:1: real-hostname"]


def test_the_allow_file_is_comment_and_blank_tolerant(monkeypatch, tmp_path):
    allow = tmp_path / "allowed.txt"
    allow.write_text("# header\n\nvendor-one.com  # inline note\n  Vendor-Two.IO\n")
    monkeypatch.setattr(audit, "_ALLOWED_DOMAINS_FILE", allow)

    assert audit._allowed_domains() == ["vendor-one.com", "vendor-two.io"]
    assert _host("+u = 'https://api.vendor-one.com'") == []
    assert _host("+u = 'https://vendor-two.io'") == []
    assert _host("+u = 'https://getklai.com'") == [f"{_TEST}:1: real-hostname"]


def test_module_paths_versions_and_file_names_are_not_hostnames():
    for line in (
        "+from knowledge_ingest.routes.crawl import router",
        "+patch('knowledge_ingest.routes.crawl.fetch')",
        "+`knowledge_ingest/routes/crawl.py` and `src/a.ts`",
        "+assert version == '1.2.3'",
        "+pin = 'v10.4.0-rc.1'",
        "+expect(screen.getByRole('button')).toBeVisible()",
        "+assert row.id == 1 and item.name == 'x'",
        "+open('fixtures/data.json')",
        "+# see README.md and config.yaml",
    ):
        assert _host(line) == [], line


def test_this_guards_own_tests_are_exempt_from_the_hostname_rule():
    line = "+URL = 'https://wiki.example-customer.cloud'"

    assert _host(line, "scripts/tests/test_audit_public_tenant_data.py") == []


# ---- The whole tree (--tree), rules 4 and 5 only ----


def _repo(tmp_path, files: dict[str, str]):
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return tmp_path


def test_tree_mode_reports_file_line_and_kind_for_existing_content(tmp_path):
    root = _repo(
        tmp_path,
        {
            "docs/a.md": "clean\nAcme Telecom was here\n",
            "svc/tests/test_a.py": "ok = 'https://example.com'\nbad = 'https://wiki.example-customer.cloud'\n",
            "svc/app/b.py": "URL = 'https://wiki.example-customer.cloud'\n",
            "img.bin": "\x00\xff\xfe",
        },
    )

    assert audit.scan_tree(root, _NAMES) == [
        "docs/a.md:2: customer-name",
        "svc/app/b.py:1: customer-name",
        "svc/tests/test_a.py:2: customer-name",
        "svc/tests/test_a.py:2: real-hostname",
    ]


def test_tree_mode_prints_only_locations_and_a_total(tmp_path, monkeypatch, capsys):
    root = _repo(tmp_path, {"svc/tests/test_a.py": "bad = 'https://wiki.example-customer.cloud'\n"})
    monkeypatch.chdir(root)
    monkeypatch.delenv("KLAI_TENANT_NAMES", raising=False)
    monkeypatch.setattr(audit, "_NAMES_FILE", Path("/nonexistent/tenant-names.txt"))
    monkeypatch.setattr(sys, "argv", ["audit", "--tree"])

    assert audit.main() == 1
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "svc/tests/test_a.py:1: real-hostname"
    assert out.splitlines()[-1] == "total: 1"
    assert "example-customer" not in out
