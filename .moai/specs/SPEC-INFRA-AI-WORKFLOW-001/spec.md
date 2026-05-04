# SPEC-INFRA-AI-WORKFLOW-001 — AI workflow hygiene mechanical guards

**Status:** draft
**Created:** 2026-05-04
**Type:** infrastructure / tooling

## Why this exists

A multi-AI-coding-session workflow on this repo accumulates entropy faster
than humans can clean it up:

- 39 worktrees on disk, only 5 active. Net result: ~34 worktrees of
  long-merged or abandoned work, blocking the canonical main checkout
  via cross-worktree main-collision.
- 30+ stale local branches with `gone` upstream — never auto-pruned.
- 10 stashes accumulated across sessions. Most contain WIP from abandoned
  branches; some are dirty work that was stashed during context-switches
  and never resolved.
- Submodule pointers drift per session (`m klai-private` `m klai-website`)
  because each session bumps them independently.

The 2026-05-02 librechat-voys incident (SPEC-INFRA-CONTAINER-HYGIENE-001)
established the principle: **mechanical guards, not markdown rules**.
This SPEC applies the same principle to git workflow: an AI agent does
the primary git work, so the hygiene rules MUST be enforced in code, not
documented as "agents should remember to".

## Requirements

### REQ-1 [HARD] No worktree-add on `main`

A `git worktree add <path> main` (or any flag-form that ends up checking
out main in a non-canonical location) MUST be blocked at PreToolUse. A
worktree on main forces the canonical main repo into detached-HEAD
whenever the user wants to switch to main, creating exactly the friction
that caused the cleanup mess.

Allowed: `git worktree add <path> -b feature/X main` (creates a NEW
branch FROM main; main itself stays in canonical repo).

### REQ-2 [HARD] Auto-teardown after PR merge

After a successful `gh pr merge ...`, if the current working directory
is a worktree (not the canonical repo), PostToolUse MUST:

1. Detect the worktree's path.
2. Print a clear "this worktree is now dead, run X to remove" hint with
   the exact `git worktree remove --force <path>` command.

The hook MUST NOT auto-execute the removal — that would surprise users
who are still poking around. The hook MUST surface the next step
clearly, so agents and humans both see it.

### REQ-3 [SHOULD] Block `git stash push` in agent sessions

Stashes are silent backlog. PreToolUse on `git stash push` MUST:

1. Block the command.
2. Suggest the alternative: commit with a `WIP-` prefix in the current
   branch, switch context, return, then amend or interactive-rebase the
   WIP commit away.

Exception: `git stash push -m "RESCUE-..."` is allowed (cleanup-time
rescue is a legitimate use).

### REQ-4 [SHOULD] SessionStart hygiene audit

On every Claude Code SessionStart, run a hygiene audit and warn if any
of these thresholds are exceeded:

- More than 5 worktrees besides the canonical repo
- More than 3 local branches with `gone` upstream
- More than 3 stashes that are NOT prefixed with `RESCUE-`

The warning MUST suggest the cleanup commands (one-liner per threshold
exceeded). It MUST NOT auto-clean — humans review and decide.

### REQ-5 [HARD] Pitfall documentation

A new pitfall `worktree-teardown-after-merge` MUST be added to
`.claude/rules/klai/pitfalls/process-rules.md` documenting:

- The failure mode: `gh pr merge --delete-branch` from inside a worktree
  fails its local-branch cleanup step silently when run from the worktree
  itself, leaving the worktree on disk forever.
- The Windows-specific edge case: `git worktree remove --force` may hang
  on locked agent worktrees if file handles are still open in the dir.
- The recovery path: detach-HEAD trick to free a branch from the
  canonical repo when a worktree claims `main`.

## Out of scope

- Refactoring existing worktrees (one-off cleanup, not a recurring
  problem once these guards are live).
- Per-session locking semantics (file-level conflicts between parallel
  AI sessions). Belongs in a follow-up SPEC.
- Submodule pointer discipline. Mentioned in the workflow advice but
  not enforced by this SPEC's hooks.

## Acceptance criteria

- AC-1: `git worktree add /tmp/x main` is blocked by the PreToolUse hook
  with a clear "use `-b feature/X` to branch FROM main instead" message.
- AC-2: After `gh pr merge 999 --admin --squash --delete-branch` from
  inside a worktree, PostToolUse prints the exact `git worktree remove
  --force <path>` command for the current worktree.
- AC-3: `git stash push` (without `RESCUE-` message) is blocked with a
  suggestion for the WIP-commit alternative.
- AC-4: SessionStart prints a hygiene-audit summary; thresholds exceeded
  trigger a warning with cleanup commands.
- AC-5: `.claude/rules/klai/pitfalls/process-rules.md` contains the
  `worktree-teardown-after-merge` pitfall.

## Verification

Each hook is a standalone bash script with deterministic input/output.
Test by piping mock JSON inputs to each hook and asserting the
`decision`/`reason` JSON output matches expectations. No CI changes
needed — hooks live in `.claude/hooks/klai/` and are loaded by Claude
Code's session runtime.
