# Codex-specific notes

Reference for agents running under Codex (GPT-5.5) in this repo. The binding
rules are in the `AGENTS.md` hierarchy; this file only explains what differs
from Claude so no agent makes a false assumption.

## What Codex does NOT have (that Claude does)

- **No Stop / PreToolUse hooks.** Claude mechanically enforces the confidence
  format, fail-loud, worktree, and container-hygiene rules via hooks in
  `.claude/settings.json`. Codex runs none of these. Under Codex, the rules in
  `AGENTS.md` are self-enforced — there is no safety net that blocks a bad
  action or rejects a "done" claim without evidence. Hold yourself to them.
- **No `paths:`-scoped auto-loading.** Claude auto-loads `.claude/rules/klai/**`
  when matching files are opened. Codex only auto-loads files named `AGENTS.md`
  in the directory chain (root → cwd, 32 KiB combined cap). The compact gates
  in each `AGENTS.md` are therefore self-sufficient; the `.claude/rules/**`
  files are optional deeper reference you must choose to open.
- **No MoAI `/moai …` orchestration or Claude `Agent()` subagent catalog.**
  Those are Claude-only. Use Codex's own task/plan model; do not treat MoAI
  command syntax or the Agent catalog as required workflow.

## Reasoning effort

Default is `medium` (set in `~/.codex/config.toml`). Re-evaluate the level
before escalating — escalate for genuine uncertainty, not by default:

- `none` / `low` — titles, branch names, small docs, known-file copy edits.
- `medium` — normal Codex work: scoped bugfixes, PR comments, small
  backend/frontend tasks.
- `high` — architecture, security, migrations, multi-file refactors, unclear
  production bugs, schema changes, live deploys.
- `xhigh` — only when "we don't know where the truth is": large system design,
  cross-service root-cause, security-critical review.

## What Codex shares with Claude

- The same MCP servers (`serena`, `context7`, `playwright`, `codeindex`,
  `grafana`, `victorialogs`) — configured in `~/.codex/config.toml`.
- The same model-neutral rules in the root and nested `AGENTS.md` files.

## Serena under Codex

Codex is configured to start Serena from the current project, but Codex does not
see Serena's project prompt or memories until the Serena tools are loaded. For
code exploration tasks:

1. Load Serena's `initial_instructions` first when the MCP is available.
2. Follow `.serena/project.yml` for source-code exploration and symbol edits.
3. Use normal file reads/search for Markdown, YAML, config, env examples, and
   other non-code files.

Tracked `.serena/memories/**` files are public repo content. They may contain
stable, contributor-safe project orientation only. Keep production operations in
`klai-infra` and GTM/compliance/research context in `klai-private`.

## How instructions load under Codex

Codex concatenates `AGENTS.md` from the repo root down to your working
directory; closer files win on conflict. A linked file that is NOT named
`AGENTS.md` is read only if you decide to open it — never rely on a link to
carry a hard rule. That is why every gate here is inline.
