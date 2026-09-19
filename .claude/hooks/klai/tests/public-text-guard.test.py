#!/usr/bin/env python3
"""The gh text guard blocks customer data in what gh would publish, and only that.

Names come from KLAI_TENANT_NAMES; a made-up name keeps the real list out of
this public file.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
HOOK = ROOT / ".claude/hooks/klai/public-text-guard.py"
ENV = {**os.environ, "CLAUDE_PROJECT_DIR": str(ROOT), "KLAI_TENANT_NAMES": "Acme Telecom\n"}


def run(command: str) -> int:
    payload = json.dumps({"tool_input": {"command": command}})
    return subprocess.run(
        [sys.executable, str(HOOK)], input=payload, capture_output=True, text=True, env=ENV, check=False
    ).returncode


class PublicTextGuardTest(unittest.TestCase):
    def test_an_inline_body_with_a_customer_count_is_blocked(self):
        self.assertEqual(run('gh pr create --title x --body "Acme Telecom had 272 answers."'), 2)

    def test_a_body_file_is_read(self):
        """The leaked body was a table, and bodies usually travel in a file."""
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as body:
            body.write("| Tenant | Answers |\n|---|---|\n| someone | 23 |\n")
        self.assertEqual(run(f"gh pr edit 5 --body-file {body.name}"), 2)

    def test_an_api_call_carrying_a_body_is_checked(self):
        self.assertEqual(run("gh api repos/GetKlai/klai/issues -f body='Acme Telecom: 40 tickets a day'"), 2)

    def test_a_pr_number_and_a_date_next_to_a_name_are_not_counts(self):
        self.assertEqual(run('gh pr create --title "fix: Acme Telecom widget (#1520), 18 sep" --body ok'), 0)

    def test_commands_that_publish_nothing_pass(self):
        self.assertEqual(run("echo Acme Telecom had 272 answers"), 0)

    def test_what_runs_before_the_publishing_command_is_not_sent(self):
        self.assertEqual(run("grep 'Acme Telecom 422' notes.md; gh pr create --fill"), 0)
        self.assertEqual(run("grep x notes.md; gh pr create --body 'Acme Telecom had 272 answers.'"), 2)

    def test_reading_a_pr_is_not_publishing(self):
        """Cleaning up a leak starts with reading it, often with the name in a grep."""
        self.assertEqual(run("gh pr view 419 --json body | grep -n 'Acme Telecom | 422'"), 0)
        self.assertEqual(run("gh api repos/GetKlai/klai/pulls/419 --jq .body | grep 'Acme Telecom 422'"), 0)


if __name__ == "__main__":
    unittest.main()
