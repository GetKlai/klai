"""SPEC-SEC-CORS-001 AC-18 — synthetic regression tests for the
`cors-middleware-must-be-last` ast-grep rule.

The rule lives at `rules/cors_middleware_last.yml` and is discovered via the
repo-root `sgconfig.yml`. We invoke ast-grep directly (via `uvx --from
ast-grep-cli ast-grep`) on two fixture files:

- `fixtures/bad_middleware_order.py` — registers CORSMiddleware BEFORE another
  add_middleware. The lint MUST exit non-zero and name the fixture file.
- `fixtures/good_middleware_order.py` — registers CORSMiddleware LAST. The
  lint MUST exit zero.

We also assert the per-service CI workflow files include an `ast-grep/action`
step so the lint runs on every PR that touches a listed entry module
(REQ-6.3).
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

# REQ-6.3 — every klai FastAPI service whose entry module is in scope of the
# lint. The CI workflow wiring assertion (AC-18 "CI wiring" clause) checks
# that each of these workflow files declares the ast-grep step.
SERVICE_WORKFLOW_FILES = [
    ".github/workflows/portal-api.yml",
    ".github/workflows/klai-connector.yml",
    ".github/workflows/retrieval-api.yml",
    ".github/workflows/scribe-api.yml",
    ".github/workflows/knowledge-ingest.yml",
    ".github/workflows/klai-mailer.yml",
    ".github/workflows/klai-knowledge-mcp.yml",
]


def _ast_grep_cli() -> list[str] | None:
    """Resolve the ast-grep CLI invocation.

    Prefers a system-installed `sg` or `ast-grep`; falls back to
    `uvx --from ast-grep-cli ast-grep` so the test works in CI without an
    explicit install step.
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


@pytest.mark.parametrize(
    "fixture_name",
    [
        "bad_middleware_order.py",  # branch 1 — sibling at module level
        "bad_middleware_order_nested.py",  # branch 2 — CORS inside if-block
    ],
)
def test_lint_fails_on_bad_fixture(ast_grep_cli: list[str], fixture_name: str) -> None:
    """AC-18: the rule MUST flag CORSMiddleware-not-last regressions, both at
    the simple sibling case AND the nested-if case (connector pattern).
    """
    fixture = FIXTURES_DIR / fixture_name
    assert fixture.exists(), fixture
    result = _scan(ast_grep_cli, fixture)

    combined = result.stdout + result.stderr
    assert result.returncode != 0, (
        f"Lint did not fail on {fixture_name} (exit {result.returncode}). "
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert fixture_name in combined, (
        f"Lint output does not name {fixture_name}. Output:\n{combined}"
    )
    assert "cors-middleware-must-be-last" in combined, (
        f"Lint output does not reference rule id. Output:\n{combined}"
    )


def test_lint_passes_on_good_fixture(ast_grep_cli: list[str]) -> None:
    """AC-18: the rule MUST NOT flag the canonical correct order."""
    fixture = FIXTURES_DIR / "good_middleware_order.py"
    assert fixture.exists(), fixture
    result = _scan(ast_grep_cli, fixture)

    assert result.returncode == 0, (
        f"Lint failed unexpectedly on good fixture (exit {result.returncode}). "
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


@pytest.mark.parametrize("workflow_path", SERVICE_WORKFLOW_FILES)
def test_workflow_wires_ast_grep_lint(workflow_path: str) -> None:
    """AC-18 (CI wiring): each in-scope service workflow must declare an
    `ast-grep/action` step that targets the cors_middleware_last rule via
    `sgconfig.yml`. We do not pin the exact action SHA here — only that the
    step exists and references `sgconfig.yml`.
    """
    full_path = REPO_ROOT / workflow_path
    assert full_path.exists(), f"workflow file missing: {workflow_path}"

    data = yaml.safe_load(full_path.read_text())
    assert isinstance(data, dict), workflow_path

    jobs = data.get("jobs") or {}
    assert jobs, f"no jobs declared in {workflow_path}"

    found_ast_grep_step = False
    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            continue
        for step in job.get("steps", []) or []:
            uses = (step or {}).get("uses", "")
            if not isinstance(uses, str) or "ast-grep/action" not in uses:
                continue
            with_block = (step or {}).get("with", {}) or {}
            config = with_block.get("config", "")
            if "sgconfig.yml" in str(config):
                found_ast_grep_step = True
                break
        if found_ast_grep_step:
            break

    assert found_ast_grep_step, (
        f"{workflow_path}: no ast-grep/action step found that references "
        "sgconfig.yml. SPEC-SEC-CORS-001 REQ-6.3 requires the lint to run "
        "on every PR that modifies the service's entry module."
    )
