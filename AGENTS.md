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

<!-- codeindex:start -->
# CodeIndex MCP

This project is indexed by CodeIndex as **klai** (15301 symbols, 19385 relationships, 0 execution flows).

## Rules (MUST follow)

- **Before ANY code modification**: call `impact` on the symbol(s) you will change
- **Before searching code**: try `query` first — only use Grep/Glob if CodeIndex returns nothing useful
- **"How does X work?" questions**: use `query` or `context` — do NOT start with Grep
- **Skip CodeIndex only for**: non-code conversations, config edits, or single known-file changes

## Always Start Here

1. **Read `codeindex://repo/{name}/context`** — codebase overview + check index freshness
2. **Match your task to a skill below** and **read that skill file**
3. **Follow the skill's workflow and checklist**

> If step 1 warns the index is stale, run `codeindex update` in the terminal first.

## Skills

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/codeindex/codeindex-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/codeindex/codeindex-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/codeindex/codeindex-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/codeindex/codeindex-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/codeindex/codeindex-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/codeindex/codeindex-cli/SKILL.md` |

<!-- codeindex:end -->
