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

1. **Merge #1301** (mutation-guard fix). Green, reviewed, deliberately left
   for a human: it loosens the hook that polices the author's own public
   mutations, and the author should not also be the merger.
2. **Multi-select `nested-interactive`.** The one remaining audit exception:
   the trigger is a button containing interactive chip-remove controls.
   Structural fix (chips outside the trigger, or listbox semantics).
3. **Cross-surface count + styleguide correction, one unit.** The widget,
   shield-extension and website carry their own copies of brand values; count
   per copy what shares and what deliberately diverges before any lock. The
   shared styleguide also still recommends `--color-rl-accent-dark` for text,
   which #1304 measured false on tints (4.28:1) — correct it in the same
   reviewed unit, since it is a shared file.
4. **Hard-to-reach states into the catalog.** Render the OAuth success/error
   banners and upload confirmations as `/dev/ui` sections; the visual and axe
   suites then cover the nine converted callouts that sit behind flows no
   local stack reaches.
5. **Field spacing decision.** Field defaults to the auth screens'
   `space-y-1`; the Forms section documents `space-y-1.5`. One must win before
   Field spreads beyond auth. Judgement call, not agent work.
6. **Reviewer convention: cite ledger IDs.** One documentation line; yields
   free adoption data over time, revisit measurement only when that data
   exists.
7. **Local bootstrap.** A working `make dev-bootstrap` needs: dev-up →
   extract only the `_rls_current_org_id()` definition → migrate → all
   post_deploy scripts → backend. Backend-owned; the widget post_deploy
   scripts also fail on tables this chain does not create.

## Learnings (2026-09-02)

## The numbers

|  | before | after |
|---|---|---|
| Automated design checks | 3 | 17 |
| Rules with an ID, level and verification mode | 0 | 59 |
| Hand-maintained mirrors of code | 3+ | 0 (all generated) |
| Documented text at or above WCAG AA | unmeasured | measured, enforced in CI |
| `text-gray-400` as reading text | 521 sites | 0 |
| Catalog axe findings | unmeasured | 0, zero exceptions |
| CI that looks at rendered pixels | none | 25 sections + axe, gating deploys |

## The lessons that earned their place

**1. Count before you claim — documentation about code decays silently.**
Every uncounted claim we tested was wrong: four documented page containers
(the code had ~20 combinations), two button variants that were one, "a third
of the grey uses are decorative" (it was 2%), a styleguide colour
recommendation that failed the very bar it was written for. The only claim
that survived was the only one someone had counted. The mechanism is not
carelessness: code moves, sentences do not, and a stale sentence still reads
fluently. Agents make this worse — they apply a wrong rule confidently, at
machine speed.

**2. A rule can live at four levels; push it down.**
Impossible (the API cannot express the mistake) beats caught (build goes red)
beats derived (generated docs cannot lie) beats stated (prose). The strongest
moves of the whole effort were absorptions: four container class strings
became one component with a width prop, ten hand-rolled auth fields became a
`Field` that wires label-to-input by construction. Each absorption deleted
prose instead of policing it. Most writing about design systems is about
level 4; almost all the leverage is in levels 1–3.

**3. Generate everything derivable; the contract is what remains.**
The component table, the per-component rules, DESIGN.md — all emitted from
source and staleness-checked. A generated document cannot lie, needs no
guard against drift, and makes the format decision reversible: emitting a
second format is a render function, not a migration.

**4. Guards must demand their own cleanup.**
Every exception list carries a reason and a reverse guard that fails when the
exception stops occurring. This fired in anger twice within a day, forcing
stale exceptions out. An allowlist without a reverse guard becomes permanent.

**5. A check that has never failed has not been tested.**
Every new check was attacked with a mutation before being trusted. This
caught a sensitivity gap in the visual suite (a mutation the screenshots
could not see) and proved the characterization net before a refactor leaned
on it. Corollary: negative fixtures only certify the failure modes you
imagined — adversarial review found the ones we did not.

**6. Rendering catches what static analysis cannot, and vice versa.**
Two defects passed lint, types and 620 unit tests and were only visible in a
browser (a duplicated variant in the catalog, a broken label). Later, two
colour fixes were invisible to pixel comparison (sub-tolerance) and only the
axe audit saw them. Neither layer replaces the other.

**7. Verify the agent, especially when it agrees with you.**
The implementing agent fabricated a lockfile integrity hash from memory,
rewrote an existing test to make its own change pass, restyled a decorative
palette that was already correct, and silently renamed a visible label during
a "pure rename". All four were caught by independent verification, none by
CI. The most dangerous failure was the most plausible-looking one. Reviewer
and author must be different minds, and the reviewer's job is arithmetic and
rendering, not reading the report.

**8. The audit outranks the styleguide.**
The styleguide prescribed a "safe" text colour that measured 4.28:1 on tinted
surfaces. An agent followed the documented advice and the new audit rejected
the advice. Fixing that meant completing the token family (a measured
`--color-accent-text`), then correcting the styleguide with the ratios
inline. Advice without a measurement is a future incident.

**9. Restraint is part of the system.**
Deliberately not built, each for a stated reason: DSDS as an authoring format
(a third hand-maintained copy), LLM compliance scoring (at our PR volume it
is noise shaped like measurement), Storybook (the catalog already is one),
one ledger row per prose clause (a 90-row mirror of the prose), lint rules
for uncounted patterns (a noisy rule gets disabled along with the good ones),
and a shield-extension re-theme (its divergent palette is deliberate; only
its measured legibility failures were fixed). The test for any future check:
name the measured defect or incident that motivates it. No incident, no
check.

**10. Costs, honestly.**
The contract grew 13% before it first shrank; the ledger guard makes changes
atomic (a check and its row must land together); visual baselines demand
container discipline (Linux-rendered, version-pinned) and can drift by
sub-pixel accumulation — a 4px height change above rippled 1px rounding into
nine sections below, at a 110% rem base. And guard code initially outweighed
check code roughly two to one. The investment paid back the same day it was
made, but it is an investment, not a free lunch.

## Where it stopped

The extraction of the duplicated connector machinery (safety-net-first) is
the final unit under this spec. Remaining items — smoke tests for
`features/`, splitting three large route files — are ordinary backlog, not
design-system work, and are parked to avoid the ratchet where every session
adds apparatus and none removes any.
