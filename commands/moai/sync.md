---
description: Synchronize documentation, codemaps, create pull request, and capture learnings
argument-hint: "[SPEC-XXX] [--merge] [--skip-mx] [--skip-learn]"
---

Use Skill("moai") with arguments: sync $ARGUMENTS

After the sync completes, unless `--skip-learn` was passed: invoke the manager-learn agent and ask it to review what was done in this cycle and capture any patterns or pitfalls worth preserving. Provide context about the SPEC that was just completed.
