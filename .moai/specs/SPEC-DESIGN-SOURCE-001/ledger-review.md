# Ledger review against external requirements standards

Date: 2026-09-02. Scope: `klai-portal/frontend/docs/ui-standards.md` (the
Rules Ledger) and its machinery, tested against five findings from external
research (INCOSE GtWR v4, Google Tricorder, arXiv:2602.11988). Method per our
own rules: count first, recommend second, build nothing without an incident.

Sources of the numbers: the recovered ledger-faithfulness audit (45 findings,
full list recovered from the session transcript of 2026-09-01; it survives in
no repo artifact — see the note at the end), commit `aebd1d10a`, and direct
counts against the working tree at `e12e4a520` + this change.

## Finding 1 — granularity: classify the declined audit findings

The audit produced 45 findings over 49 rows: 9 misstatements, 1 wrong level,
30 missing rows, 5 conflations. What actually happened to them:

- **13 addressed in 12 row edits**: the 9 misstatements (findings 4–12), plus
  4 "missing rows" that were folded into existing rows as qualifiers rather
  than added (finding 2 → KLAI-UI-039, findings 3 and 21 → KLAI-UI-017,
  finding 41 → KLAI-UI-018).
- **1 refuted** (finding 13: KLAI-UI-014 keeps `should`).
- **31 declined** — not 30 as commit `aebd1d10a` stated; the commit's
  arithmetic was off by one because it counted a folded finding as declined.

Classification of the 31 declined, per the criterion "does it state a distinct
obligation, or a qualifier of an existing one":

| Class | Count | Findings |
|---|---|---|
| (a) sub-clause of an existing rule, correctly not its own row | 18 | 15, 16, 17, 18, 20, 22, 25, 26, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37 |
| (b) distinct obligation that currently has no ID | 9 | 1, 14, 19, 23, 24, 27, 38, 39, 40 |
| (c) conflation: multiple obligations sharing one ID | 4 | 42, 43, 44, 45 |

The 18 in class (a) are component-anatomy details (pagination internals,
disclosure-row spacing, wizard step clickability) and qualifiers of ledgered
rules — INCOSE R18 says these travel with their rule, and declining them was
right for that reason, not for the row-count reason we gave.

The 9 in class (b) break down further: 5 are component-choice rules with no
owning row (14 Checkbox-vs-Switch, 19 overlay need→component mapping, 23
bordered-icon default, 24 central action maps, 27 list-primitives-vs-table),
3 are document-governance obligations (38 count-before-enforcement, 39
portal-only scope, 40 conflicting-docs-same-change), and 1 is a deployment
boundary already impossible by construction (1: `/dev/ui` is stripped from
production builds by the `import.meta.env.DEV` gate — a ledger row would
restate what Vite enforces at level 1).

**Recommendation:** no rows added now — none of the 9 has an incident behind
it, and 5 of them are exactly the choice-guidance shape that reviewer judgement
covers. If one of them is violated in review, that violation is the incident
that earns it an ID. Cost of acting now: 9 more hand-maintained rows, each a
new drift surface.

**Proposed preamble criterion** (replacing the implicit row-count heuristic;
prose, not a check — to land in a later change, not this one):

> A clause gets its own row when it states an obligation an agent could
> violate while honouring every other row. A qualifier, exception, or
> anatomical detail travels in the row of the rule it conditions — a rule and
> its escape clause are one requirement, not two. Where one row deliberately
> carries several obligations about the same surface, the row must name each
> half explicitly, so no half can silently drop out.

The last sentence is there because rows 017, 039 and the four class-(c)
conflations already work that way, and a criterion the ledger itself violates
would be a new false claim.

## Finding 2 — INCOSE minimum attributes, per row category

Rows: 59 total — 21 generated from `@guideline` component comments, 38
hand-maintained. Mode distribution: 17 automated, 4 assisted, 34 manual, 4
deliberately unchecked.

| INCOSE attribute | Generated (21) | Hand-maintained (38) |
|---|---|---|
| A15 unique identifier | all | all |
| A1 rationale | 4 (the components carrying `@rationale`: button, inline-delete-confirm, inline-edit-row, tabs) | 8 carry a why-clause in the reason cell (020, 024, 026, 029, 031, 039, 042, 050); 30 carry none |
| A7/A8 verification strategy + method | all: mode plus a named check | mode always present; for the 34 `manual` rows the method is the label "Reviewer step", which is a strategy, not a method |
| A28 verification status | live for `automated` rows: the check passing in CI *is* the status | absent: nothing records whether or when a `manual` rule was last verified |

The honest gap is the last cell: 34 manual rules have no recorded verification
status, ever. The equally honest observation is that a hand-maintained status
column would be a mirror of nothing — it would record intentions, drift
immediately, and be exactly the artifact this spec removed elsewhere. The
review-comment ID convention (commit `16479fab4`) is the cheap version of A28:
PR history becomes the verification record for manual rules, per rule, dated,
for free. No columns added.

## Finding 3 — the adoption measurement's blind spot, named

Done, one sentence appended to the citing-IDs paragraph in "What Is Enforced",
its own commit. The limit: adoption data covers ledgered rules only; prose
deliberately left without an ID is unmeasured by construction. No second
measurement built.

## Finding 4 — suppression count for the 17 automated checks

Counted three suppression mechanisms:

- **Inline eslint disables of `klai/` design rules in `src/`: 2.** Both in
  `WidgetChatSurface.tsx`, both `klai/no-raw-text-input`, both with the same
  stated reason (dark-mode variant the owned Input cannot express), and both
  already named in that rule's own ledger row. `git log -S` shows no design
  suppression was ever added and later removed.
- **Test-side exception lists:** documented-contrast carries 2 entries
  (gray-400 decorative/disabled, gray-500 non-text) — these are documented
  usage scopes with reverse guards, not silenced failures. The a11y suite's
  exception list is empty.
- **Every other check (5 of 7 eslint rules, all 9 design tests, both
  generators): zero suppressions.**

By Tricorder's yardstick that is a not-useful rate of effectively zero, with
one rule (`no-raw-text-input`) at 2 documented, ledgered, reverse-guarded
exceptions. Nothing is near probation. **Recommendation: nothing.** The
retirement half of the policy costs nothing to keep in mind and there is
nothing to retire. No dashboard, no counter, no new prose.

## Finding 5 — instruction vs overview in the always-loaded surfaces

Line classification of the three surfaces every portal-UI session loads
(judgement call per block; imperative sentences and do/don't tables counted as
instruction, descriptions of what a file or system *is* counted as overview):

| File | Total | Instruction | Overview-shaped |
|---|---|---|---|
| `design/tokens.md` | 96 | ~88 | ~8 (the intro block describing what loads and why) |
| `design/portal-patterns.md` | 34 | ~26 | ~8 (the two bullets describing what DESIGN.md and the ledger are) |
| `klai-portal-ui` skill | 46 | ~34 | ~12 (the reference-list descriptions) |
| Total | 176 | ~148 | ~28 |

The arXiv result penalizes overview content and exonerates instructions, and
these surfaces are ~84% instruction. The ~28 overview lines are one-line
descriptions attached to imperative pointers ("read X — it is the generated
contract"), which is closer to an instruction's justification than to a
repository overview. **Recommendation: remove nothing.** The candidate saving
is 28 lines against the risk of pointers whose purpose an agent can no longer
judge. Revisit only on a concrete incident (an agent demonstrably misusing or
ignoring a pointer, or context pressure with a measured cost).

## AC-7 — DSDS MCP comparison: feasibility

Feasible in this workspace, with one caveat. The pieces exist: the pilot
`.context/dsds-pilot/klai-button.dsds.json` is still on disk, `dsds-mcp`
resolves on npm, and Claude Code can register a stdio MCP server per-workspace
(`claude mcp add`). Estimated setup: one evening including authoring the
paired task. The caveat that would invalidate a naive run: the pilot covers
one component, while the ledger-in-context arm covers all 59 rules — a fair
comparison needs DSDS documents generated for at least the components the
task touches, which means extending the generator first. Not run as part of
this review.

## Where counting was not possible

- The 45-finding audit list exists in no repo artifact — not in PR #1285
  (that PR predates the audit), not in commit `aebd1d10a` (which summarizes
  it), and the Sol working directory that produced it is gone. It was
  recovered from the session transcript
  (`~/.claude/projects/...14b03738....jsonl`) and the classification above is
  against that recovered text. If the transcript is ever pruned, the
  classification table in this file is the surviving record.
- "Has any check *ever* been suppressed" was answered via `git log -S` on the
  disable-comment string, which finds added-then-removed suppressions only if
  they used that exact comment form. Other silencing shapes (a rule switched
  off in `eslint.config.js` and back on) were checked by reading the config's
  history for the 7 `klai/` rules: none found.
