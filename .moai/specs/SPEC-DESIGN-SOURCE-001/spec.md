# SPEC-DESIGN-SOURCE-001 — The design contract rests on code

**Status:** accepted · **Area:** klai-portal/frontend · **Opened:** 2026-09-01

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
