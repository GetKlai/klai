#!/usr/bin/env python3
"""A change to the chat path must record its measurement in the plan.

Between 24 and 29 September 2026 five changes to the widget's chat path went
live without the replay the spec asked for, and a review of real answers found
the share of good answers going down. The gate on paper was skipped five times,
so this one runs in CI: a pull request that touches a file where the chat
decides what a user gets must add a row to the results table of
docs/architecture/chat-quality-history-and-plan.md (section 8), saying what was
expected and what the measuring stick showed.

It checks that the row is there, not what it says: the measurement itself runs
on real conversations, which never enter this repository.

usage: check-chat-path-measured.py <base-ref>
"""

import re
import subprocess
import sys

PLAN = "docs/architecture/chat-quality-history-and-plan.md"
CHAT_PATH = re.compile(
    r"^(klai-portal/backend/app/api/partner\.py"
    r"|klai-portal/backend/app/services/"
    r"(partner_chat|answer_\w+|turn_judge|clarify_\w+|query_paraphrase|query_rewrite"
    r"|escalation_intent|gap_classification|off_topic_referral|chat_turn_rules)\.py"
    r"|klai-libs/chat-prompts/klai_chat_prompts/.*\.py"
    r"|deploy/litellm/(klai_knowledge|klai_kb_\w+|klai_chat_prompts|klai_answer_grounding|custom_router)\.py"
    r"|klai-retrieval-api/retrieval_api/api/retrieve\.py)$"
)


def check(changed: list[str], plan_diff: str) -> str | None:
    """The reason this change may not merge, or None."""
    touched = [path for path in changed if CHAT_PATH.match(path)]
    if not touched:
        return None
    in_results = False
    for line in plan_diff.splitlines():
        if line[1:].startswith("## "):
            in_results = line[1:].startswith("## 8.")
        if in_results and line.startswith("+|") and not set(line) <= set("+|-: "):
            return None
    return (
        f"{len(touched)} file(s) on the chat path changed ({touched[0]}, ...) and section 8 of\n"
        f"  {PLAN} has no new row.\n"
        "  Add the step, what was expected, what the measuring stick showed and the decision."
    )


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


if __name__ == "__main__":
    base = sys.argv[1]
    # Whole-file context, so the section heading is in the diff whatever changed under it.
    problem = check(
        _git("diff", "--name-only", f"{base}...HEAD").split(),
        _git("diff", "--no-color", "-U9999", f"{base}...HEAD", "--", PLAN),
    )
    if problem:
        print(f"chat-path gate: {problem}")
        sys.exit(1)
