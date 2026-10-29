"""Tests for scripts/renovate-revert-after-rollback.py (SPEC-INFRA-AUTO-ROLLBACK-001 S5, AT-b2).

Each case builds a throwaway repository with a bare `origin`, a synthetic compose
file and a copy of the real renovate.json5, commits the last green sync and the
bumps after it, and runs the script against a fixture report. `gh` is a stub on
PATH that records its arguments, so nothing touches the network.

Run: python3 -m unittest scripts/tests/test_renovate_revert_after_rollback.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "renovate-revert-after-rollback.py"
spec = importlib.util.spec_from_file_location("revert", SCRIPT)
assert spec and spec.loader
revert = importlib.util.module_from_spec(spec)
spec.loader.exec_module(revert)

COMPOSE = """\
services:
  alpha:
    image: acme/alpha:v1.4.0
  beta:
    image: ghcr.io/acme/beta:2.0.0-alpine
  logs:
    image: victoriametrics/victoria-logs:v1.51.1
  app:
    image: acme/app:latest
  pinned:
    image: ghcr.io/acme/pinned@sha256:aaaa
"""

GH_STUB = """\
#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_GH_LOG"
case "$1 $2" in
  "pr list") printf '%s' "${FAKE_GH_PRS:-[]}" ;;
  "issue list") printf '[{"number": 42, "title": "CI failure: Sync compose"}]' ;;
esac
"""

RENOVATE = ("klai-renovate[bot]", "316920285+klai-renovate[bot]@users.noreply.github.com")
HUMAN = ("Example Dev", "dev@example.com")


def excluded(config: str, dep: str) -> re.Pattern[str]:
    """The negated allowedVersions regex of the rolled-back entry for dep, as Renovate reads it."""
    block = config[config.index(f"matchDepNames: ['{dep}']") :]
    raw = re.search(r"allowedVersions: '!/(.*)/',", block).group(1)  # type: ignore[union-attr]
    return re.compile(raw.replace("\\\\", "\\"))


class RevertAfterRollback(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.work = base / "work"
        self.origin = base / "origin.git"
        bin_dir = base / "bin"
        bin_dir.mkdir()
        (bin_dir / "gh").write_text(GH_STUB)
        (bin_dir / "gh").chmod(0o755)
        self.gh_log = base / "gh.log"
        self.env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_GH_LOG": str(self.gh_log)}
        self.env.pop("FAKE_GH_PRS", None)
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.work)], check=True)
        self.git("remote", "add", "origin", str(self.origin))
        (self.work / "deploy").mkdir()
        (self.work / "deploy/docker-compose.yml").write_text(COMPOSE)
        (self.work / "renovate.json5").write_text((ROOT / "renovate.json5").read_text())
        self.green = self.commit(HUMAN, "deploy: last green sync")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.work, capture_output=True, text=True, check=True).stdout

    def commit(self, who: tuple[str, str], subject: str, **replace: str) -> str:
        compose = self.work / "deploy/docker-compose.yml"
        text = compose.read_text()
        for old, new in replace.items():
            text = text.replace(old.replace("__", ":"), new)
        compose.write_text(text)
        self.git("add", "-A")
        self.git("-c", f"user.name={who[0]}", "-c", f"user.email={who[1]}", "commit", "-q", "-m", subject)
        return self.git("rev-parse", "HEAD").strip()

    def bump(self, image: str, old: str, new: str) -> str:
        return self.commit(RENOVATE, f"chore(deps): update {image} docker tag to {new}", **{f"{image}__{old}": f"{image}:{new}"})

    def run_script(self, outcome: str = "rolled_back", gate: str = "compose-up-verification") -> subprocess.CompletedProcess[str]:
        to = self.git("rev-parse", "HEAD").strip()
        self.git("push", "-q", "origin", "main")
        report = {"service": "compose", "kind": "compose", "from": self.green, "to": to, "commit": to,
                  "gate": gate, "outcome": outcome, "ts": "2026-10-28T12:00:00Z",
                  "log": "/opt/klai/deploy-reports/compose.log"}
        path = self.work.parent / "report.json"
        path.write_text(json.dumps(report))
        return subprocess.run(
            ["python3", str(SCRIPT), "--report", str(path), "--run-id", "777",
             "--run-url", "https://github.com/GetKlai/klai/actions/runs/777", "--workflow", "Sync compose"],
            cwd=self.work, env=self.env, capture_output=True, text=True, check=False,
        )

    def remote_file(self, branch: str, path: str) -> str:
        return subprocess.run(["git", "--git-dir", str(self.origin), "show", f"{branch}:{path}"],
                              capture_output=True, text=True, check=True).stdout

    def remote_branches(self) -> list[str]:
        out = subprocess.run(["git", "--git-dir", str(self.origin), "branch", "--format=%(refname:short)"],
                             capture_output=True, text=True, check=True).stdout
        return out.split()

    def gh_calls(self) -> str:
        return self.gh_log.read_text() if self.gh_log.exists() else ""

    def test_rolled_back_bump_gets_a_revert_pr_that_excludes_only_that_version(self) -> None:
        self.bump("acme/alpha", "v1.4.0", "v1.5.0")
        self.commit(HUMAN, "deploy: a change of our own", **{"acme/app__latest": "acme/app:stable"})
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)

        branch = "revert/renovate-acme-alpha-v1.5.0-777"
        compose = self.remote_file(branch, "deploy/docker-compose.yml")
        self.assertIn("image: acme/alpha:v1.4.0", compose)
        self.assertIn("image: acme/app:stable", compose, "our own change must stay (D3)")
        config = self.remote_file(branch, "renovate.json5")
        self.assertLess(config.index("matchDepNames: ['acme/alpha']"), config.index(revert.ANCHOR))
        self.assertIn("#42", config)
        pattern = excluded(config, "acme/alpha")
        for blocked in ("v1.5.0", "1.5.0"):
            self.assertTrue(pattern.match(blocked), blocked)
        for flowing in ("v1.5.1", "1.5.01", "v1.6.0", "1.5.0.1", "v1.5.0-rc.1"):
            self.assertFalse(pattern.match(flowing), flowing)

        calls = self.gh_calls()
        self.assertIn("pr create --base main --head " + branch +
                      " --title revert(renovate): acme/alpha v1.5.0 -> v1.4.0, rolled back by run 777", calls)
        self.assertIn("outcome `rolled_back`", calls)
        self.assertIn(f"pr merge {branch} --auto --squash", calls)

    def test_a_failed_shadow_reverts_the_version_like_a_rollback(self) -> None:
        # The new image failed on a copy of the store's data, before anything was
        # switched: the version is bad for that data, so it is reverted and excluded.
        self.bump("acme/alpha", "v1.4.0", "v1.5.0")
        result = self.run_script(outcome="shadow_failed", gate="datastore-shadow")
        self.assertEqual(result.returncode, 0, result.stderr)

        branch = "revert/renovate-acme-alpha-v1.5.0-777"
        self.assertIn("image: acme/alpha:v1.4.0", self.remote_file(branch, "deploy/docker-compose.yml"))
        pattern = excluded(self.remote_file(branch, "renovate.json5"), "acme/alpha")
        self.assertTrue(pattern.match("v1.5.0"))
        self.assertFalse(pattern.match("v1.5.1"))
        calls = self.gh_calls()
        self.assertIn(" --title revert(renovate): acme/alpha v1.5.0 -> v1.4.0, failed its shadow in run 777", calls)
        self.assertIn("outcome `shadow_failed`", calls)
        self.assertIn(f"pr merge {branch} --auto --squash", calls)

    def test_two_bumps_in_the_range_land_in_one_pr(self) -> None:
        self.bump("acme/alpha", "v1.4.0", "v1.5.0")
        self.bump("ghcr.io/acme/beta", "2.0.0-alpine", "2.1.0-alpine")
        self.bump("acme/alpha", "v1.5.0", "v1.5.1")
        self.assertEqual(self.run_script().returncode, 0)

        branch = "revert/renovate-2-images-777"
        compose = self.remote_file(branch, "deploy/docker-compose.yml")
        self.assertIn("image: acme/alpha:v1.4.0", compose)
        self.assertIn("image: ghcr.io/acme/beta:2.0.0-alpine", compose)
        config = self.remote_file(branch, "renovate.json5")
        self.assertTrue(excluded(config, "acme/alpha").match("v1.5.1"))
        self.assertFalse(excluded(config, "acme/alpha").match("v1.5.0"), "only the version that ran is excluded")
        self.assertTrue(excluded(config, "ghcr.io/acme/beta").match("2.1.0-alpine"))
        self.assertFalse(excluded(config, "ghcr.io/acme/beta").match("2.1.0"), "the docker tag is the version")
        self.assertIn("acme/alpha v1.5.1 -> v1.4.0, ghcr.io/acme/beta 2.1.0-alpine -> 2.0.0-alpine,", self.gh_calls())

    def test_a_bump_an_open_pr_already_reverts_is_left_alone(self) -> None:
        self.bump("acme/alpha", "v1.4.0", "v1.5.0")
        self.env["FAKE_GH_PRS"] = json.dumps([{
            "headRefName": "revert/renovate-acme-alpha-v1.5.0-700",
            "title": "revert(renovate): acme/alpha v1.5.0 -> v1.4.0, rolled back by run 700",
        }])
        self.assertEqual(self.run_script().returncode, 0)
        self.assertEqual(self.remote_branches(), ["main"])
        self.assertNotIn("pr create", self.gh_calls())

    def test_a_rerun_after_the_pr_was_opened_only_arms_auto_merge(self) -> None:
        self.bump("acme/alpha", "v1.4.0", "v1.5.0")
        branch = "revert/renovate-acme-alpha-v1.5.0-777"
        self.env["FAKE_GH_PRS"] = json.dumps([{
            "headRefName": branch, "title": "revert(renovate): acme/alpha v1.5.0 -> v1.4.0, rolled back by run 777",
        }])
        self.assertEqual(self.run_script().returncode, 0)
        self.assertEqual(self.remote_branches(), ["main"], "the branch is not pushed again")
        self.assertIn(f"pr merge {branch} --auto --squash", self.gh_calls())
        self.assertNotIn("pr create", self.gh_calls())

    def test_a_digest_only_bump_is_skipped(self) -> None:
        self.commit(RENOVATE, "chore(deps): update ghcr.io/acme/pinned digest",
                    **{"ghcr.io/acme/pinned@sha256__aaaa": "ghcr.io/acme/pinned@sha256:bbbb"})
        result = self.run_script()
        self.assertEqual(result.returncode, 0)
        self.assertIn("digest-only change", result.stdout)
        self.assertEqual(self.remote_branches(), ["main"])

    def test_a_rollback_without_a_renovate_bump_does_nothing(self) -> None:
        self.commit(HUMAN, "chore(deps): update acme/alpha by hand", **{"acme/alpha__v1.4.0": "acme/alpha:v1.5.0"})
        result = self.run_script()
        self.assertEqual(result.returncode, 0)
        self.assertIn("no Renovate image bump", result.stdout)
        self.assertEqual(self.remote_branches(), ["main"])
        self.assertEqual(self.gh_calls(), "")

    def test_a_failed_rollback_is_left_to_a_person(self) -> None:
        self.bump("acme/alpha", "v1.4.0", "v1.5.0")
        # Restored, then red again: the host state is unverified, so no auto-merged revert.
        result = self.run_script(outcome="rollback_failed", gate="compose-up-verification")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.remote_branches(), ["main"])
        self.assertEqual(self.gh_calls(), "")

    def test_a_package_with_a_ceiling_is_refused_not_overridden(self) -> None:
        self.bump("victoriametrics/victoria-logs", "v1.51.1", "v1.51.2")
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertIn("already has an allowedVersions rule", result.stderr)
        self.assertEqual(self.remote_branches(), ["main"])
        self.assertNotIn("pr create", self.gh_calls())

    def test_a_prerelease_tag_excludes_only_itself(self) -> None:
        config = revert.add_exclusion((ROOT / "renovate.json5").read_text(), "acme/alpha", "v1.5.0-rc.1", "run 1")
        pattern = excluded(config, "acme/alpha")
        self.assertTrue(pattern.match("v1.5.0-rc.1"))
        self.assertFalse(pattern.match("v1.5.0"))

    def test_a_github_sourced_image_excludes_the_version_its_manager_reads(self) -> None:
        # redis is read as 8.10.2 from `image: redis:8.10.2-alpine` by its custom manager.
        config = revert.add_exclusion((ROOT / "renovate.json5").read_text(), "redis", "8.10.2-alpine", "run 1")
        pattern = excluded(config, "redis")
        self.assertTrue(pattern.match("8.10.2"))
        self.assertFalse(pattern.match("8.10.20"))

    def test_a_second_rolled_back_version_joins_the_existing_entry(self) -> None:
        config = revert.add_exclusion((ROOT / "renovate.json5").read_text(), "acme/alpha", "v1.5.0", "run 1")
        config = revert.add_exclusion(config, "acme/alpha", "v1.7.0", "run 2")
        self.assertEqual(config.count("matchDepNames: ['acme/alpha']"), 1, "a second rule would replace the first")
        pattern = excluded(config, "acme/alpha")
        self.assertTrue(pattern.match("v1.5.0") and pattern.match("v1.7.0"))
        self.assertFalse(pattern.match("v1.6.0"))
        self.assertEqual(revert.add_exclusion(config, "acme/alpha", "v1.7.0", "run 3"), config)


if __name__ == "__main__":
    unittest.main()
