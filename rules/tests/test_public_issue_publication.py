"""Regression guards for public GitHub publication boundaries."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
HOOK = REPO_ROOT / ".claude" / "hooks" / "klai" / "public-github-mutation-guard.py"
SECURITY_POLICY = REPO_ROOT / ".github" / "SECURITY.md"
PUBLICATION_WORKFLOW = WORKFLOWS / "public-disclosure-guard.yml"
RULES_WORKFLOW = WORKFLOWS / "rules-tests.yml"
CLAUDE_SETTINGS = REPO_ROOT / ".claude" / "settings.json"
GITLEAKS_CONFIG = REPO_ROOT / ".gitleaks.toml"


def _run_hook(
    command: str, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(HOOK)],
        input=json.dumps({"tool_input": {"command": command}}),
        capture_output=True,
        check=False,
        cwd=cwd,
        text=True,
    )


def _init_git_repo(path: Path, branch: str) -> None:
    subprocess.run(
        ["git", "init", f"--initial-branch={branch}", str(path)],
        capture_output=True,
        check=True,
        text=True,
    )


def _run_publication_workflow(
    payload: dict[str, object],
    *,
    gitleaks_detected: bool = False,
    existing_comments: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    workflow = yaml.safe_load(PUBLICATION_WORKFLOW.read_text())
    script = next(
        step["with"]["script"]
        for step in workflow["jobs"]["guard"]["steps"]
        if step.get("id") == "mitigate"
    )
    harness = f"""
const calls = [];
const failures = [];
const existingComments = JSON.parse(process.env.TEST_EXISTING_COMMENTS);
const github = {{
  rest: {{
    pulls: {{ update: async (args) => calls.push({{ endpoint: 'pulls', args }}) }},
    issues: {{
      update: async (args) => calls.push({{ endpoint: 'issues', args }}),
      addLabels: async (args) => calls.push({{ endpoint: 'labels', args }}),
      createComment: async (args) => calls.push({{ endpoint: 'comments', args }}),
      listComments: async () => ({{ data: existingComments }}),
    }},
  }},
  paginate: async (method, args) => (await method(args)).data,
}};
const context = {{
  payload: JSON.parse(process.env.TEST_EVENT_PAYLOAD),
  repo: {{ owner: 'GetKlai', repo: 'klai' }},
}};
const core = {{
  info: () => {{}},
  setFailed: (message) => failures.push(message),
}};

(async () => {{
  try {{
    await (async () => {{
{textwrap.indent(script, '      ')}
    }})();
  }} catch (error) {{
    failures.push(String(error));
  }} finally {{
    process.stdout.write(JSON.stringify({{ calls, failures }}));
  }}
}})();
"""
    result = subprocess.run(
        ["node", "-e", harness],
        capture_output=True,
        check=False,
        env={
            **os.environ,
            "GITLEAKS_DETECTED": str(gitleaks_detected).lower(),
            "TEST_EVENT_PAYLOAD": json.dumps(payload),
            "TEST_EXISTING_COMMENTS": json.dumps(existing_comments or []),
        },
        text=True,
    )

    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_only_allowlisted_workflow_can_create_public_issues() -> None:
    # public-disclosure-guard.yml may add a review comment. It must never create
    # a new issue or replace an issue's title/body.
    allowed = {"ci-failure-notice.yml", "public-disclosure-guard.yml"}
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


def test_publication_hook_is_registered_and_changes_run_rules_ci() -> None:
    settings = json.loads(CLAUDE_SETTINGS.read_text())
    bash_hooks = next(
        entry["hooks"]
        for entry in settings["hooks"]["PreToolUse"]
        if entry["matcher"] == "Bash"
    )
    commands = {hook["command"] for hook in bash_hooks}

    assert any("public-github-mutation-guard.py" in command for command in commands)
    assert "- '.claude/hooks/**'" in RULES_WORKFLOW.read_text()
    assert "- '.gitleaks.toml'" in RULES_WORKFLOW.read_text()


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


@pytest.mark.parametrize(
    "command",
    [
        "gh pr create --title 'finding' --body 'details'",
        "gh pr edit 42 --body 'new details'",
        "gh pr comment 42 --body 'new details'",
        "gh pr review 42 --comment --body 'review details'",
        "gh api repos/GetKlai/klai/pulls -X POST -f title=finding",
        "gh api -X PATCH repos/GetKlai/klai/pulls/42 -f state=closed",
        "gh api graphql -f query='mutation { mergePullRequest(input: {}) { pullRequest { id } } }'",
        "curl https://api.github.com/repos/GetKlai/klai/pulls/42 -X PATCH -d '{}'",
    ],
)
def test_hook_blocks_unapproved_public_pr_mutations(command: str) -> None:
    result = _run_hook(command)

    assert result.returncode == 2
    assert "autonomous public GitHub PR mutation" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "git push origin main",
        "git push --force origin main",
        "git push --set-upstream origin main",
        "git push --force-with-lease origin HEAD:main",
        "git push origin HEAD:refs/heads/main",
        "git push origin fix/publication-guard:refs/heads/main",
        "git push origin deadbeef:refs/heads/main",
        "git push origin +deadbeef:refs/heads/main",
        "git push origin +main",
        "git push origin --delete main",
        "git push --all",
        "git push --all origin",
        "git push --mirror",
        "git push --mirror origin",
    ],
)
def test_hook_blocks_unapproved_pushes_that_can_mutate_main(command: str) -> None:
    result = _run_hook(command)

    assert result.returncode == 2
    assert "public main branch push" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "gh pr view 42 --json title,body",
        "gh pr list --state open",
        "gh pr checks 42",
        "gh pr diff 42",
        "gh pr checkout 42",
        "git push origin feature/foo",
        "git push --force origin feature/foo",
        "git push -u origin fix/publication-guard",
        "git push --set-upstream origin feature/foo",
        "git push origin HEAD:refs/heads/fix/publication-guard",
        "git push origin deadbeef:refs/heads/fix/publication-guard",
        "git push origin +deadbeef:refs/heads/fix/publication-guard",
        "git push origin +feature/foo",
        "git push origin --delete feature/foo",
    ],
)
def test_hook_allows_read_only_pr_commands_and_named_feature_branch_pushes(
    command: str,
) -> None:
    result = _run_hook(command)

    assert result.returncode == 0


@pytest.mark.parametrize(
    "command",
    [
        "git push",
        "git push --force",
        "git push -f",
        "git push origin",
        "git push origin HEAD",
        "git push --force origin HEAD",
        "git push -f origin HEAD",
        "git push -u origin HEAD",
        "git push --set-upstream origin HEAD",
    ],
)
def test_hook_allows_head_pushes_from_a_feature_branch(
    command: str, tmp_path: Path
) -> None:
    """HEAD-relative pushes depend on the checked-out branch, so pin it.

    These forms were once asserted without controlling the branch. That passed
    on a feature worktree and on a detached PR checkout, and failed on main --
    where the hook is right to block them. The test was reading its environment
    rather than the contract. Its sibling below pins main; this one pins a
    feature branch, so both outcomes are asserted deliberately.
    """
    _init_git_repo(tmp_path, "feature/guard-context")

    result = _run_hook(command, cwd=tmp_path)

    assert result.returncode == 0


@pytest.mark.parametrize(
    "command",
    [
        "git push",
        "git push --force",
        "git push -f",
        "git push origin",
        "git push origin HEAD",
        "git push --force origin HEAD",
        "git push -f origin HEAD",
        "git push -u origin HEAD",
        "git push --set-upstream origin HEAD",
    ],
)
def test_hook_resolves_head_pushes_to_main(
    command: str, tmp_path: Path
) -> None:
    _init_git_repo(tmp_path, "main")

    result = _run_hook(command, cwd=tmp_path)

    assert result.returncode == 2
    assert "public main branch push" in result.stderr


def test_hook_blocks_when_head_branch_cannot_be_resolved(tmp_path: Path) -> None:
    result = _run_hook("git push --force origin HEAD", cwd=tmp_path)

    assert result.returncode == 2
    assert "symbolic-ref" in HOOK.read_text()


def test_hook_resolves_head_in_leading_cd_worktree(tmp_path: Path) -> None:
    session_repo = tmp_path / "session-main"
    feature_repo = tmp_path / "target feature"
    _init_git_repo(session_repo, "main")
    _init_git_repo(feature_repo, "feature/worktree-target")

    command = f"cd {shlex.quote(str(feature_repo))} && git push -u origin HEAD"
    result = _run_hook(command, cwd=session_repo)

    assert result.returncode == 0


def test_hook_blocks_head_in_leading_cd_main_worktree(tmp_path: Path) -> None:
    session_repo = tmp_path / "session-feature"
    main_repo = tmp_path / "target main"
    _init_git_repo(session_repo, "feature/session")
    _init_git_repo(main_repo, "main")

    command = f"cd {shlex.quote(str(main_repo))} && git push origin HEAD"
    result = _run_hook(command, cwd=session_repo)

    assert result.returncode == 2
    assert "public main branch push" in result.stderr


def test_hook_blocks_branch_dependent_push_in_unsupported_shell_context(
    tmp_path: Path,
) -> None:
    session_repo = tmp_path / "session-feature"
    main_repo = tmp_path / "target-main"
    _init_git_repo(session_repo, "feature/session")
    _init_git_repo(main_repo, "main")

    command = f"(cd {shlex.quote(str(main_repo))} && git push origin HEAD)"
    result = _run_hook(command, cwd=session_repo)

    assert result.returncode == 2
    assert "public main branch push" in result.stderr


@pytest.mark.parametrize(
    ("branch", "expected_exit"),
    [("feature/git-c-target", 0), ("main", 2)],
)
def test_hook_resolves_head_in_git_c_worktree(
    branch: str, expected_exit: int, tmp_path: Path
) -> None:
    target_repo = tmp_path / branch.replace("/", "-")
    _init_git_repo(target_repo, branch)

    result = _run_hook(
        f"git -C {shlex.quote(str(target_repo))} push origin HEAD", cwd=tmp_path
    )

    assert result.returncode == expected_exit


def test_hook_prefers_explicit_refspec_destination_over_current_branch(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path, "main")

    result = _run_hook(
        "git push origin HEAD:refs/heads/fix/explicit-target", cwd=tmp_path
    )

    assert result.returncode == 0


@pytest.mark.parametrize(
    "command",
    [
        "git push --unknown-option origin feature/guard",
        "git --no-pager push origin feature/guard",
        "git push origin refs/heads/*:refs/heads/*",
        "git push origin deadbeef:",
    ],
)
def test_hook_blocks_push_shapes_with_ambiguous_destinations(command: str) -> None:
    result = _run_hook(command)

    assert result.returncode == 2
    assert "public main branch push" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "KLAI_ALLOW_PUBLIC_CODE_MUTATION=1 gh pr comment 42 --body approved",
        "KLAI_ALLOW_PUBLIC_CODE_MUTATION=1 git push origin main",
    ],
)
def test_hook_allows_explicitly_authorized_public_code_mutation(command: str) -> None:
    result = _run_hook(command)

    assert result.returncode == 0


def test_server_guard_has_safe_events_and_minimal_write_permissions() -> None:
    workflow = yaml.safe_load(PUBLICATION_WORKFLOW.read_text())
    triggers = workflow.get("on", workflow.get(True))

    assert set(triggers["issues"]["types"]) == {"opened", "edited", "reopened"}
    assert set(triggers["pull_request_target"]["types"]) == {
        "opened",
        "edited",
        "reopened",
    }
    assert workflow["permissions"] == {
        "contents": "read",
        "issues": "write",
        "pull-requests": "write",
    }
    workflow_text = PUBLICATION_WORKFLOW.read_text()
    assert "actions/checkout" not in workflow_text
    assert "ghcr.io/gitleaks/gitleaks:v8.30.1" in workflow_text
    assert "stdin --config=/gitleaks.toml" in workflow_text
    assert ".gitleaks.toml" in workflow_text
    assert "--redact" in workflow_text
    assert "issues.update" not in workflow_text
    assert "pulls.update" not in workflow_text


def test_gitleaks_config_uses_default_rules_and_exact_klai_token_shape() -> None:
    config = GITLEAKS_CONFIG.read_text()

    assert "[extend]" in config
    assert "useDefault = true" in config
    assert 'id = "klai-mcp-token"' in config
    assert "{43}" in config


def test_server_guard_urgently_labels_gitleaks_finding_without_rewriting_text() -> None:
    marker = "credential value omitted from workflow logs"
    result = _run_publication_workflow(
        {"issue": {"number": 42, "title": "Incident", "body": marker}},
        gitleaks_detected=True,
    )

    assert result["failures"] == []
    assert [call["endpoint"] for call in result["calls"]] == ["labels", "comments"]
    label_call, comment_call = result["calls"]
    assert label_call["args"]["labels"] == ["security"]
    assert "high-confidence" in comment_call["args"]["body"]
    assert "immediately" in comment_call["args"]["body"]
    assert marker not in comment_call["args"]["body"]


def test_server_guard_urgently_labels_rfc1918_address_without_rewriting_text() -> None:
    result = _run_publication_workflow(
        {
            "pull_request": {
                "number": 42,
                "title": "Incident",
                "body": "The upstream answered from 172.18.0.49",
            }
        }
    )

    assert result["failures"] == []
    assert [call["endpoint"] for call in result["calls"]] == ["labels", "comments"]
    assert "high-confidence" in result["calls"][1]["args"]["body"]


@pytest.mark.parametrize(
    ("marker", "category"),
    [
        ("A truth table for mcp.getklai.com", "Klai hostname"),
        ("The X-Internal-Secret header is required", "internal header name"),
        ("Guard the job that reaches core-01", "SSH host alias"),
    ],
)
def test_server_guard_labels_contextual_markers_without_rewriting_text(
    marker: str, category: str
) -> None:
    result = _run_publication_workflow(
        {"pull_request": {"number": 42, "title": "Security fix", "body": marker}}
    )

    assert result["failures"] == []
    assert [call["endpoint"] for call in result["calls"]] == ["labels", "comments"]
    label_call, comment_call = result["calls"]
    assert label_call["args"]["labels"] == ["security"]
    assert category in comment_call["args"]["body"]
    assert marker not in comment_call["args"]["body"]


def test_server_guard_does_not_repeat_contextual_review_comment() -> None:
    result = _run_publication_workflow(
        {
            "issue": {
                "number": 42,
                "title": "Header docs",
                "body": "Document X-Internal-Secret",
            }
        },
        existing_comments=[
            {"body": "<!-- public-disclosure-review -->\nAlready requested."}
        ],
    )

    assert result["failures"] == []
    assert [call["endpoint"] for call in result["calls"]] == ["labels"]


def test_server_guard_leaves_public_website_and_local_examples_unchanged() -> None:
    result = _run_publication_workflow(
        {
            "issue": {
                "number": 42,
                "title": "Docs typo",
                "body": "See https://getklai.com/docs and localhost 127.0.0.1",
            }
        }
    )

    assert result == {"calls": [], "failures": []}
