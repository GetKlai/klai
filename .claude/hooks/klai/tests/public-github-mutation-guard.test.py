#!/usr/bin/env python3
"""Regression tests for the public GitHub mutation guard."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock


HOOK = Path(__file__).resolve().parents[1] / "public-github-mutation-guard.py"
SPEC = importlib.util.spec_from_file_location("public_github_mutation_guard", HOOK)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot import hook from {HOOK}")
GUARD = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = GUARD
sys.dont_write_bytecode = True
SPEC.loader.exec_module(GUARD)


class PublicGitHubMutationGuardTest(unittest.TestCase):
    def run_guard(
        self, command: str, *, current_branch: str | None = None
    ) -> tuple[int, str, mock.Mock]:
        payload = io.StringIO(json.dumps({"tool_input": {"command": command}}))
        stderr = io.StringIO()
        with (
            mock.patch.object(sys, "stdin", payload),
            mock.patch.object(
                GUARD, "_current_git_branch", return_value=current_branch
            ) as branch_lookup,
            redirect_stderr(stderr),
        ):
            exit_code = GUARD.main()
        return exit_code, stderr.getvalue(), branch_lookup

    def assert_allowed(
        self, command: str, *, current_branch: str | None = None
    ) -> mock.Mock:
        exit_code, stderr, branch_lookup = self.run_guard(
            command, current_branch=current_branch
        )
        self.assertEqual(0, exit_code, stderr)
        return branch_lookup

    def assert_blocked(
        self,
        command: str,
        message: str,
        *,
        current_branch: str | None = None,
    ) -> mock.Mock:
        exit_code, stderr, branch_lookup = self.run_guard(
            command, current_branch=current_branch
        )
        self.assertEqual(2, exit_code, stderr)
        self.assertIn(message, stderr)
        return branch_lookup

    def test_allows_named_feature_push_after_heredoc_commit(self) -> None:
        command = """\
cd /path/to/repo
git add -A klai-portal/frontend
git commit -q -F - <<'MSG'
It's a `design-callouts` change — with “quotes”.
MSG
git push -q -u origin mvletter/design-callouts && echo "pushed"
"""

        branch_lookup = self.assert_allowed(command)

        branch_lookup.assert_not_called()

    def test_allows_named_feature_push_in_simple_compound(self) -> None:
        command = (
            'git status --short; echo "---"; '
            "git push -u origin mvletter/design-callouts 2>&1 | tail -3"
        )

        branch_lookup = self.assert_allowed(command)

        branch_lookup.assert_not_called()

    def test_allows_pr_mutation_words_inside_heredoc_data(self) -> None:
        command = """\
cat > /tmp/notes.md <<'EOF'
Then we ran gh pr merge 123 and it worked.
EOF
"""

        self.assert_allowed(command)

    def test_allows_mutation_words_in_unquoted_and_tab_stripped_heredocs(
        self,
    ) -> None:
        commands = [
            "cat <<EOF\ngit push origin main\nEOF\n",
            "cat <<-EOF\n\tgh issue close 123\n\tEOF\n",
        ]

        for command in commands:
            with self.subTest(command=command):
                self.assert_allowed(command)

    def test_heredoc_data_cannot_supply_the_code_approval_marker(self) -> None:
        command = """\
cat <<'EOF'
KLAI_ALLOW_PUBLIC_CODE_MUTATION=1
EOF
gh pr merge 123
"""

        self.assert_blocked(
            command, "autonomous public GitHub PR mutation"
        )

    def test_non_heredoc_shift_operators_do_not_hide_a_real_mutation(self) -> None:
        commands = [
            "printf '%s\\n' \"<<'EOF'\"\ngh pr merge 123\nEOF\n",
            "echo $((1 <<EOF))\ngh pr merge 123\nEOF\n",
        ]

        for command in commands:
            with self.subTest(command=command):
                self.assert_blocked(
                    command, "autonomous public GitHub PR mutation"
                )

    def test_unsupported_compound_delimiter_fails_closed(self) -> None:
        command = """\
cat <<E'OF'
notes
EOF
gh pr merge 123
"""

        self.assert_blocked(
            command, "autonomous public GitHub PR mutation"
        )

    def test_allows_standalone_named_feature_push(self) -> None:
        branch_lookup = self.assert_allowed(
            "git push origin mvletter/design-callouts", current_branch="main"
        )

        branch_lookup.assert_not_called()

    def test_blocks_every_explicit_main_destination(self) -> None:
        commands = [
            "git push origin main",
            "git push origin refs/heads/main",
            "git push origin HEAD:main",
            "git push origin feature:main",
            "git push origin feature:refs/heads/main",
            "git push origin :main",
            "git push origin --delete main",
            "git push --all",
            "git push --mirror origin",
            "git push --force origin main",
            "git push -f origin feature:main",
            "git push --force-with-lease origin HEAD:main",
            "git push origin +feature:main",
        ]

        for command in commands:
            with self.subTest(command=command):
                branch_lookup = self.assert_blocked(
                    command, "public main branch push", current_branch="feature/safe"
                )
                branch_lookup.assert_not_called()

    def test_bare_push_uses_current_branch_and_fails_closed_when_unknown(self) -> None:
        cases = [
            ("main", True),
            ("feature/design-callouts", False),
            (None, True),
        ]

        for current_branch, should_block in cases:
            with self.subTest(current_branch=current_branch):
                if should_block:
                    branch_lookup = self.assert_blocked(
                        "git push",
                        "public main branch push",
                        current_branch=current_branch,
                    )
                else:
                    branch_lookup = self.assert_allowed(
                        "git push", current_branch=current_branch
                    )
                branch_lookup.assert_called_once_with([], None)

    def test_head_push_in_unsupported_shell_context_fails_closed(self) -> None:
        branch_lookup = self.assert_blocked(
            "(cd /path/to/repo && git push origin HEAD)",
            "public main branch push",
            current_branch="feature/session",
        )

        branch_lookup.assert_not_called()

    def test_blocks_real_pr_mutation_outside_heredoc_without_marker(self) -> None:
        self.assert_blocked(
            "gh pr merge 123 --squash", "autonomous public GitHub PR mutation"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
