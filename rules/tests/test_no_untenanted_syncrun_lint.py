"""SPEC-SEC-PORTAL-RLS-001 REQ-3 — synthetic regression tests for the
``no-untenanted-syncrun-query`` ast-grep rule.

The rule lives at ``rules/no-untenanted-syncrun-query.yml`` and is
discovered via the repo-root ``sgconfig.yml``. We invoke ast-grep
directly against two fixture files:

- ``fixtures/bad_syncrun_no_tenant.py`` — ``select(SyncRun)`` without a
  ``SyncRun.org_id`` constraint anywhere in the chain. The lint MUST
  exit non-zero and name the fixture file.
- ``fixtures/good_syncrun_tenant_scoped.py`` — ``select(SyncRun)`` with
  ``.where(SyncRun.org_id == org_id)``. The lint MUST exit zero.

These tests are the mechanical guard for TP-5 (audit
``reports/audit-2026-05-04/tenant-scoping.md``): connector.sync_runs has
no Postgres RLS, so the application filter is the only barrier. If a
future refactor drops the org_id clause, this rule fires before merge.
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


def _ast_grep_cli() -> list[str] | None:
    """Resolve the ast-grep CLI invocation.

    Prefers a system-installed ``sg`` or ``ast-grep``; falls back to
    ``uvx --from ast-grep-cli ast-grep`` so the test works in CI without
    an explicit install step.
    """
    if (sg := shutil.which("sg")) is not None:
        return [sg]
    if (ag := shutil.which("ast-grep")) is not None:
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


def test_lint_fails_on_untenanted_syncrun(ast_grep_cli: list[str]) -> None:
    """REQ-3: the rule MUST flag ``select(SyncRun)`` without ``org_id``.

    Without this guard, a refactor that drops the application-level
    tenant filter would not fail CI — and connector.sync_runs has no
    Postgres RLS to fall back on (see TP-5).
    """
    fixture = FIXTURES_DIR / "bad_syncrun_no_tenant.py"
    assert fixture.exists(), fixture
    result = _scan(ast_grep_cli, fixture)

    combined = result.stdout + result.stderr
    assert result.returncode != 0, (
        f"Lint did not fail on bad fixture (exit {result.returncode}). "
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "bad_syncrun_no_tenant.py" in combined, (
        f"Lint output does not name the fixture. Output:\n{combined}"
    )
    assert "no-untenanted-syncrun-query" in combined, (
        f"Lint output does not reference the rule id. Output:\n{combined}"
    )


def test_lint_passes_on_tenant_scoped_syncrun(ast_grep_cli: list[str]) -> None:
    """REQ-3: the rule MUST NOT flag the canonical tenant-scoped pattern."""
    fixture = FIXTURES_DIR / "good_syncrun_tenant_scoped.py"
    assert fixture.exists(), fixture
    result = _scan(ast_grep_cli, fixture)

    # ast-grep returns exit 0 when no matches are found.
    assert result.returncode == 0, (
        f"Lint failed unexpectedly on good fixture (exit {result.returncode}). "
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "no-untenanted-syncrun-query" not in result.stdout, (
        f"Lint flagged the good fixture. Output:\n{result.stdout}"
    )
