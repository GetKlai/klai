"""Regression tests for the no-leftmost-x-forwarded-for ast-grep rule."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = REPO_ROOT / "rules" / "tests" / "fixtures"
SGCONFIG = REPO_ROOT / "sgconfig.yml"


def _is_ast_grep_binary(path: str) -> bool:
    try:
        result = subprocess.run(  # noqa: S603
            [path, "--version"], capture_output=True, text=True, timeout=5, check=False
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    blob = (result.stdout or "") + (result.stderr or "")
    return "ast-grep" in blob.lower()


def _ast_grep_cli() -> list[str] | None:
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
    return subprocess.run(  # noqa: S603
        [*cli, "scan", "--config", str(SGCONFIG), str(target)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_lint_fails_on_leftmost_xff_parser(ast_grep_cli: list[str]) -> None:
    fixture = FIXTURES_DIR / "bad_leftmost_xff.py"
    result = _scan(ast_grep_cli, fixture)
    combined = result.stdout + result.stderr

    assert result.returncode != 0, combined
    assert "no-leftmost-x-forwarded-for" in combined


def test_lint_allows_resolve_caller_ip(ast_grep_cli: list[str]) -> None:
    fixture = FIXTURES_DIR / "good_resolve_caller_ip_leftmost_xff.py"
    result = _scan(ast_grep_cli, fixture)

    assert result.returncode == 0, result.stdout + result.stderr


def test_lint_fails_on_raw_forwarded_headers(ast_grep_cli: list[str]) -> None:
    fixture = FIXTURES_DIR / "bad_raw_forwarded_headers.py"
    result = _scan(ast_grep_cli, fixture)
    combined = result.stdout + result.stderr

    assert result.returncode != 0, combined
    assert "no-raw-forwarded-headers" in combined


def test_lint_allows_request_state_for_origin(ast_grep_cli: list[str]) -> None:
    fixture = FIXTURES_DIR / "good_request_state_raw_forwarded_headers.py"
    result = _scan(ast_grep_cli, fixture)

    assert result.returncode == 0, result.stdout + result.stderr
