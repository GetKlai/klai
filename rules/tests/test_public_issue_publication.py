"""Regression guards for public GitHub publication boundaries."""

from __future__ import annotations

import json
import os
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


ELSEWHERE = "outside GetKlai"


def _repo_with_origin(tmp_path: Path, owner: str) -> Path:
    """A checkout whose `origin` belongs to ``owner``.

    The guard reads that remote to work out where a command with no explicit
    repository is aimed, so this is the only setup these tests need.
    """
    repo = tmp_path / owner
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin",
         f"https://github.com/{owner}/thing.git"],
        check=True, capture_output=True,
    )
    return repo


@pytest.mark.parametrize(
    "command",
    [
        "gh pr merge 42 --squash",
        "gh pr merge 42 --merge --admin",
        "gh pr create --title 'fix' --body 'details'",
        "gh pr ready 42",
        "gh pr comment 42 --body 'details'",
        "gh pr review 42 --approve",
        "gh pr edit 42 --body 'new details'",
        "git push origin main",
        "git push --force-with-lease origin HEAD:main",
        "gh api repos/GetKlai/klai/pulls -X POST -f title=x",
    ],
)
def test_hook_stays_out_of_the_way_inside_getklai(command: str, tmp_path: Path) -> None:
    """Inside our own org the hook blocks nothing, on purpose.

    `main` is gated by branch protection: a pull request, a green `quality`
    check that itself waits on every affected service job, no force-push, no
    deletion. A marker the one developer types on every merge adds no second
    opinion -- and it self-authorised once, when a PR body that merely named
    the marker satisfied the match (#1400).
    """
    result = _run_hook(command, cwd=_repo_with_origin(tmp_path, "GetKlai"))

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "gh pr create --repo unclecode/crawl4ai --title x --body y",
        "gh pr ready 2249 --repo unclecode/crawl4ai",
        "gh pr merge 2249 --repo unclecode/crawl4ai",
        "gh pr comment 2249 --repo unclecode/crawl4ai --body 'ping'",
        "gh api --method POST repos/unclecode/crawl4ai/pulls -f title=x",
        "curl -X POST https://api.github.com/repos/unclecode/crawl4ai/pulls -d '{}'",
    ],
)
def test_hook_blocks_publication_aimed_at_another_organisation(
    command: str, tmp_path: Path
) -> None:
    """Pushing a branch to your own fork notifies nobody; these do.

    Opening a pull request puts it in front of someone else's maintainers, and
    undrafting is the moment it asks to be reviewed. On 2026-09-11 a PR was
    opened at unclecode/crawl4ai from a sentence read as permission, and
    nothing here stopped it -- `gh pr create` was on the read-only list.
    """
    result = _run_hook(command, cwd=_repo_with_origin(tmp_path, "GetKlai"))

    assert result.returncode == 2, result.stderr
    assert ELSEWHERE in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "gh pr ready 2249 --repo unclecode/crawl4ai --undo",
        "gh pr view 2249 --repo unclecode/crawl4ai",
        "gh pr checks 2249 --repo unclecode/crawl4ai",
        "gh pr diff 2249 --repo unclecode/crawl4ai",
    ],
)
def test_hook_allows_retreating_and_reading_elsewhere(
    command: str, tmp_path: Path
) -> None:
    """Putting a PR back to draft is the retreat; reading is not publication.

    Opening one is NOT here, draft or otherwise: a draft is still a pull
    request nobody asked for, and the --draft exemption was where the holes
    were -- a body reading "use --draft next time" satisfied it.
    """
    result = _run_hook(command, cwd=_repo_with_origin(tmp_path, "GetKlai"))

    assert result.returncode == 0, result.stderr


def test_hook_fails_closed_when_the_destination_is_unknown(tmp_path: Path) -> None:
    """No `--repo`, no usable `origin`: treat it as elsewhere.

    Being wrong that way costs one sentence asking the user. Being wrong the
    other way costs a stranger their inbox.
    """
    nowhere = tmp_path / "nowhere"
    nowhere.mkdir()
    result = _run_hook("gh pr merge 1", cwd=nowhere)

    assert result.returncode == 2, result.stderr
    assert ELSEWHERE in result.stderr


def test_hook_allows_explicitly_authorized_publication_elsewhere(
    tmp_path: Path,
) -> None:
    result = _run_hook(
        "KLAI_ALLOW_PUBLIC_CODE_MUTATION=1 gh pr ready 2249 --repo unclecode/crawl4ai",
        cwd=_repo_with_origin(tmp_path, "GetKlai"),
    )

    assert result.returncode == 0, result.stderr


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
