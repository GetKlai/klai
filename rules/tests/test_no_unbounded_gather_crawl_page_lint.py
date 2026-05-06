"""SPEC-INGEST-RECONCILE-001 Fix 4 / AC-12 — regression tests for the
``no-unbounded-gather-crawl-page`` ast-grep rule.

The rule lives at ``rules/no-unbounded-gather-crawl-page.yml`` and is
discovered via the repo-root ``sgconfig.yml``. We invoke ast-grep
directly (via ``uvx --from ast-grep-cli ast-grep``) on two fixture
files:

- ``fixtures/bad_unbounded_gather_crawl_page.py`` — contains exactly the
  legacy supplement-loop pattern (Bug A). The lint MUST exit non-zero
  and name the fixture file.
- ``fixtures/good_bulk_crawl.py`` — bulk POST /crawl path with no
  ``asyncio.gather`` over ``crawl_page`` anywhere. The lint MUST exit
  zero.

We also assert the knowledge-ingest CI workflow includes an
``ast-grep/action`` step so the lint runs on every PR that touches the
package (per AC-12 "CI-enforced" clause).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
RULES_DIR = REPO_ROOT / "rules"
FIXTURES_DIR = RULES_DIR / "tests" / "fixtures"
SGCONFIG = REPO_ROOT / "sgconfig.yml"
RULE_FILE = RULES_DIR / "no-unbounded-gather-crawl-page.yml"


def _is_ast_grep_binary(path: str) -> bool:
    """Return True iff ``path`` is actually ast-grep.

    Ubuntu ships ``/usr/bin/sg`` (set-group ID command from util-linux) which
    has nothing to do with ast-grep, and ``shutil.which("sg")`` finds it on
    default GHA runners. Verify by running ``<bin> --version`` and looking
    for "ast-grep" in the output. Falls back gracefully on FileNotFoundError
    or non-zero exit. Pattern copied from
    ``rules/tests/test_cors_middleware_last_lint.py`` for parity.
    """
    try:
        result = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    blob = (result.stdout or "") + (result.stderr or "")
    return "ast-grep" in blob.lower()


def _ast_grep_command() -> list[str]:
    """Resolve a runnable ast-grep CLI invocation.

    Order:
    1. ``ast-grep`` on PATH (developer machines via brew/apt) — verified.
    2. ``sg`` on PATH (the alternate name) — verified to actually be ast-grep.
    3. ``uvx --from ast-grep-cli ast-grep`` (CI fallback — no global install).
    """
    for name in ("ast-grep", "sg"):
        path = shutil.which(name)
        if path and _is_ast_grep_binary(path):
            return [path]
    if shutil.which("uvx"):
        return ["uvx", "--from", "ast-grep-cli", "ast-grep"]
    pytest.skip("ast-grep / sg / uvx not available on PATH — skipping lint regression test")


@pytest.fixture(scope="module")
def ast_grep_cmd() -> list[str]:
    return _ast_grep_command()


def test_rule_file_is_valid_yaml() -> None:
    """The rule file MUST be loadable as YAML and declare the expected id."""
    with RULE_FILE.open(encoding="utf-8") as fh:
        rule = yaml.safe_load(fh)
    assert rule["id"] == "no-unbounded-gather-crawl-page"
    assert rule["language"] == "python"
    assert rule["severity"] == "error"
    # Intentionally no ``files:`` allow-list — see docstring in the rule
    # file. The pattern is unique enough that path scoping adds nothing
    # and breaks the regression-test fixture path.
    assert "files" not in rule


def test_bad_fixture_is_flagged(ast_grep_cmd: list[str]) -> None:
    """The unbounded-gather supplement-loop pattern MUST be flagged."""
    fixture = FIXTURES_DIR / "bad_unbounded_gather_crawl_page.py"
    assert fixture.exists()

    proc = subprocess.run(
        [*ast_grep_cmd, "scan", "--config", str(SGCONFIG), str(fixture)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode != 0, (
        f"ast-grep returned 0 on the BAD fixture — rule did not fire.\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "no-unbounded-gather-crawl-page" in combined or "asyncio.gather" in combined, (
        f"ast-grep flagged something else on the BAD fixture — output was:\n{combined}"
    )


def test_good_fixture_is_not_flagged(ast_grep_cmd: list[str]) -> None:
    """The bulk-crawl shape MUST pass the lint cleanly."""
    fixture = FIXTURES_DIR / "good_bulk_crawl.py"
    assert fixture.exists()

    proc = subprocess.run(
        [*ast_grep_cmd, "scan", "--config", str(SGCONFIG), str(fixture)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    assert proc.returncode == 0, (
        f"ast-grep returned non-zero on the GOOD fixture — rule false-positive.\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


def test_knowledge_ingest_workflow_invokes_ast_grep() -> None:
    """AC-12 CI-enforced clause: the knowledge-ingest workflow MUST
    include the ast-grep action so the rule fires on every PR."""
    workflow = REPO_ROOT / ".github" / "workflows" / "knowledge-ingest.yml"
    assert workflow.exists(), "Missing CI workflow .github/workflows/knowledge-ingest.yml"
    text = workflow.read_text(encoding="utf-8")
    assert "ast-grep/action" in text, (
        "knowledge-ingest CI workflow does not call ast-grep/action — "
        "the no-unbounded-gather-crawl-page rule has no CI hook."
    )
