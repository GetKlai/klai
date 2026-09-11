# Klai — Agent Operating Rules

Model-neutral rules for every agent in this monorepo (Codex, Claude, or other).
Codex reads this file natively. Claude reads it via `@AGENTS.md` in `CLAUDE.md`.
A nested `AGENTS.md` closer to the file you edit overrides anything here.

> Enforcement note: Claude runs Stop/PreToolUse hooks that mechanically enforce
> some of these rules. **Codex runs no such hooks** — under Codex these rules
> are self-enforced only. Treat them as MUST, not as suggestions.
> Codex-specific notes: `.agents/codex/README.md`.

## Prime directive — autonomy on execution, strictness on claims

- Keep going. Do not ask permission to proceed, to push approved work, or to
  move to the next step. Stop only for (a) a real decision the user must make,
  or (b) a failing guard: CI red, test failure, or unverifiable external state.
- You may NOT claim "fixed / done / deployed" without evidence per claim.
  "Looks correct" / "should work" / "reviewed the code" is not evidence — it
  scores zero. Prove it, don't assert it.
- If the user asks to ship, merge, push, or get work live on `main`, do not stop
  at an open PR. Either get the intended commit reachable from `origin/main` and
  verify main CI/deploy/E2E/live health, or report the exact blocker. Production
  host-specific proof steps live in the private `klai-infra` runbook
  `docs/runbooks/ship-public-klai-to-main.md`.

## Always-on engineering discipline

- **data-before-code** — Trace real logs / DB / runtime before fixing. No
  guessing, no stacked patches. For production: query VictoriaLogs by
  `request_id:<uuid>`. One root cause confirmed by data = one fix.
- **measure, then scope** — Data that shows a wider problem than the reported bug
  goes into the report as a backlog item with the numbers. It does not widen this
  change. One ticket, one cause, one fix.
- **fail loudly** — No silent fallback on external-provider drift. Unknown
  external state = raise an error, or name the condition under which the change
  fails and what you would see. No "best-effort success" when the core mutation
  failed. (Database-layer RLS
  defense-in-depth is deliberate and stays — this rule is about app-layer
  shims, not the DB security model.)
- **minimal changes** — Only what was asked. No drive-by refactors, reformatting,
  or "improvements" to untouched files.
- **clean over clever, no parallel old+new** — Remove the code your change
  replaces in the SAME change: no dead fields, no commented-out blocks, no old
  and new flow living side by side. Clean solutions over defensive clutter. (This
  is about removing what you replaced — not editing untouched files; it composes
  with "minimal changes", it does not contradict it.)
- **scale the answer to the problem** — Lead with the simplest solution that
  works (the 5-minute fix if one exists); escalate to a bigger design only when
  the problem demands it. No SPEC for something that affects 1–5 people. Report
  what you deliberately left out as a decision: what, why, and when it must
  still happen.
- **verify-changes-landed** — Before reporting done: `git diff --stat` (right
  files?), service health/logs (running new code?), and a Playwright
  click-through for any UI change (real user flow works?).
- **search broadly when changing a default/name** — grep every consumer, all
  case variants (kebab, snake, camel, Pascal, SCREAMING_SNAKE). Defaults have
  unbounded blast radius.
- **no plausible assumptions** — Do not infer `message.sources`, Zitadel
  password policy, streaming chunks, BFF cookies, OIDC flows, or performance
  paths from intuition. Require evidence from code, tests, logs, docs, or an
  explicit user confirmation.
- **verify vendored contracts on the bump** — Bumping a pinned image or a
  network SDK is not done until something exercised the contract you rely on
  against the NEW version. Two rules learned the hard way, both in August
  2026: read the installed source or the running container, never the schema
  (docling-serve's OpenAPI types `task_status` as a six-value enum the server
  can only ever answer with four of, and trusting it produced the wrong fix);
  and record the verified version in a test, not in a docstring — a docstring
  goes stale in silence, a test goes red. A mocked unit test asserting "we
  sent the right shape" proves nothing about whether the dependency still
  accepts that shape: crawl4ai 0.9 started answering `400` to a field we had
  always sent, every authenticated crawl broke for weeks, and the whole suite
  stayed green.

## Conductor handoff contract

Conductor workspaces are isolated git worktrees. Do not assume another agent,
workspace, or chat can see this chat history, this workspace's `.context`
directory, local uncommitted files, attachments, browser state, VictoriaLogs
queries, or terminal output.

- When asking another Conductor session/agent to continue work, provide a fully
  self-contained handoff: task, current branch/workspace, exact files, relevant
  request IDs/log query keys, commands already run, findings, assumptions,
  remaining questions, and current git diff summary.
- If the handoff depends on generated artifacts, either put them in a tracked
  repo path or paste their relevant contents into the handoff. A `.context/*`
  path is valid only after explicitly confirming the receiving agent is in the
  same workspace and can read that path.
- Public share links, screenshots, and product URLs are context only. They do
  not replace request IDs, logs, DB/Qdrant evidence, or source-code references.
- Before sending a handoff prompt, sanity-check it as if pasted into a brand-new
  workspace with zero prior conversation. If it would not be actionable there,
  rewrite it before sending.
- When the user asks for a prompt for another agent/session, the thread is the
  primary deliverable: output the complete prompt directly in the thread first.
  Do not rely on a summary, attachment, markdown file, omitted sections, or
  "same as above" references. Create a handoff file only when the user
  explicitly asks for a file, or as an additional artifact after the full prompt
  has already been posted in the thread.
- If the user says a prompt is wrong or incomplete, respond with the full
  corrected prompt. Do not answer only with agreement, diagnosis, or a partial
  replacement snippet.
- If the user corrects the collaboration format itself, update the documented
  workflow rule immediately and then continue using the corrected format in the
  same turn. Do not repeat the previous delivery mechanism after the user has
  rejected it.

## Public publication boundary

This repository is public. A backlog finding is conversation output, not
authorization to mutate GitHub.

- Never create, edit, or comment on a public GitHub issue unless the user's
  current request explicitly asks for that exact public mutation. "Track it",
  "backlog item", autonomous execution, and finding something outside the
  current task are not authorization.
- Potential security findings stay private by default. Do not publish exploit
  steps, reachability, live-system evidence, secret paths, or unpatched details
  in issues, PRs, discussions, commits, tracked files, or public Actions output.
  Report them in the private conversation first. Use a draft GitHub Security
  Advisory or a private repository only when the user explicitly authorizes
  that route.
- A public repository branch or PR is itself disclosure. Before pushing an
  unpatched security fix, establish the private remediation and deployment
  path with the user. After the fix is deployed, a deliberately redacted public
  explanation is allowed when requested.
- The hook that enforces this asks WHERE a command is aimed, not which verb
  it uses. Inside GetKlai it blocks nothing: `main` is already gated by branch
  protection, and a marker the one developer types on every merge is a
  keystroke, not a second opinion. Outside GetKlai it blocks opening a pull
  request that is not a draft, undrafting one, and the same publication made
  through `gh api` or `curl` — because a branch on your own fork notifies
  nobody, while those three put it in front of someone else's maintainers.
  Open it with `--draft`, show the user, undraft only once they have said to.
  It still blocks issue mutations everywhere; issues have no other gate.
  Everything above still governs what you WRITE in a PR body — that judgement
  is yours, not the hook's.
- If sensitive details are already public, do not amplify them in comments or
  linked issues. Prioritize remediation, then close or redact the public item
  only with explicit user authorization.

## Local / production browser testing

See `.claude/rules/klai/lang/browser-testing.md` (loads when the portal frontend, e2e, or the preflight script are touched).

## pen.dev design files

Portal design (.pen files): see `.claude/rules/klai/design/pen-files.md` (loads when design files are touched).

## Production bugfix gate

Steps 1–5 apply to class M and L fixes (see the global size-before-shape rule),
and to any bug touching auth, tenancy, data loss, money or a multi-path helper.
For a class S fix (one reproduced cause, one place) the gate is: reproduce with
data, one failing test that names the symptom, patch, full suite green, and the
size line in the report. No state matrix for a detector with a regex bug.

Treat a customer report as a SYMPTOM, not a diagnosis. Before closing:

1. **Contract first** — write down: visible problem · which system contract is
   hit · who is source of truth (frontend / backend / Zitadel / LibreChat /
   LiteLLM / retrieval-api / …) · which tests prove it, which are missing.
2. **Reproduce or trace** the real runtime/code path — confirmed by data.
3. **State/lifecycle matrix** — list every state the entity can hold and every
   route/API/UI action that mutates it; check the fix against all of them.
4. **Regression test first** — write a test that FAILS on the broken
   user-visible behavior BEFORE the patch. Test name names the contract, not
   the implementation. Then patch. Cover: reported path + one adjacent edge +
   the legacy/partial-failure external-system state.
5. **Shared-helper stopgate** — if the change touches a multi-path helper
   (citation rendering, password policy, auth, Zitadel, streaming, caching,
   retrieval): before patching, report direct callers, indirect paths, which
   paths you test, and which you cannot.

Auth / invite / delete / offboard / suspend / IdP bugs have an extra gate in
`klai-portal/backend/AGENTS.md`. Use `codebase-memory-mcp cli trace_path --project <name> --function-name <fn> --direction inbound` before editing
any shared helper; if the graph is stale, verify against source + git history.

## Customer-facing publishing (private runbooks)

Three separate publishing paths, all operated from the private `klai-infra`
repo. None of these workflows is documented in this public repo.

| Surface | Runbook |
|---|---|
| "What's new" product-updates feed (megaphone in the portal) | `PRODUCT_UPDATES.md` |
| Customer help centre (Klai Docs KB `klai-help`) | `HELP_SYSTEM.md` |
| getklai.com website and blog | `docs/runbooks/website-publishing.md` |

When publishing a product-updates batch, check whether the help centre
needs matching page updates.

For website content, two things are worth knowing before you start, because
both have already cost time. `klai-website` is a submodule that is usually
NOT checked out in a Conductor worktree, so edit it in its own checkout, not
from here. And a push to its `main` is the deploy — it is Coolify-hosted and
rebuilds automatically, with no staging branch and no approval gate. Build
locally first, and verify the deployed commit and the live URL afterwards.
`docs/runbooks/website-publishing.md` has the exact commands.
<!-- codebase-memory:start -->
## codebase-memory-mcp (code graph, CLI only)

This repo is indexed by `codebase-memory-mcp` (pinned v0.10.8 in `~/bin`), used only through its CLI: no MCP server, no daemon watcher, no hooks. The graph is a precomputed map, not a source of truth: verify every hit in the source.

- **Index (first use in a worktree, after a merge, and after your own changes):** `codebase-memory-mcp cli index_repository --repo-path .` (about 10 s, incremental on rerun). The `project` field in its output is the project name for the commands below. `.cbmignore` un-skips code directories the built-in skip-list would drop.
- **Impact before changing a shared symbol:** `codebase-memory-mcp cli trace_path --project <name> --function-name <fn> --direction inbound` with `--depth 1` lists the callers you must check before editing; the default depth 3 is the blast radius for your report; go deeper only to find the route or entry point that reaches the symbol. On an ambiguous name pass the `qualified_name` it suggests. `search_graph --project <name> --name-pattern "<regex>" [--label Function|Method|Class|Route]` finds symbols and HTTP routes.
- **Orientation:** `get_architecture --project <name>` for the summary and `detect_changes --project <name>` for the symbols touched by the current diff against main.
- Full command reference and worked examples: the global `codebase-memory` skill.
<!-- codebase-memory:end -->
