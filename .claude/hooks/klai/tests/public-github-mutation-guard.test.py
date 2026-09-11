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

OUTSIDE = "outside GetKlai"
ISSUES = "autonomous public GitHub issue mutation"


class GuardTestCase(unittest.TestCase):
    """Shared harness.

    ``origin_owner`` stands in for reading `git remote get-url origin`, which
    is how the guard works out where a command with no ``--repo`` is aimed.
    It defaults to our own org so a test has to opt in to being elsewhere.
    """

    def run_guard(self, command: str, *, origin_owner: str | None = "getklai"):
        payload = io.StringIO(json.dumps({
            "tool_input": {"command": command},
            "cwd": "/some/checkout",
        }))
        stderr = io.StringIO()
        with (
            mock.patch.object(sys, "stdin", payload),
            mock.patch.object(GUARD, "_origin_owner", return_value=origin_owner),
            redirect_stderr(stderr),
        ):
            exit_code = GUARD.main()
        return exit_code, stderr.getvalue()

    def assert_allowed(self, command: str, *, origin_owner: str | None = "getklai") -> None:
        exit_code, stderr = self.run_guard(command, origin_owner=origin_owner)
        self.assertEqual(0, exit_code, stderr)

    def assert_blocked(
        self, command: str, message: str, *, origin_owner: str | None = "getklai"
    ) -> None:
        exit_code, stderr = self.run_guard(command, origin_owner=origin_owner)
        self.assertEqual(2, exit_code, stderr)
        self.assertIn(message, stderr)


class OurOwnRepositoryTest(GuardTestCase):
    """Inside GetKlai the guard is out of the way. Branch protection is the gate."""

    def test_pushing_to_main_is_not_our_business(self) -> None:
        for command in (
            "git push origin main",
            "git push origin HEAD:main",
            "git push --force-with-lease origin main",
        ):
            with self.subTest(command=command):
                self.assert_allowed(command)

    def test_merging_our_own_pr_needs_no_marker(self) -> None:
        for command in (
            "gh pr merge 42 --squash",
            "gh pr merge 42 --merge --admin",
            "gh pr create --title x --body y",
            "gh pr ready 42",
            "gh pr comment 42 --body 'looks good'",
        ):
            with self.subTest(command=command):
                self.assert_allowed(command)

    def test_an_explicit_getklai_repo_is_still_ours(self) -> None:
        self.assert_allowed(
            "gh pr merge 42 --repo GetKlai/klai-infra", origin_owner="somebodyelse"
        )


class AnotherOrganisationTest(GuardTestCase):
    """Outside GetKlai, opening or undrafting a PR is the user's call."""

    def test_opening_a_pr_elsewhere_is_blocked(self) -> None:
        self.assert_blocked(
            "gh pr create --repo unclecode/crawl4ai --title x --body y", OUTSIDE
        )

    def test_a_draft_elsewhere_still_needs_the_user_to_have_asked(self) -> None:
        """There is no --draft exemption, on purpose.

        A draft is still a pull request someone else did not ask for, and the
        exemption was where the holes were: a body reading "use --draft next
        time" satisfied it, and so did a --draft in a later command on the
        same line.
        """
        self.assert_blocked(
            "gh pr create --repo unclecode/crawl4ai --draft --title x", OUTSIDE
        )

    def test_undrafting_elsewhere_is_blocked_even_though_the_pr_exists(self) -> None:
        self.assert_blocked("gh pr ready 2249 --repo unclecode/crawl4ai", OUTSIDE)

    def test_putting_it_back_to_draft_is_a_retreat_not_a_publication(self) -> None:
        self.assert_allowed("gh pr ready 2249 --repo unclecode/crawl4ai --undo")

    def test_short_and_env_spellings_of_the_repo_are_read_too(self) -> None:
        """gh takes -R and GH_REPO as well. A guard that knows only the long
        flag is a guard with a written-down way around it."""
        for command in (
            "gh pr create -R unclecode/crawl4ai --title x",
            "GH_REPO=unclecode/crawl4ai gh pr create --title x",
        ):
            with self.subTest(command=command):
                self.assert_blocked(command, OUTSIDE)

    def test_undo_switched_off_explicitly_is_still_a_publication(self) -> None:
        self.assert_blocked(
            "gh pr ready 2249 --repo unclecode/crawl4ai --undo=false", OUTSIDE
        )

    def test_a_graphql_pr_mutation_cannot_prove_where_it_lands(self) -> None:
        """It addresses an opaque node id, so nothing in the command says which
        repository it hits -- not even from one of our own checkouts."""
        self.assert_blocked(
            "gh api graphql -f query='mutation "
            "{ markPullRequestReadyForReview(input: {}) { pullRequest { id } } }'",
            OUTSIDE,
        )

    def test_gh_short_verbs_are_classified_like_their_long_forms(self) -> None:
        self.assert_blocked("gh pr new --repo unclecode/crawl4ai --title x", OUTSIDE)
        self.assert_allowed("gh pr ls --repo unclecode/crawl4ai")

    def test_a_read_only_verb_cannot_shelter_a_mutation_behind_it(self) -> None:
        """A greedy tail judged `gh pr view && gh pr create` on the view."""
        self.assert_blocked(
            "gh pr view 1 --repo unclecode/crawl4ai && "
            "gh pr create --repo unclecode/crawl4ai --title x",
            OUTSIDE,
        )

    def test_a_getklai_name_in_argument_text_does_not_make_it_ours(self) -> None:
        """Run from an external checkout with our name sitting in a --body."""
        self.assert_blocked(
            "gh pr comment 42 --body 'see example --repo GetKlai/klai please'",
            OUTSIDE,
            origin_owner="unclecode",
        )

    def test_a_repository_named_by_variable_cannot_be_resolved(self) -> None:
        """`-R "$TARGET"` names a destination this hook cannot read. That is
        not "none named"; it is unknown, and unknown is elsewhere."""
        self.assert_blocked('gh pr ready 42 -R "$TARGET"', OUTSIDE)

    def test_a_directory_change_invalidates_the_checkout_we_were_handed(self) -> None:
        self.assert_blocked("cd /somewhere/else && gh pr ready 42", OUTSIDE)

    def test_a_getklai_path_in_argument_text_does_not_clear_it(self) -> None:
        """Unlike a tokenised flag, `repos/owner/name` can sit inside a quoted
        argument, so our name appearing there proves nothing."""
        self.assert_blocked(
            "gh pr comment 42 --body 'see repos/GetKlai/klai for the pattern'",
            OUTSIDE,
            origin_owner="unclecode",
        )

    def test_a_read_only_invocation_cannot_vouch_for_the_one_beside_it(self) -> None:
        """The repository is named once, on the half that only reads."""
        self.assert_blocked(
            "gh pr view 1 --repo GetKlai/klai && gh pr merge 42",
            OUTSIDE,
            origin_owner="unclecode",
        )

    def test_lowercase_r_is_not_a_repository_selector(self) -> None:
        """`-R` selects a repository; `-r` is reviewer on create and rebase on
        merge. Folding the case read `-r GetKlai/security` as our repo, and
        blocked an ordinary `gh pr merge -r` inside GetKlai."""
        self.assert_allowed("gh pr merge 42 -r")
        self.assert_blocked(
            "gh pr create -r someone/else --title x", OUTSIDE, origin_owner="unclecode"
        )

    def test_the_value_glued_onto_the_short_flag_is_read(self) -> None:
        self.assert_blocked("gh pr create -Runclecode/crawl4ai --title x", OUTSIDE)

    def test_a_pull_request_url_is_a_destination(self) -> None:
        """`gh pr merge <url>` is valid syntax and names the repository."""
        self.assert_blocked(
            "gh pr merge https://github.com/unclecode/crawl4ai/pull/2249 --merge",
            OUTSIDE,
        )

    def test_a_host_prefixed_repository_still_reads_as_ours(self) -> None:
        """gh takes [HOST/]OWNER/REPO; rejecting that spelling blocked our own
        work for no reason."""
        self.assert_allowed("gh pr merge 42 --repo github.com/GetKlai/klai")

    def test_pushd_moves_the_checkout_just_like_cd(self) -> None:
        self.assert_blocked("pushd /elsewhere && gh pr ready 42", OUTSIDE)

    def test_an_api_path_quoted_in_a_body_does_not_block_our_own_work(self) -> None:
        """The path only counts where such a path IS the destination."""
        self.assert_allowed(
            "gh pr comment 42 --body 'call repos/unclecode/crawl4ai/pulls for this'"
        )

    def test_unparseable_shell_is_not_proof_of_anything(self) -> None:
        self.assert_blocked("gh pr merge 42 --body 'unterminated", OUTSIDE)

    def test_a_directory_change_is_fine_when_our_repo_is_named_outright(self) -> None:
        self.assert_allowed("cd /somewhere/else && gh pr merge 42 --repo GetKlai/klai")

    def test_a_fork_pr_aimed_at_us_is_ours(self) -> None:
        """`--head owner:branch` names where the branch lives, not where the PR
        lands. Reading it as the target blocks an ordinary contribution to
        GetKlai."""
        self.assert_allowed("gh pr create --head someone:fix/x --title y --fill")

    def test_an_unknown_destination_fails_closed(self) -> None:
        self.assert_blocked("gh pr merge 1", OUTSIDE, origin_owner=None)

    def test_the_api_route_to_the_same_publication_is_blocked(self) -> None:
        """Run from one of OUR checkouts, so only the path says where it lands."""
        for command in (
            "gh api --method POST repos/unclecode/crawl4ai/pulls -f title=x",
            "curl -X POST https://api.github.com/repos/unclecode/crawl4ai/pulls -d '{}'",
        ):
            with self.subTest(command=command):
                self.assert_blocked(command, OUTSIDE)

    def test_the_api_route_against_our_own_repo_is_fine(self) -> None:
        self.assert_allowed("gh api --method POST repos/GetKlai/klai/pulls -f title=x")

    def test_reading_someone_elses_repo_is_fine(self) -> None:
        for command in (
            "gh pr view 2249 --repo unclecode/crawl4ai",
            "gh pr checks 2249 --repo unclecode/crawl4ai",
            "gh pr diff 2249 --repo unclecode/crawl4ai",
        ):
            with self.subTest(command=command):
                self.assert_allowed(command)


class IssueTest(GuardTestCase):
    """Issues keep their own gate: they have no other one."""

    def test_creating_an_issue_is_blocked(self) -> None:
        self.assert_blocked("gh issue create --title x --body y", ISSUES)

    def test_issue_marker_as_argument_data_cannot_authorize(self) -> None:
        self.assert_blocked(
            'gh issue create --title x --body "KLAI_ALLOW_PUBLIC_ISSUE_MUTATION=1"',
            ISSUES,
        )


class CommandParsingTest(GuardTestCase):
    """The guard reads shell, not prose. These are the ways that went wrong."""

    def test_allows_pr_mutation_words_inside_heredoc_data(self) -> None:
        self.assert_allowed(
            "cat > /tmp/notes.md <<'EOF'\n"
            "Then we ran gh pr merge 123 --repo other/thing and it worked.\n"
            "EOF\n"
        )

    def test_allows_mutation_words_in_unquoted_and_tab_stripped_heredocs(self) -> None:
        for command in (
            "cat <<EOF\ngh pr create --repo other/thing\nEOF\n",
            "cat <<-EOF\n\tgh issue close 123\n\tEOF\n",
        ):
            with self.subTest(command=command):
                self.assert_allowed(command)

    def test_heredoc_data_cannot_supply_the_approval_marker(self) -> None:
        self.assert_blocked(
            "cat <<'EOF'\n"
            "KLAI_ALLOW_PUBLIC_CODE_MUTATION=1\n"
            "EOF\n"
            "gh pr merge 123 --repo other/thing\n",
            OUTSIDE,
        )

    def test_marker_as_argument_data_cannot_authorize(self) -> None:
        """Writing ABOUT the escape hatch must not open it.

        The check was a plain substring match on the command text, so a PR
        body explaining the marker, a commit message quoting it, or a doc edit
        documenting it all authorised the mutation. Heredoc bodies were
        stripped for exactly this reason; a quoted argument was not. That is
        how PR #1400 came to be opened: its body contained a sentence naming
        the marker.
        """
        for command in (
            'gh pr comment 42 --repo other/thing --body "retry with KLAI_ALLOW_PUBLIC_CODE_MUTATION=1"',
            'gh pr edit 42 --repo other/thing --title "KLAI_ALLOW_PUBLIC_CODE_MUTATION=1"',
        ):
            with self.subTest(command=command):
                self.assert_blocked(command, OUTSIDE)

    def test_marker_in_the_assignment_position_still_authorizes(self) -> None:
        """The intended escape hatch keeps working, including after a separator."""
        for command in (
            "KLAI_ALLOW_PUBLIC_CODE_MUTATION=1 gh pr merge 42 --repo other/thing",
            "echo hi && KLAI_ALLOW_PUBLIC_CODE_MUTATION=1 gh pr ready 42 --repo other/thing",
        ):
            with self.subTest(command=command):
                exit_code, _stderr = self.run_guard(command)
                self.assertEqual(0, exit_code, command)

    def test_non_heredoc_shift_operators_do_not_hide_a_real_mutation(self) -> None:
        for command in (
            "printf '%s\\n' \"<<'EOF'\"\ngh pr merge 123 --repo other/thing\nEOF\n",
            "echo $((1 <<EOF))\ngh pr merge 123 --repo other/thing\nEOF\n",
        ):
            with self.subTest(command=command):
                self.assert_blocked(command, OUTSIDE)

    def test_unsupported_compound_delimiter_fails_closed(self) -> None:
        self.assert_blocked(
            "cat <<E'OF'\nnotes\nEOF\ngh pr merge 123 --repo other/thing\n", OUTSIDE
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
