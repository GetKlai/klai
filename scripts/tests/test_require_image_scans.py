"""Tests for scripts/require-image-scans.py.

The script decides whether the required `quality` check may pass when a pull
request changes a Dockerfile. Each case feeds it the changed Dockerfiles and the
`needs` result of the quality job, exactly as the workflow does.

Run: python3 -m unittest scripts/tests/test_require_image_scans.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "require-image-scans.py"
RETRIEVAL = "klai-retrieval-api/Dockerfile"


def run(changed, needs):
    env = {
        **os.environ,
        "CHANGED_DOCKERFILES": json.dumps(changed) if changed is not None else "",
        "NEEDS_JSON": json.dumps(needs),
    }
    done = subprocess.run([sys.executable, "-I", str(SCRIPT)], env=env, capture_output=True, text=True, check=False)
    return done.returncode, done.stdout


def job(result, scanned=None):
    outputs = {} if scanned is None else {"scanned": scanned}
    return {"result": result, "outputs": outputs}


class RequireImageScans(unittest.TestCase):
    def test_changed_dockerfile_with_a_scan_that_ran_passes(self):
        code, _ = run([RETRIEVAL], {"changes": job("success"), "retrieval-api": job("success", "true")})
        self.assertEqual(code, 0)

    def test_changed_dockerfile_with_a_skipped_job_fails(self):
        code, out = run([RETRIEVAL], {"changes": job("success"), "retrieval-api": job("skipped")})
        self.assertEqual(code, 1)
        self.assertIn(RETRIEVAL, out)

    def test_changed_dockerfile_whose_job_passed_without_scanning_fails(self):
        # The 2026-11-23 case: the workflow succeeded, its scan job was skipped inside it.
        code, out = run([RETRIEVAL], {"changes": job("success"), "retrieval-api": job("success", "")})
        self.assertEqual(code, 1)
        self.assertIn("without running the Trivy scan", out)

    def test_changed_dockerfile_with_a_cancelled_job_fails(self):
        code, _ = run([RETRIEVAL], {"changes": job("success"), "retrieval-api": job("cancelled")})
        self.assertEqual(code, 1)

    def test_changed_dockerfile_with_no_mapped_scan_fails_and_names_the_file(self):
        code, out = run(["services/new-thing/Dockerfile"], {"changes": job("success")})
        self.assertEqual(code, 1)
        self.assertIn("services/new-thing/Dockerfile", out)

    def test_no_changed_dockerfile_passes_when_every_scan_was_skipped(self):
        needs = {"changes": job("success"), "retrieval-api": job("skipped"), "caddy": job("skipped")}
        self.assertEqual(run([], needs)[0], 0)
        self.assertEqual(run(None, needs)[0], 0)

    def test_one_unscanned_dockerfile_among_scanned_ones_fails(self):
        needs = {"changes": job("success"), "retrieval-api": job("success", "true"), "slack": job("skipped")}
        code, out = run([RETRIEVAL, "klai-slack/Dockerfile"], needs)
        self.assertEqual(code, 1)
        self.assertIn("klai-slack/Dockerfile", out)
        self.assertNotIn(f"::error::{RETRIEVAL}", out)

    def test_policy_exempt_dockerfile_passes_with_a_notice(self):
        code, out = run(["deploy/crawl4ai/Dockerfile"], {"changes": job("success")})
        self.assertEqual(code, 0)
        self.assertIn("deploy/crawl4ai/Dockerfile", out)


class MappingCoversTheRepository(unittest.TestCase):
    """A new Dockerfile must be mapped or exempted before it can merge."""

    @classmethod
    def setUpClass(cls):
        spec = {}
        exec(compile(SCRIPT.read_text(), str(SCRIPT), "exec"), {"__name__": "mapping"}, spec)
        cls.scan_jobs = spec["SCAN_JOBS"]
        cls.exempt = spec["UNSCANNED_BY_POLICY"]

    def test_every_tracked_dockerfile_is_mapped_or_exempt(self):
        listed = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split("\n")
        dockerfiles = {p for p in listed if re.search(r"(^|/)Dockerfile(\.[^/]+)?$", p)}
        self.assertFalse(dockerfiles - set(self.scan_jobs) - set(self.exempt))

    def test_mapped_and_exempt_paths_exist(self):
        for path in [*self.scan_jobs, *self.exempt]:
            self.assertTrue((ROOT / path).is_file(), path)

    def test_every_mapped_job_is_in_the_quality_aggregate(self):
        quality = (ROOT / ".github/workflows/quality.yml").read_text()
        needs_block = quality.split("    needs:\n      - changes\n", 1)[1].split("    runs-on", 1)[0]
        for name in set(self.scan_jobs.values()):
            self.assertRegex(quality, rf"(?m)^  {re.escape(name)}:$")
            self.assertIn(f"      - {name}\n", needs_block)


if __name__ == "__main__":
    unittest.main()
