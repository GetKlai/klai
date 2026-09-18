#!/usr/bin/env python3
"""Parse the shell in every workflow step and fail on a syntax error.

A `run:` block and an `appleboy/ssh-action` `script:` are shell programs that no
tool in this repo ever parsed. On 2026-09-18 an edit to deploy-compose.yml
dropped the `esac` from a `case` inside a `for` loop. YAML still parsed, every
existing check passed, and the error would only have surfaced on core-01 at
deploy time — after the step had already copied docker-compose.yml into place
and before it recreated anything, which is the half-landed state that deploy is
supposed to avoid.

Bash parses a compound command as a unit, so a missing terminator kills the
whole loop rather than one branch. `bash -n` catches exactly this class without
executing anything.

Steps whose body contains a GitHub expression are skipped: `${{ ... }}` is
substituted before the shell ever sees it, so the pre-substitution text is not
valid shell and a parse failure there would be noise, not signal.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile

import yaml

WORKFLOWS = pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows"


def shell_bodies(workflow: dict):
    """Yield (step name, shell source) for every step that runs shell."""
    for job in (workflow.get("jobs") or {}).values():
        if not isinstance(job, dict):
            continue
        for step in job.get("steps") or []:
            if not isinstance(step, dict):
                continue
            name = step.get("name", "(unnamed step)")
            if isinstance(step.get("run"), str):
                yield name, step["run"]
            elif str(step.get("uses", "")).startswith("appleboy/ssh-action"):
                script = (step.get("with") or {}).get("script")
                if isinstance(script, str):
                    yield name, script


def main() -> int:
    failures = 0
    checked = 0
    for path in sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml")):
        try:
            workflow = yaml.safe_load(path.read_text())
        except yaml.YAMLError as exc:
            print(f"FAIL {path.name}: not valid YAML: {exc}")
            failures += 1
            continue
        if not isinstance(workflow, dict):
            continue
        for name, body in shell_bodies(workflow):
            if "${{" in body:
                continue
            checked += 1
            with tempfile.NamedTemporaryFile("w", suffix=".sh") as handle:
                handle.write(body)
                handle.flush()
                result = subprocess.run(
                    ["bash", "-n", handle.name], capture_output=True, text=True
                )
            if result.returncode != 0:
                detail = result.stderr.strip().replace(handle.name, path.name)
                print(f"FAIL {path.name} :: {name}\n{detail}")
                failures += 1

    if failures:
        print(f"\n{failures} workflow step(s) contain invalid shell.")
        return 1
    print(f"OK: {checked} workflow shell step(s) parse.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
