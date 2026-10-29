"""The name list must derive cleanly from portal rows and never blank the guard.

All names below are invented; hosts sit under the reserved .test top-level domain.
"""

from __future__ import annotations

import importlib.util
import stat
import sys
import textwrap
from pathlib import Path

import pytest

_MODULE = (
    Path(__file__).resolve().parents[2]
    / "deploy"
    / "scripts"
    / "refresh-tenant-names.py"
)
_spec = importlib.util.spec_from_file_location("refresh_tenant_names", _MODULE)
refresh = importlib.util.module_from_spec(_spec)
sys.modules["refresh_tenant_names"] = refresh
_spec.loader.exec_module(refresh)

ALLOWED = ["saas.test", "mailhost.test"]


def test_registrable_domain_keeps_three_labels_under_a_second_level_code():
    assert refresh.registrable("wiki.quuxcorp.test") == "quuxcorp.test"
    # Built from parts: a literal two-letter country code would read as a real host.
    for second in ("co", "com", "org", "net", "eu"):
        assert (
            refresh.registrable(f"a.b.quuxcorp.{second}.zz") == f"quuxcorp.{second}.zz"
        )
    assert refresh.registrable("a.b.quuxcorp.shop.zz") == "shop.zz"


def test_host_of_reads_urls_ports_and_bare_hosts():
    assert (
        refresh.host_of("https://Wiki.Quuxcorp.test:8443/path?q=1")
        == "wiki.quuxcorp.test"
    )
    assert refresh.host_of("quuxcorp.test") == "quuxcorp.test"
    assert refresh.host_of("") is None
    assert refresh.host_of("localhost") is None


def test_a_host_gives_its_registrable_domain_and_first_label():
    assert refresh.host_entries("wiki.quuxcorp.test", ALLOWED) == {
        "quuxcorp.test",
        "quuxcorp",
    }


def test_a_customer_on_an_allowed_platform_is_identified_by_the_label_below_it():
    assert refresh.host_entries("zorblax.saas.test", ALLOWED) == {"zorblax"}
    assert refresh.host_entries("team.zorblax.saas.test", ALLOWED) == {"zorblax"}


def test_a_bare_or_generic_platform_host_yields_nothing():
    assert refresh.host_entries("saas.test", ALLOWED) == set()
    assert refresh.host_entries("www.saas.test", ALLOWED) == set()
    assert refresh.host_entries("abc.saas.test", ALLOWED) == set()
    assert refresh.host_entries("support.saas.test", ALLOWED) == set()


def test_generic_and_short_labels_are_dropped():
    assert refresh.host_entries("support.test", ALLOWED) == set()
    assert refresh.host_entries("security.test", ALLOWED) == set()
    assert refresh.host_entries("abc.test", ALLOWED) == {"abc.test"}


def test_a_two_word_org_name_stays_whole():
    assert refresh.org_entries("Zorblax Labs", "", "") == {"zorblax labs"}


def test_a_person_named_org_yields_only_the_phrase():
    assert refresh.org_entries("Frits Jansen Advies", "", "") == {"frits jansen advies"}


def test_a_one_word_org_name_needs_to_be_its_own_slug_or_domain_label():
    assert refresh.org_entries("Security", "security", "security-example") == set()
    assert refresh.org_entries("Nestrix", "nestrix", "") == {"nestrix"}
    assert refresh.org_entries("Nestrix", "", "nestrix") == {"nestrix"}
    assert refresh.org_entries("Nestrix", "other", "other") == set()
    assert refresh.org_entries("Abcd", "abcd", "abcd") == set()


def test_an_email_gives_the_domain_side_only_and_skips_mail_providers():
    assert refresh.email_entries("anna@quuxcorp.test", ALLOWED) == {
        "quuxcorp.test",
        "quuxcorp",
    }
    assert refresh.email_entries("anna@mailhost.test", ALLOWED) == set()
    assert refresh.email_entries("no-at-sign", ALLOWED) == set()


def test_derive_counts_each_source_and_tolerates_null_columns():
    rows = {
        "orgs": [
            ["Zorblax Labs", "zorblax", "zorblax.test"],
            ["Security", "security", "security-example.test"],
            ["Plainco", "", ""],
        ],
        "users": [["anna@quuxcorp.test"]],
        "connectors": [["https://wiki.fizzbuzz.test/x", ""], ["", ""]],
    }

    out = refresh.derive(rows, ALLOWED)

    assert out["orgs"] == {
        "zorblax labs",
        "zorblax",
        "zorblax.test",
        "security-example.test",
        "security-example",
    }
    assert out["users"] == {"quuxcorp.test", "quuxcorp"}
    assert out["connectors"] == {"fizzbuzz.test", "fizzbuzz"}


def _rows(*rows: list[str]) -> str:
    """psql -A -F unit-separator -R record-separator output."""
    return "".join("\x1f".join(row) + "\x1e" for row in rows) + "\n"


def _fake_psql(
    tmp_path: Path, orgs: str, users: str, connectors: str, fail: str = ""
) -> str:
    script = tmp_path / "fake_psql.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import sys
            sql = sys.stdin.read()
            if {fail!r} and {fail!r} in sql:
                sys.stderr.write("ERROR: boom")
                sys.exit(1)
            if "portal_orgs" in sql:
                sys.stdout.write({orgs!r})
            elif "portal_users" in sql:
                sys.stdout.write({users!r})
            elif "portal_connectors" in sql:
                sys.stdout.write({connectors!r})
            """
        )
    )
    return f"{sys.executable} {script}"


@pytest.fixture
def allowed_file(tmp_path):
    path = tmp_path / "allowed.txt"
    path.write_text("# vendors\nsaas.test\n")
    return path


def _run(tmp_path, allowed_file, psql, *extra):
    out = tmp_path / "private" / "names.txt"
    code = refresh.main(
        ["--psql", psql, "--output", str(out), "--allowed", str(allowed_file), *extra]
    )
    return code, out


def test_a_run_writes_a_sorted_private_file_and_prints_no_names(
    tmp_path, allowed_file, capsys
):
    psql = _fake_psql(
        tmp_path,
        _rows(["Zorblax Labs", "zorblax", "zorblax.test"]),
        _rows(["anna@quuxcorp.test"], ["bob@quuxcorp.test"]),
        _rows(["https://wiki.fizzbuzz.test/x", ""]),
    )

    code, out = _run(tmp_path, allowed_file, psql)

    assert code == 0
    lines = out.read_text().splitlines()
    assert lines[0].startswith("# generated by deploy/scripts/refresh-tenant-names.py ")
    assert lines[1:] == sorted(set(lines[1:]))
    assert "fizzbuzz.test" in lines and "quuxcorp" in lines
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert stat.S_IMODE(out.parent.stat().st_mode) == 0o700
    printed = capsys.readouterr().out
    assert (
        "orgs: 3" in printed
        and "users: 2" in printed
        and "connectors: 2" in printed
        and "total: 7" in printed
    )
    assert "zorblax" not in printed and "fizzbuzz" not in printed


def test_an_empty_result_never_overwrites_the_list_and_fails(
    tmp_path, allowed_file, capsys
):
    out = tmp_path / "private" / "names.txt"
    out.parent.mkdir()
    out.write_text("keepme\n")

    code = refresh.main(
        [
            "--psql",
            "cat /dev/null",
            "--output",
            str(out),
            "--allowed",
            str(allowed_file),
        ]
    )

    assert code == 1
    assert out.read_text() == "keepme\n"
    assert "empty" in capsys.readouterr().err


def test_a_list_more_than_30_percent_shorter_is_refused(tmp_path, allowed_file, capsys):
    out = tmp_path / "private" / "names.txt"
    out.parent.mkdir()
    old = "".join(f"entry{i:02d}\n" for i in range(10))
    out.write_text(old)
    psql = _fake_psql(tmp_path, "", _rows(["anna@quuxcorp.test"]), "")

    code = refresh.main(
        ["--psql", psql, "--output", str(out), "--allowed", str(allowed_file)]
    )

    assert code == 1
    assert out.read_text() == old
    assert "shorter" in capsys.readouterr().err


def test_dry_run_reports_added_and_removed_and_writes_nothing(
    tmp_path, allowed_file, capsys
):
    out = tmp_path / "private" / "names.txt"
    out.parent.mkdir()
    out.write_text("# old\nquuxcorp\nstale-entry\n")
    psql = _fake_psql(tmp_path, "", _rows(["anna@quuxcorp.test"]), "")

    code = refresh.main(
        [
            "--psql",
            psql,
            "--output",
            str(out),
            "--allowed",
            str(allowed_file),
            "--dry-run",
        ]
    )

    assert code == 0
    assert out.read_text() == "# old\nquuxcorp\nstale-entry\n"
    assert "added: 1, removed: 1" in capsys.readouterr().out


def test_a_failing_database_command_stops_the_run(tmp_path, allowed_file):
    code, out = _run(tmp_path, allowed_file, "false")

    assert code == 1
    assert not out.exists()


def test_one_failing_source_leaves_the_list_untouched_and_names_the_source(
    tmp_path, allowed_file, capsys
):
    out = tmp_path / "private" / "names.txt"
    out.parent.mkdir()
    out.write_text("# old\nquuxcorp\n")
    psql = _fake_psql(
        tmp_path,
        _rows(["Zorblax Labs", "", ""]),
        _rows(["anna@quuxcorp.test"]),
        "",
        fail="portal_connectors",
    )

    code = refresh.main(
        ["--psql", psql, "--output", str(out), "--allowed", str(allowed_file)]
    )

    assert code == 1
    assert out.read_text() == "# old\nquuxcorp\n"
    err = capsys.readouterr().err
    assert "connectors" in err and "boom" not in err


def test_a_tab_or_newline_inside_a_value_does_not_break_the_row(tmp_path, allowed_file):
    psql = _fake_psql(
        tmp_path, _rows(["Zorblax\tLabs\nHolding", "", ""]), "", _rows(["", ""])
    )

    code, out = _run(tmp_path, allowed_file, psql)

    assert code == 0
    assert "zorblax labs holding" in out.read_text().splitlines()


def test_rows_split_on_the_unit_and_record_separators():
    raw = _rows(["a\tb", "c\r\n"], ["", "d"])

    assert refresh.parse_rows(raw) == [["a\tb", "c"], ["", "d"]]


def test_atomic_write_leaves_no_temp_file_behind(tmp_path):
    out = tmp_path / "p" / "names.txt"

    refresh.write_atomic(out, {"bbbb", "aaaa"}, "2026-10-29")

    assert (
        out.read_text()
        == "# generated by deploy/scripts/refresh-tenant-names.py 2026-10-29\naaaa\nbbbb\n"
    )
    assert [p.name for p in out.parent.iterdir()] == ["names.txt"]
