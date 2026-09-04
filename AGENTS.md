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
- **fail loudly** — No silent fallback on external-provider drift. Unknown
  external state = raise an error or report explicit residual risk. No
  "best-effort success" when the core mutation failed. (Database-layer RLS
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
  the problem demands it. No SPEC for something that affects 1–5 people. State
  explicitly what you deliberately did NOT do.
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
- If sensitive details are already public, do not amplify them in comments or
  linked issues. Prioritize remediation, then close or redact the public item
  only with explicit user authorization.

## Codex + Serena

Codex only auto-loads `AGENTS.md` files. It does not automatically read
`.serena/project.yml`, `.serena/memories/**`, or `.claude/rules/**`.

- For code exploration under Codex, load Serena first when the Serena MCP tools
  are available: call `initial_instructions`, then follow the project prompt in
  `.serena/project.yml`.
- Use Serena for source-code symbol discovery and edits. Use `rg`/normal file
  reads for Markdown, YAML, config, env examples, and other non-code files.
- If Serena is unavailable, continue with local source inspection and state that
  residual risk in the final answer.

Serena memory files in this public repo are public documentation. Keep them
evergreen and contributor-safe only: repo layout, coding patterns, public
service contracts, local development, and self-hosting templates are allowed.
Do not write Klai production hostnames, SSH aliases, IPs, tunnel topology,
secret names that are not already part of public code/config contracts,
operator runbooks, business/GTM plans, compliance records, or customer context
to `.serena/memories/**`. Production operations belong in the private
`klai-infra` repo; business, GTM, compliance, and research context belongs in
the private `klai-private` repo.

## Local / Production Browser Testing Contract

Before any browser-driven portal check (Playwright, Browser MCP, manual
localhost navigation, screenshots, or E2E), establish which runtime contract is
being tested. Do not guess ports, auth mode, proxy target, or whether a
localhost listener belongs to this workspace.

- **Local standalone UI** means: frontend in `VITE_AUTH_DEV_MODE=true`, backend
  in `AUTH_DEV_MODE=true`, frontend proxying to the local backend, no Zitadel,
  no production login redirect. The required preflight is:
  `scripts/local-dev-status.sh --mode local --strict`. If it fails, fix setup or
  report the failure. Do not continue clicking through login.
- **Production E2E** means: no localhost target. Validate credentials/target
  with `scripts/local-dev-status.sh --mode prod-e2e`, then run from
  `klai-portal/frontend` with `source .env.local && npm run test:e2e:prod`.
- **Conductor ports**: if `CONDUCTOR_PORT` is set, the frontend port is
  `CONDUCTOR_PORT` and the backend port is `CONDUCTOR_PORT+1`; otherwise they
  default to `5174` and `8010`. Use `make frontend` / `make backend` or the
  preflight output. Never start an ad-hoc Vite server on a random port to
  "just check" a portal route.
- **Env files**: Vite dev config belongs in
  `klai-portal/frontend/.env.development.local`. `klai-portal/frontend/.env.local`
  may contain production E2E credentials and must not be overwritten for local
  dev.
- If a local portal route lands on `my.getklai.com/login` or another production
  login page while you intended local standalone testing, stop immediately and
  diagnose with `scripts/local-dev-status.sh --mode local --strict`.

## pen.dev design files (klai-portal/frontend/design/)

Portal design lives in git next to the code as pen.dev `.pen` files (JSON).

| Path | What it is |
|---|---|
| `klai-portal/frontend/design/klai.lib.pen` | The design library: pen components mirroring `src/components/ui/`, one pen component per code file (`button.tsx/default`, `badge.tsx/success`, `card.tsx`, ...) |
| `klai-portal/frontend/design/screens/*.pen` | Screen files. They `imports` the library and instance its components via `ref: "klai:<componentId>"` |

Three rules, in order of how expensive they are to get wrong:

- **Tokens are one-way: code is the source, pen follows.** Every `.pen` file's
  `variables` map is generated from the `@theme inline` block in
  `src/index.css` by `scripts/generate-pen-variables.mjs`, run from
  `klai-portal/frontend`. Never hand-edit `variables` in a
  `.pen` file, and never edit `src/index.css` to make a design match. Run the
  generator with `--check` to detect drift. Note the portal root font size is
  110%, so `1rem = 17.6px`; the generator converts radii and spacing at that
  root, and pen values are px.
- **Fonts on the canvas are a substitute, and only on the canvas.** pen.dev
  renders Google Fonts only, so it cannot load the self-hosted brand faces.
  The generator therefore emits a second set of variables — `font-sans-preview`
  (Schibsted Grotesk), `font-display-preview`, `font-display-bold-preview`,
  `font-mono-preview` (DM Mono) — chosen by measuring x-height, cap-height and
  n/o/H/i/M advance widths against the real font binaries, so text occupies
  realistic space. Text nodes reference the `-preview` variables; the truthful
  `font-sans` / `font-mono` tokens stay in the file as the code contract and
  must never be repointed. A preview family is never a reason to change
  `src/index.css`.
- **Opacity is a variable, not a property.** pen.dev has no fill opacity, so a
  Tailwind modifier like `bg-[var(--color-success)]/10` maps to its own
  generated variable `color-success-tint-10`. Those tints are derived from the
  modifiers actually used under `src/`, so if a tint you need is missing it is
  because no component uses it yet — add the usage in code first, then
  regenerate. Never hand-write a tinted hex.
- **The library is the source for new screens.** Build a screen from library
  instances, not from fresh frames. Use variables (`$color-rl-accent`), never
  a hardcoded hex.
- **Generated code is a measurement, not a deliverable.** Code exported or
  generated from a `.pen` file must never be committed over a hand-written
  component. It inlines hex, converts every rem to an arbitrary px value, and
  loses the component boundary, semantics, i18n and responsive behaviour.

Commands an agent may use (auth: `set -a; . ~/.mcp/pen/pen.env; set +a`, then
`pen --workspace klai`):

| Goal | Command |
|---|---|
| Read/modify a `.pen` file deterministically, no model cost | `pen interactive --in <f>.pen --out <f>.pen` then `execute({...})` / `save()` |
| Add a repo SVG as a canvas asset | one reusable `path` node: concatenate the file's `d` attributes, set `viewBox`, set `fill` to a token |
| Regenerate variables from `src/index.css` | `node scripts/generate-pen-variables.mjs` |
| Detect token drift | `node scripts/generate-pen-variables.mjs --check` |
| Let an agent design or generate code | `pen --in <f>.pen --out <f>.pen --model claude-haiku-4-5 --usage ./pen-usage.json --prompt "..."` |

CI enforces both rules: the `quality` job in `.github/workflows/portal-frontend.yml`
runs `--check`, which fails on token drift AND on a committed `fileToken`.

Two pen.dev behaviours that cost time to discover, so do not rediscover them:
a `descendants` override on an instance of an IMPORTED component needs the alias
in the key (`instanceId/klai:childId`) and fails silently without it; and a
`layout: "none"` frame does not resize its children, so overlapping vector
artwork belongs in a single `path` node rather than a frame of paths.

Prefer `pen interactive`: it is a plain MCP bridge and costs no model quota.
Escalate to `--model` only when the task genuinely needs an agent, use
`claude-haiku-4-5` for bulk work, and always log usage. `.pen` files carry a
`fileToken` that ties them to the cloud workspace — this repo is public, so the
generator strips it on write; run the generator before committing a `.pen`
file a designer saved.

## Production bugfix gate (stateful / customer-reported bugs)

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
`klai-portal/backend/AGENTS.md`. Use CodeIndex `impact` before editing any
shared helper; if the index is stale, verify against source + git history.

## End-of-bugfix answer format

Separate evidence from assumptions. Always end with:

```
Proven:           <claim · source path · test name · command run>
Assumed:          <what you took on faith>
Not verified:     <what you could not check>
Tests run:        <commands + result>
Remaining risk:   <honest residual>
Confidence: [0-100] — <one-line evidence summary>   (evidence only; "looks right" = 0)
```

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

<!-- codeindex:start -->
# CodeIndex MCP

This project is indexed by CodeIndex as **klai** (16280 symbols, 20581 relationships, 0 execution flows).

## Rules (MUST follow)

Use CodeIndex when it adds graph value; do not use it as a reflexive wrapper
around ordinary source inspection.

- **Required before high-blast-radius code changes**: call `impact` before
  editing shared helpers, exported/public APIs, cross-module contracts,
  auth/RLS/Zitadel/streaming/caching/retrieval helpers, or doing a rename /
  extraction / refactor. Backend auth/invite/delete/offboard/suspend/IdP work
  still follows the stricter gate in `klai-portal/backend/AGENTS.md`.
- **Required for architecture/debugging questions**: use `query` or `context`
  for "How does X work?", "What breaks if X changes?", unfamiliar flows, or
  multi-hop caller/callee questions.
- **Prefer local source search first** for known-file edits, single-file UI
  work, literal text/CSS/component searches, config/docs/scripts, and direct
  "where is this string/symbol?" lookups. Use `git grep`/`rg`/IDE/Serena, then
  escalate to CodeIndex only if graph context changes the decision.
- **If CodeIndex MCP is unavailable** (`Transport closed`, missing lazy-loaded
  tool, stale advisory while health is green), do not block routine work. Use
  local source + git history, state the residual risk, and run
  `scripts/codeindex-health.sh` only when graph accuracy matters.

## For Tasks That Need CodeIndex

1. **Read `codeindex://repo/{name}/context`** — codebase overview + check index freshness
2. **Match your task to a skill below** and **read that skill file**
3. **Follow the skill's workflow and checklist**

> In Conductor worktrees, a stale warning can mean the current worktree or the
> registered checkout differs from the shared main index. Do **not** run
> `codeindex update` from a feature worktree. Run `scripts/codeindex-health.sh`;
> only if it reports the shared base index is stale, run
> `scripts/codeindex-health.sh --repair`. Treat branch changes as an overlay on
> the shared graph and verify local diffs/source files directly.

## Skills

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/codeindex/codeindex-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/codeindex/codeindex-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/codeindex/codeindex-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/codeindex/codeindex-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/codeindex/codeindex-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/codeindex/codeindex-cli/SKILL.md` |

## If CodeIndex tools appear missing

If you don't see CodeIndex tools in your active toolset, they are almost certainly **deferred/lazy-loaded** by the agent harness — **NOT disconnected**. The MCP server is fine.

Use your harness's tool-discovery mechanism once, then continue with the loaded CodeIndex MCP tools:

- **Claude Code**: call `ToolSearch` with `select:mcp__codeindex__query,mcp__codeindex__context,mcp__codeindex__impact,mcp__codeindex__detect_changes,mcp__codeindex__rename,mcp__codeindex__cypher,mcp__codeindex__remember,mcp__codeindex__recall,mcp__codeindex__forget`
- **Codex / Conductor**: call `tool_search` with the same `select:mcp__codeindex__...` query above

After that the tools are directly callable, usually as `mcp__codeindex__query` / `mcp__codeindex__.query` or plain `query`, depending on the harness. If a `list_repos` tool is not exposed, read the `codeindex://repos` resource instead.

Do **NOT** run `npx codeindex`, `codeindex analyze`, or `codeindex update` as a workaround for "missing MCP" or a stale index. The CLI is for explicit setup/maintenance requests. In Conductor, use `scripts/codeindex-health.sh` to diagnose shared main-index health; if it is healthy, use CodeIndex results as advisory and verify branch-local code against source files.

<!-- codeindex:end -->
