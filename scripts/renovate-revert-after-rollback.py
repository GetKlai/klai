#!/usr/bin/env python3
"""Revert PR after a compose rollback of a Renovate image bump (SPEC-INFRA-AUTO-ROLLBACK-001 S5).

When the core-01 compose sync rolls back, the host runs the previous compose file
again but main still names the bad version, so the next compose change would
deploy it once more. This script reads the rollback report, finds the Renovate
image bumps of deploy/docker-compose.yml between the last green sync ("from")
and the rolled-back commit, and opens one PR with the Klai Renovate app token
that puts those image lines back and excludes each exact new version in the
rolled-back block of renovate.json5. GitHub auto-merge is armed on it, so it
lands once `quality` is green.

Our own code is never reverted (decision D3): only commits authored by
klai-renovate[bot] with a `chore(deps): update` subject count. A rollback with
no such commit in its range does nothing and exits 0. Only `rolled_back` acts:
after `rollback_failed` the host is in a state nobody verified, and that is for
a person (the ci-failure issue carries the `rollback-failed` label).

Usage, from the root of a full-history checkout of main, with GH_TOKEN set:

    renovate-revert-after-rollback.py --report <compose.json> --run-id <id> \
        --run-url <url> --workflow <workflow name>

Exit 0: PR opened, or nothing to do. Exit 1: the revert cannot be made safely
(unknown range, a package that already has an allowedVersions ceiling, a git or
gh failure); the message says why and nothing was pushed unless it says so.

One PR per rollback, not one per package: every exclusion is inserted at the
same anchor in renovate.json5, so separate PRs would conflict with each other
after the first merge, and the host rolled the whole compose file back at once.

Known ceilings
  - An image line is put back wherever its text matches. If one of our own
    commits added the same line for another service between the bump and the
    rollback, that line goes back too. Accepted: the window is the minutes of
    one sync, and the result is the image the host runs after the rollback.
  - A package that already has an allowedVersions ceiling is refused (exit 1,
    the job goes red): Renovate cannot combine a range and an exclusion in one
    option, and a second rule replaces the first, so a person combines them.
    Today that is seven rules, two of them compose images (victoria-logs, mongo).
  - LiteLLM never reaches this path: it migrates its own database, so a red gate
    after a LiteLLM change restores nothing and ends rollback_failed. That is
    why its PyPI pin in litellm-tests.yml and its Renovate depName `litellm`
    (not the image name) are not handled here.
  - A digest-only change (no tag) is skipped with a message: there is no
    version to exclude.
"""

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

COMPOSE = "deploy/docker-compose.yml"
CONFIG = "renovate.json5"
ANCHOR = "    // End of the rolled-back versions: scripts/renovate-revert-after-rollback.py inserts above this line."
RENOVATE_AUTHOR = "klai-renovate[bot]"
RENOVATE_EMAIL = "316920285+klai-renovate[bot]@users.noreply.github.com"
AUTO_DESCRIPTION = "Rolled back on core-01"
# An `image:` key or an env value such as BROWSER_IMAGE (vexa-bot), the two
# forms Renovate bumps in the compose file.
REF_LINE = re.compile(r"""^\s*(?:image|[A-Z_]*IMAGE):\s*["']?([^\s"'#]+)""")


class Refused(Exception):
    pass


@dataclass
class Bump:
    repo: str
    old: str
    new: str
    lines: list[tuple[str, str]] = field(default_factory=list)  # (old line, new line), newest commit first

    @property
    def label(self) -> str:
        return f"{self.repo} {self.new} -> {self.old}"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout


def split_ref(ref: str) -> tuple[str, str]:
    ref = ref.split("@", 1)[0]
    repo, sep, tag = ref.rpartition(":")
    if not sep or "/" in tag:
        return ref, ""
    return repo, tag


def acts_on(report: dict) -> bool:
    return report.get("kind") == "compose" and report.get("outcome") == "rolled_back"


def renovate_bumps(frm: str, to: str) -> list[Bump]:
    """Image bumps by Renovate commits in frm..to, one per repository (oldest old, newest new)."""
    log = git("log", "--reverse", "--format=%H%x1f%an%x1f%s", f"{frm}..{to}", "--", COMPOSE)
    bumps: dict[str, Bump] = {}
    for entry in log.splitlines():
        sha, author, subject = entry.split("\x1f")
        if author != RENOVATE_AUTHOR or not subject.startswith("chore(deps): update "):
            continue
        for repo, (old, old_line), (new, new_line) in changed_refs(git("show", "-U0", "--format=", sha, "--", COMPOSE)):
            bump = bumps.setdefault(repo, Bump(repo, old, new))
            bump.new = new
            if (old_line, new_line) not in bump.lines:
                bump.lines.insert(0, (old_line, new_line))
    return list(bumps.values())


def changed_refs(diff: str) -> list[tuple[str, tuple[str, str], tuple[str, str]]]:
    """(repo, (old tag, old line), (new tag, new line)) for every image reference a diff hunk replaced."""
    out = []
    for hunk in re.split(r"^@@.*$", diff, flags=re.MULTILINE)[1:]:
        removed: dict[str, tuple[str, str]] = {}
        added: dict[str, tuple[str, str]] = {}
        for line in hunk.splitlines():
            m = REF_LINE.match(line[1:]) if line[:1] in "-+" else None
            if m:
                repo, tag = split_ref(m.group(1))
                (removed if line[0] == "-" else added)[repo] = (tag, line[1:])
        out += [(repo, removed[repo], added[repo]) for repo in removed if repo in added and removed[repo] != added[repo]]
    return out


def version_pattern(config: str, repo: str, tag: str) -> str:
    """The regex body that matches exactly this tag, as the version Renovate compares.

    The docker datasource compares the whole tag, so v1.5.0-rc.1 excludes only
    itself and never v1.5.0. The GitHub-sourced custom managers above read the
    numeric part of the tag (`redis:(?<currentValue>[0-9][0-9.]*)-alpine` reads
    8.10.2 from 8.10.2-alpine), so for those the numeric part is the version.
    """
    tag = tag.removeprefix("v")
    if f"{repo}:(?<currentValue>" in config or f"{repo}:v(?<currentValue>" in config:
        tag = re.match(r"[0-9][0-9.]*", tag).group(0).rstrip(".")  # type: ignore[union-attr]
    return re.escape(tag)


def allowed_versions(patterns: list[str]) -> str:
    body = "|".join(patterns)
    # JSON5 single-quoted string: every regex backslash is written twice.
    return f"'!/^v?({body})$/'".replace("\\", "\\\\")


def rule_blocks(config: str) -> list[tuple[int, int]]:
    """Line ranges [start, end] of the packageRules entries (four-space `{` ... `},`)."""
    lines = config.splitlines()
    blocks, start = [], None
    for i, line in enumerate(lines):
        if line == "    {":
            start = i
        elif line == "    }," and start is not None:
            blocks.append((start, i))
            start = None
    return blocks


def add_exclusion(config: str, dep: str, tag: str, note: str) -> str:
    """Return config with tag excluded for dep; unchanged if it already is."""
    lines = config.splitlines()
    quoted = f"'{dep}'"
    pattern = version_pattern(config, dep, tag)
    for start, end in rule_blocks(config):
        block = lines[start : end + 1]
        if not any(quoted in line for line in block) or not any("allowedVersions" in line for line in block):
            continue
        if not any(AUTO_DESCRIPTION in line for line in block):
            raise Refused(
                f"{dep} already has an allowedVersions rule in {CONFIG} (line {start + 1}); a second rule "
                "would replace that ceiling instead of adding to it, so the exclusion has to be combined by hand"
            )
        idx = next(i for i in range(start, end + 1) if lines[i].lstrip().startswith("allowedVersions:"))
        current = re.search(r"\?\((.*?)\)\$", lines[idx].replace("\\\\", "\\"))
        existing = current.group(1).split("|") if current else []
        if pattern in existing:
            return config
        lines[idx] = f"      allowedVersions: {allowed_versions(existing + [pattern])},"
        lines.insert(idx, f"      // {tag}: {note}")
        return "\n".join(lines) + "\n"
    if ANCHOR not in lines:
        raise Refused(f"{CONFIG} has no rolled-back anchor line; cannot place the exclusion")
    at = lines.index(ANCHOR)
    entry = [
        "    {",
        "      description:",
        f"        '{AUTO_DESCRIPTION}: Renovate must not propose these exact versions of {dep} \\",
        "        again; later releases still flow. Renovate never proposes a downgrade, so \\",
        "        this entry can go once deploy/docker-compose.yml runs a newer version.',",
        f"      matchDepNames: [{quoted}],",
        f"      // {tag}: {note}",
        f"      allowedVersions: {allowed_versions([pattern])},",
        "    },",
    ]
    lines[at:at] = entry
    return "\n".join(lines) + "\n"


def ci_failure_issue(workflow: str) -> str:
    title = f"CI failure: {workflow}"
    issues = json.loads(gh("issue", "list", "--label", "ci-failure", "--state", "open", "--json", "number,title"))
    number = next((i["number"] for i in issues if i["title"] == title), None)
    return f"#{number}" if number else f'"{title}" (opened when this run ends)'


def open_revert_prs() -> list[dict]:
    prs = json.loads(gh("pr", "list", "--state", "open", "--json", "headRefName,title", "--limit", "200"))
    return [p for p in prs if p["headRefName"].startswith("revert/renovate-")]


def branch_name(todo: list[Bump], run_id: str) -> str:
    if len(todo) == 1:
        return f"revert/renovate-{slug(todo[0].repo)}-{slug(todo[0].new)}-{run_id}"
    return f"revert/renovate-{len(todo)}-images-{run_id}"


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9.]+", "-", text).strip("-")


def run(args: argparse.Namespace) -> int:
    report = json.loads(Path(args.report).read_text())
    if not acts_on(report):
        print(f"outcome {report.get('outcome')} (gate {report.get('gate')}): nothing was restored on the host, no revert")
        return 0
    frm, to = report.get("from", ""), report.get("commit") or report.get("to", "")
    if frm in ("", "none") or not to:
        raise Refused(f"the report has no commit range (from={frm!r}, to={to!r}); which bumps were rolled back is unknown")

    bumps = renovate_bumps(frm, to)
    if not bumps:
        print(f"no Renovate image bump in {frm[:12]}..{to[:12]}: the rollback is for a person, no revert")
        return 0

    git("fetch", "--quiet", "origin", "main")
    compose = git("show", f"origin/main:{COMPOSE}")
    config = git("show", f"origin/main:{CONFIG}")
    todo = []
    for b in bumps:
        if not b.new:
            print(f"{b.repo}: a digest-only change has no version to exclude; left for a person")
        elif not any(new in compose.splitlines() for _, new in b.lines):
            print(f"{b.repo}: main no longer runs {b.new}; nothing to revert")
        else:
            todo.append(b)
    if not todo:
        return 0
    # A rerun of this run after the push succeeded and `gh pr create` did not, or
    # after the PR was opened: finish that PR instead of pushing the branch again.
    prs = open_revert_prs()
    branch = branch_name(todo, args.run_id)
    if any(p["headRefName"] == branch for p in prs):
        gh("pr", "merge", branch, "--auto", "--squash")
        print(f"{branch} already has an open PR; auto-merge armed")
        return 0
    covered = [b for b in todo if any(f"{b.label}," in p["title"] for p in prs)]
    for b in covered:
        print(f"{b.repo}: an open PR already reverts {b.new}")
    todo = [b for b in todo if b not in covered]
    if not todo:
        return 0
    branch = branch_name(todo, args.run_id)

    issue = ci_failure_issue(args.workflow)
    note = f"failed gate {report.get('gate')} on {report.get('service')}, run {args.run_url}, issue {issue}"
    lines = compose.splitlines()
    for b in todo:
        for old, new in b.lines:
            lines = [old if line == new else line for line in lines]
        config = add_exclusion(config, b.repo, b.new, note)
    title = f"revert(renovate): {', '.join(b.label for b in todo)}, rolled back by run {args.run_id}"

    git("checkout", "--quiet", "-B", branch, "origin/main")
    Path(COMPOSE).write_text("\n".join(lines) + "\n")
    Path(CONFIG).write_text(config)
    git("add", COMPOSE, CONFIG)
    git(
        "-c", f"user.name={RENOVATE_AUTHOR}", "-c", f"user.email={RENOVATE_EMAIL}",
        "commit", "--quiet", "-m", title, "-m",
        f"The core-01 compose sync rolled back {to[:12]} ({report.get('outcome')}, gate "
        f"{report.get('gate')}). Run: {args.run_url}",
    )
    git("push", "--quiet", "--force", "origin", f"HEAD:refs/heads/{branch}")

    rows = "\n".join(f"| `{b.repo}` | `{b.new}` | `{b.old}` |" for b in todo)
    body = (
        f"The core-01 compose sync of `{to[:12]}` was rolled back by run {args.run_url} "
        f"(ci-failure issue {issue}). This puts the image lines back and excludes each exact "
        f"version in `{CONFIG}`, so the next compose change does not deploy it again and "
        "Renovate does not propose it again; later versions keep flowing.\n\n"
        f"| Package | Rolled back | Restored |\n|---|---|---|\n{rows}\n\n"
        f"Rollback: service `{report.get('service')}`, gate `{report.get('gate')}`, outcome "
        f"`{report.get('outcome')}`, commit `{to}`, run {args.run_url}\n\n"
        "Opened by `scripts/renovate-revert-after-rollback.py` (SPEC-INFRA-AUTO-ROLLBACK-001 S5); "
        "auto-merge is armed and waits for `quality`."
    )
    gh("pr", "create", "--base", "main", "--head", branch, "--title", title, "--body", body)
    gh("pr", "merge", branch, "--auto", "--squash")
    print(f"opened {branch}: {title}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--workflow", required=True)
    try:
        return run(parser.parse_args())
    except Refused as err:
        print(f"::error::{err}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as err:
        print(f"::error::{' '.join(err.cmd[:3])} failed ({err.returncode}): {err.stderr.strip()}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
