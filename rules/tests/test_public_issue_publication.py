"""Regression guards for public GitHub issue publication boundaries."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
HOOK = REPO_ROOT / ".claude" / "hooks" / "klai" / "public-github-mutation-guard.py"
SECURITY_POLICY = REPO_ROOT / ".github" / "SECURITY.md"


def _run_hook(command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(HOOK)],
        input=json.dumps({"tool_input": {"command": command}}),
        capture_output=True,
        check=False,
        text=True,
    )


def test_only_allowlisted_workflow_can_create_public_issues() -> None:
    allowed = {"ci-failure-notice.yml"}
    publishers: set[str] = set()

    for workflow in (*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")):
        source = workflow.read_text()
        if "issues: write" in source or "github.rest.issues.create(" in source:
            publishers.add(workflow.name)

    assert publishers == allowed


def test_oidc_drift_failure_is_private_and_propagates_exit_code() -> None:
    workflow = (WORKFLOWS / "zitadel-oidc-drift.yml").read_text()

    assert "detailed output suppressed" in workflow
    assert "private email/web notifications" in workflow
    assert "set +e" in workflow
    assert 'echo "exit_code=$status"' in workflow
    assert "cat drift-report.json" not in workflow
    assert "cat drift-stderr.txt" not in workflow
    assert "github.rest.issues" not in workflow


def test_security_policy_points_reporters_to_private_reporting() -> None:
    policy = SECURITY_POLICY.read_text()

    assert "Do not open a public issue" in policy
    assert "Security → Report a vulnerability" in policy


@pytest.mark.parametrize(
    "command",
    [
        "gh issue create --title 'finding' --body 'details'",
        "gh issue edit 42 --body 'new details'",
        "gh issue comment 42 --body 'new details'",
        "gh issue close 42",
        "gh api repos/GetKlai/klai/issues -X POST -f title=finding",
        "gh api -X PATCH repos/GetKlai/klai/issues/42 -f state=closed",
        "gh api graphql -f query='mutation { closeIssue(input: {}) { issue { id } } }'",
        "curl https://api.github.com/repos/GetKlai/klai/issues -d '{}'",
    ],
)
def test_hook_blocks_unapproved_public_issue_mutations(command: str) -> None:
    result = _run_hook(command)

    assert result.returncode == 2
    assert "autonomous public GitHub issue mutation" in result.stderr


def test_hook_allows_read_only_issue_commands() -> None:
    result = _run_hook("gh issue view 1209 --json title,body")

    assert result.returncode == 0


def test_hook_allows_explicitly_authorized_public_issue_mutation() -> None:
    result = _run_hook(
        "KLAI_ALLOW_PUBLIC_ISSUE_MUTATION=1 "
        "gh issue comment 42 --body 'user-authorized update'"
    )

    assert result.returncode == 0
