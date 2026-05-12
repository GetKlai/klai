"""SPEC-PORTAL-PRICING-PER-USER-001 AC-13 — regression tests for the
``no-profile-derives-seat`` ast-grep rule.

The rule lives at ``rules/no-profile-derives-seat.yml`` and is
discovered via the repo-root ``sgconfig.yml``. We invoke ast-grep
directly (via ``uvx --from ast-grep-cli ast-grep`` if no local binary
is available) on six fixture files:

  * ``bad_seat_dict_full_*.py``     — full role -> SeatType dict        (FAIL)
  * ``bad_seat_dict_partial_*.py``  — subset of role keys with SeatType  (FAIL)
  * ``bad_seat_subscript_assign_*.py`` — subscript-assign to SeatType    (FAIL)
  * ``good_suggest_seat_call_*.py``  — uses ``suggest_seat()``           (PASS)
  * ``good_literal_no_role_*.py``    — bare SeatType, no role coupling    (PASS)
  * canonical ``app/core/seats.py``  — DEFAULT_SEAT_FOR_ROLE site         (PASS via ``ignores``)

If a future contributor weakens the rule (or accidentally adds a role
-> SeatType mapping outside ``seats.py``), this suite fails before the
PR can land.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RULES_DIR = REPO_ROOT / "rules"
FIXTURES_DIR = RULES_DIR / "tests" / "fixtures"
SGCONFIG = REPO_ROOT / "sgconfig.yml"
SEATS_MODULE = REPO_ROOT / "klai-portal" / "backend" / "app" / "core" / "seats.py"


def _is_ast_grep_binary(path: str) -> bool:
    """Return True iff ``path`` is actually ast-grep.

    Ubuntu ships ``/usr/bin/sg`` (set-group ID command), which has nothing
    to do with ast-grep. ``shutil.which("sg")`` finds that binary on GHA
    runners. Verify by reading the version banner.
    """
    try:
        result = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=5, check=False
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    blob = (result.stdout or "") + (result.stderr or "")
    return "ast-grep" in blob.lower()


def _ast_grep_cli() -> list[str] | None:
    """Resolve the ast-grep CLI invocation. Mirrors the CORS-lint test."""
    if (sg := shutil.which("sg")) is not None and _is_ast_grep_binary(sg):
        return [sg]
    if (ag := shutil.which("ast-grep")) is not None and _is_ast_grep_binary(ag):
        return [ag]
    if (uvx := shutil.which("uvx")) is not None:
        return [uvx, "--from", "ast-grep-cli", "ast-grep"]
    return None


@pytest.fixture(scope="module")
def ast_grep_cli() -> list[str]:
    cli = _ast_grep_cli()
    if cli is None:
        pytest.skip("ast-grep CLI not available (no `sg`, `ast-grep`, or `uvx`)")
    return cli


def _scan(cli: list[str], target: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*cli, "scan", "--config", str(SGCONFIG), str(target)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


# ---------------------------------------------------------------------------
# Bad fixtures — rule MUST fire
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture_name",
    [
        "bad_seat_dict_full_no_profile_derives_seat.py",
        "bad_seat_dict_partial_no_profile_derives_seat.py",
        "bad_seat_subscript_assign_no_profile_derives_seat.py",
    ],
)
def test_lint_fails_on_bad_fixture(
    ast_grep_cli: list[str], fixture_name: str
) -> None:
    """AC-13: the rule MUST flag each anti-pattern shape.

    Each fixture exhibits exactly one of the three documented anti-pattern
    shapes (full dict, partial dict, subscript-assign). The rule must
    return non-zero AND its id must appear in the output so a future
    debugger can immediately see *which* rule fired.
    """
    fixture = FIXTURES_DIR / fixture_name
    assert fixture.exists(), fixture
    result = _scan(ast_grep_cli, fixture)

    combined = result.stdout + result.stderr
    assert result.returncode != 0, (
        f"Lint did not fail on {fixture_name} (exit {result.returncode}). "
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "no-profile-derives-seat" in combined, (
        f"Lint output does not reference rule id for {fixture_name}. "
        f"Output:\n{combined}"
    )


# ---------------------------------------------------------------------------
# Good fixtures — rule MUST stay silent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture_name",
    [
        "good_suggest_seat_call_no_profile_derives_seat.py",
        "good_literal_no_role_no_profile_derives_seat.py",
    ],
)
def test_lint_passes_on_good_fixture(
    ast_grep_cli: list[str], fixture_name: str
) -> None:
    """AC-13: legitimate use-sites must NOT trip the rule.

    ``suggest_seat()`` is the sanctioned helper; bare SeatType literals
    that don't co-occur with a role string are not the anti-pattern.
    """
    fixture = FIXTURES_DIR / fixture_name
    assert fixture.exists(), fixture
    result = _scan(ast_grep_cli, fixture)

    combined = result.stdout + result.stderr
    assert result.returncode == 0, (
        f"Lint incorrectly flagged {fixture_name} (exit {result.returncode}). "
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "no-profile-derives-seat" not in combined, (
        f"Rule fired on good fixture {fixture_name}. Output:\n{combined}"
    )


# ---------------------------------------------------------------------------
# Canonical site — ``seats.py`` IS the mapping; rule must skip it via ``ignores``
# ---------------------------------------------------------------------------


def test_canonical_seats_module_is_excluded(ast_grep_cli: list[str]) -> None:
    """The canonical mapping site at ``app/core/seats.py`` (and its
    ``DEFAULT_SEAT_FOR_ROLE`` dict) is the SINGLE allowed location.

    The ``ignores:`` clause in the rule excludes this exact file. If a
    refactor renames the module or weakens the exclusion, this test
    catches the regression before the rule starts noise-flagging the
    canonical site.
    """
    assert SEATS_MODULE.exists(), SEATS_MODULE
    result = _scan(ast_grep_cli, SEATS_MODULE)

    combined = result.stdout + result.stderr
    assert result.returncode == 0, (
        f"Lint flagged the canonical seats.py (exit {result.returncode}). "
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "no-profile-derives-seat" not in combined, (
        f"Rule fired on canonical seats.py. Check ``ignores`` clause. "
        f"Output:\n{combined}"
    )
