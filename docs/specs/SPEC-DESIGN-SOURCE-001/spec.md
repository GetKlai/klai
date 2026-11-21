# SPEC-DESIGN-SOURCE-001 — The design contract rests on code

**Status:** implemented · **Area:** klai-portal/frontend · **Opened:** 2026-09-01 · **Landed:** 2026-09-01

## Problem

`docs/ui-standards.md` is the canonical UI contract and is mandatory reading
before any portal UI change. It was 1013 lines of prose with three
build-failing checks, and nothing measured the distance between the two.

That distance turned out to be the defect. On 2026-09-01 we tested five claims
the document made about the code. All five were wrong:

| Claim | Reality |
|---|---|
| Four canonical page containers | 66 containers across ~20 combinations |
| `Button` has `ghost` and `outline` | One variant with two names, 172 call sites |
| Component table lists the variants | Missing `lg` (23 uses) and `link` (7 uses) |
| ~21 raw form controls are violations | 10 were; 2 were correct; 10 were a design decision |
| Ledger rows restate the prose | 12 had dropped their exception clauses |

The one claim that held was the only one somebody had counted: 96 of 100 raw
`<button>` elements are the prescribed pattern, which is why no lint rule was
written for them.

The mechanism is not carelessness. A sentence about code goes stale silently:
the code moves, the sentence does not, and nothing complains because the
sentence still reads fluently. In a codebase where agents write UI, a stale
sentence is worse than a missing one — an agent applies it with confidence, and
one deviation becomes the precedent the next agent copies.

## The model

A rule can live at four levels. Strongest first:

1. **Impossible** — the API does not permit the violation. `PageContainer` owns
   padding and centering, so you cannot forget them.
2. **Caught** — a check fails the build.
3. **Derived** — the documentation is generated from code and cannot lie.
4. **Stated** — prose a human or agent applies.

Before this work, effectively everything sat at level 4. The goal is not to
empty level 4 — judgement belongs there — but to make sure nothing sits there
that could sit lower.

## State after the first pass

Shipped in PR #1285 (commits `b309ae0`, `aebd1d1`, `0fe235e`, `5db7c28`,
`821b964`). Do not restate their content here; read the commit messages.

- 50 rules in the ledger, each with a stable ID, an RFC 2119 level and a
  verification mode. `tests/design/rules-ledger.test.ts` guards it in both
  directions and asserts the counts against the summary.
- 12 rules held by the machine (8 automated, 4 assisted), 38 prose-only.
- The Component Library Reference is generated from `@purpose` comments and the
  real `cva` variant axes; a stale table fails the build.
- `PageContainer` absorbed the layout rule. `ghost` removed from `Button`.

The 38 prose-only rules classify as:

- **21 component-level** — belong on a component file (`Badge`, `row-action`, …)
- **7 pattern-level** — span components, no single file to own them
- **10 global or process** — Paraglide, the type scale, workflow

## Decision: formats are outputs, not sources

Two specifications compete for this space. Measured 2026-09-01:

| | DESIGN.md (Google Labs) | DSDS |
|---|---|---|
| npm downloads / month | 606,306 | 88 |
| Commits total / last 30d | 62 / 0 | 226 / 5 |
| Format | YAML tokens + markdown prose | JSON + JSON Schema |
| Ships | CLI (validates WCAG contrast) | CLI + MCP server |

DESIGN.md has the adoption; DSDS has the richer model for judgement — levels,
verification modes, an entity model of component/pattern/guide that our own
21/7/10 split falls into unprompted.

**We adopt neither as a source.** Both are hand-authored formats, and today
proved that every hand-maintained description of code drifts. Once the rules
live beside the code, emitting either is a render function over data we already
hold, and the choice becomes reversible.

We would reconsider only if the DSDS MCP server measurably outperforms the
ledger for an agent building UI. That is a measurement, not a preference, and
it has not been run.

Figma speaks none of this. It speaks DTCG for token values, Code Connect for
component-to-code mapping, and generated markdown for guidance — the same prose
answer we have. The judgement layer is unsolved industry-wide.

## Non-goals

- Migrating the 7 pattern-level and 10 global rules. They have no code home and
  prose is the correct place for them.
- Adopting DSDS or DESIGN.md as an authoring format.
- Writing lint rules for patterns that have not been counted.

## Implementation status (2026-09-01)

All three units are on main.

- **Unit 1** — documented-contrast check: PR #1285 follow-up work in #1293
  (`a234887c6`). Exceptions now hold only `text-gray-400` and `text-gray-500`.
- **Unit 2** — 21 component rules moved onto their components, ledger rows
  generated: #1293 (`d4ea5a77a`). All 51 rows byte-identical through the move.
- **Unit 3** — DESIGN.md emitted from the theme, component metadata and UI
  standards: #1298 (`940bd201c`). Committed artefact, staleness-checked.

Landed beside the units, same PRs: the full `text-gray-400` migration
(601 → 6, all six WCAG-exempt), the semantic `-text` foreground fix
(46 sites), and nine hand-rolled callouts onto `Alert` (#1297).

Ledger at landing: 53 rules — 10 automated, 4 assisted, 35 manual,
4 deliberately unchecked. It was 3 automated checks and unlabelled prose at
the start of the day.

## Follow-on work, tracked here so it is not re-litigated

This section is the coordination queue. Updated 2026-09-01 end of day; items
completed that same day are recorded with their PR and removed from the open
list.

Done, on main: `Field` + the raw-text-input lock (#1299), authoring-time
integration of the generated contract into the agent instruction surfaces
(#1300), visual snapshot CI over `/dev/ui` plus the semantic-base-foreground
lock (#1302), the axe accessibility audit gating deploys (#1303), and the
audit's colour debt including the new measured `--color-accent-text` token
(#1304). Ledger at close: 56 rules — 14 automated, 4 assisted.

Open:

Closed since the last sync, all on main: #1301 merged; the multi-select
nested-interactive fix (the catalog is axe-clean with zero exceptions); the
cross-surface locks and styleguide corrections; the widget and
shield-extension contrast fixes; the Field rhythm decision (1.5 won); the
connector characterization net (37 tests) and the extraction it protected
(−309 route lines, shared machinery single-sourced).

All three remaining backlog items closed on 2026-09-02, on main: the
hard-to-reach callout states render in the catalog and both suites cover them
(`ebafadb28`); reviewers cite ledger IDs (`16479fab4`); and
`make dev-bootstrap` implements the recorded order with failure classes
distinguished on screen, proven idempotent and fresh on a real database
(`ac1afc581`) — where the real fresh run immediately caught a filename
allowlist that had drifted the day it was written, replaced by matching the
error itself.

Nothing is open. Future work under this spec starts with the rule the
learnings end on: no incident, no check.

## Learnings — working with an implementing agent

The implementation ran as a strict division of labour: one agent (GPT Sol via
Codex CLI) wrote nearly every change from a written brief; the coordinating
agent (Claude) wrote the briefs, verified every claim, and owned what landed.
What that taught us:

**Route by task shape, not task size.** The implementing agent was excellent
at bulk mechanical work against measured targets — six hundred colour swaps,
thirty-seven characterization tests, a whole emitter — and unreliable at
frontend judgement calls: it restyled a decorative avatar palette that was
already correct, renamed a visible label during a "pure rename", and forced
conversions to clear a list. Judgement stays with the reviewer; measured
targets travel in the brief.

**The fabrication catalogue.** Four incidents, none caught by CI, all caught
by independent verification: a lockfile integrity hash written from memory
when the sandbox had no npm network; an existing test rewritten so the
agent's own change would pass; a self-assigned "Confidence: 100" beside an
unverified dimension; and "Assumed: geen" in the same report that assumed the
page background. The most dangerous output is the plausible artefact —
verification means recomputing, rendering and diff-reading, never re-reading
the report.

**Briefs that worked share a shape.** Verbatim failing commands as fixtures
instead of descriptions; the find-method instead of line numbers (numbers
drifted within hours, three separate times); an explicit do-NOT list; a
default for ambiguity ("when unsure, treat it as text"); a demand for the
reverse list — what did NOT change — because that is invisible in a diff; and
"the guards will talk to you, let them" beats enumerating the paperwork.

**Sandbox boundaries are part of the plan.** The implementing agent had no
npm network, no Docker socket, and cwd-scoped writes — so dependency
installs, container runs (baselines, axe) and workflow-file edits were
structurally the coordinator's completion work, not agent failures. And
plumbing must verify its own outputs: one dispatch reported "completed"
while the agent had never run, because a blocked earlier command silently ate
the path variable and the success was an empty echo's.

## Learnings — operational traps found along the way

Recorded because each cost real time once and is invisible until it bites:

- **The local bootstrap is circular.** `migrate` needs an RLS helper function
  that `postdeploy` creates, but `postdeploy` needs tables `migrate` creates.
  Working order: dev-up → apply only the `_rls_current_org_id()` definition →
  migrate → all post_deploy scripts → backend. Four widget post_deploy
  scripts fail on tables this chain does not create.
- **Conductor reassigns ports across resumes.** The port moved mid-session
  and a stale Vite kept serving on the old one — "port already in use" plus a
  working server on the wrong number. Check `CONDUCTOR_PORT` freshly; kill by
  pid from `lsof`, not by pattern. Failed uvicorn starts leave zombies that
  hold the port in CLOSED state.
- **Auth dev mode redirects `/login`**, so the unauthenticated screens can
  never be rendered on the local stack — their conversions rest on class-set
  proofs and layout-shell analysis, stated per commit.
- **Visual baselines are Linux artefacts with a version coupling.** Compares
  on a Mac must run through the same pinned Playwright container that CI
  uses, and Renovate bumps `@playwright/test` — the container tags in the npm
  script and the CI job must follow in lockstep. A mismatch fails loudly, but
  the coupling is worth knowing before the red run surprises someone.
- **Sub-tolerance drift stacks.** Two changes each inside the screenshot
  tolerance can sum past it later; a 4px height change above a section
  rippled a deterministic 1px rounding shift into every section below it at
  the 110% rem base. Baselines re-anchor; verify dimension-by-dimension
  before accepting a batch of "unexpected" updates. One local anomaly (a
  colour mutation invisible to runs reusing a named node_modules volume) was
  never fully explained; CI has no such volume, which bounds it.
- **Parse results, never tail them.** A failing Playwright project was masked
  behind a passing one by `tail -1`; a guard hook blocked a compound command
  and the commit inside it silently never ran. Count expected outcomes
  explicitly, and verify a push landed by comparing SHAs, not by absence of
  error text.
- **Mutation-test the thing the tests claim to cover.** A first sensitivity
  probe mutated an untested path (Notion) and proved nothing; the honest
  probe targets the documented scope (crawler) — and only then does green
  mean anything.
- **The coordinator's own false alarms cost as much as the agent's.** A
  two-dot diff against a stale main "found" foreign files; shell escaping ate
  `$`-paths and made two retained sites look unverifiable; a broken printf
  fixture "proved" a guard didn't fire. Every alarm gets the same treatment
  as every claim: recompute before reporting.

## Where it stopped

The extraction of the duplicated connector machinery (safety-net-first) is
the final unit under this spec. Remaining items — smoke tests for
`features/`, splitting three large route files — are ordinary backlog, not
design-system work, and are parked to avoid the ratchet where every session
adds apparatus and none removes any.
