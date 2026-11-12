# Plan: SPEC-DESIGN-SOURCE-001

Three units, in order. Each is independently shippable and independently
verifiable. Do not bundle them: every time this work was bundled during the
first pass, a defect slipped through that the next verification caught.

## Unit 1 — Check legibility, not only brand conformance

**Why first.** It is the smallest unit, it needs no format decision, and it
closes a live defect. `text-gray-400` is the portal's default secondary colour
at 605 uses and runs at 2.31:1 against our background, where WCAG AA requires
4.5:1. roughly half of those pair it with 12px text. We had a lint rule that catches a
hardcoded hex matching a token and no check that anyone can read the result.

**Do.** Extend `eslint-rules/klai-tokens.js`, which already parses the `@theme`
block, with a contrast function. Add a test asserting every foreground and
background pair documented in the Colors section of `ui-standards.md` meets AA.

**Do not.** Do not sweep them. Count them by usage kind first —
readable prose versus decorative metadata — because roughly a third of the
`gray-400` uses are legitimately decorative and a blanket replacement would be
the fifth uncounted blanket claim in this document's history.

**Ledger.** The check gets a row. KLAI-UI-043 moves from `none` to `automated`
only if the check actually covers it; otherwise its reason is updated to say
what is now checked and what is not.

## Unit 2 — Move the 21 component-level rules onto their components

**Why.** They are the largest block of prose-only rules and the only block with
a natural code home. Moving them makes the diff that changes a component show
the rule that governs it, and makes an orphaned rule impossible: the generator
iterates over files, so no file means no rule.

**Do.** Extend the header doc comment vocabulary that `@purpose` already
established, and extend the parser in
`scripts/generate-component-reference.mjs` (`tagValue`, ~15 lines) to read
them:

```
@guideline <level> <the rule>
@rationale <why it exists>
@avoid <the concrete wrong shape>
```

`<level>` is one of the RFC 2119 values the ledger already uses. Render the
result into the ledger table between the existing generated markers, so the
rows become derived instead of hand-written. `--check` must fail on drift, as
it already does for the component table.

**Verify.** The 21 rows must be byte-identical to their current text after
generation, or the difference must be a deliberate correction stated in the
commit. This is a move, not a rewrite.

**Expect.** `ui-standards.md` gets shorter for the second time. Prose-only
rules drop from 38 to 17.

## Unit 3 — Emit a format, once units 1 and 2 hold

**Why last.** Only after the rules live beside the code is an emitter a render
function rather than a migration.

**Do.** Add a second renderer over the same parsed data that writes
`design.md`: token front matter from the `@theme` block, prose sections from
`@purpose` / `@guideline` / `@rationale`. DESIGN.md before DSDS, because that
is where the adoption is and because Anthropic has an open issue to consume it
in the frontend-design skill.

**Then measure, do not assume.** Point the DSDS MCP server at a generated
document and compare an agent's output against the same task driven by the
ledger. If it does not measurably help, we have lost an evening and gained a
fact. If it does, add a DSDS renderer beside the DESIGN.md one.

## Sequencing constraint

The ledger guard couples the document to the checks: a partial commit that adds
a check without its row, or a row naming a check that does not exist yet, is
red by construction. Each unit therefore lands as one commit including its
ledger mutation. This is a property of the design, not an inconvenience to work
around.
