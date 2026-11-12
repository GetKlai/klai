# Acceptance Criteria: SPEC-DESIGN-SOURCE-001

Given/When/Then scenarios that must pass before each unit is considered done.
Every criterion is checkable by running something, not by reading the diff.

---

## AC-1: A colour pair below AA fails the build

**Given** the Colors section of `ui-standards.md` documents a foreground and
background token pair
**When** either token's value in `index.css` changes so the pair drops below
4.5:1 for normal text
**Then** `npx vitest run tests/design/` fails and names the pair, both hex
values and the computed ratio
**And** the failure is demonstrated with a negative fixture before the unit is
called done — a check that has never failed has not been tested

## AC-2: The contrast check does not silently pass

**Given** the Colors section is renamed, moved, or its table shape changes
**When** the check runs
**Then** it fails loudly rather than passing over an empty set

## AC-3: Component rules are generated, not written

**Given** a `@guideline` in a component's header doc comment
**When** `node scripts/generate-component-reference.mjs --check` runs
**Then** it exits non-zero if the ledger row for that rule differs from what
the generator would produce
**And** editing the ledger row by hand without editing the component fails the
same check

## AC-4: An orphaned rule is impossible

**Given** a component file is deleted
**When** the generator runs
**Then** its rules disappear from the ledger with no human action
**And** `tests/design/rules-ledger.test.ts` still passes, with counts updated

## AC-5: The move preserved meaning

**Given** the 21 component-level rules before migration
**When** they have been moved into component files and regenerated
**Then** each rendered rule is byte-identical to its previous text, or the
commit message states the correction and why it was needed
**And** the count line in "What Is Enforced" matches the table, as the existing
guard already asserts

## AC-6: The emitter is a renderer, not a source

**Given** a generated `design.md`
**When** a `@guideline` changes in a component file
**Then** regenerating produces the change in `design.md` with no hand editing
**And** hand-editing `design.md` is caught, or the file is gitignored as a
build artefact — one of the two, decided explicitly

## AC-7: The format decision is measured, not assumed

**Given** the DSDS MCP server pointed at a generated document
**When** an agent is given the same UI task twice, once with the MCP server and
once with the ledger in context
**Then** the comparison is written down with what was better, what was worse,
and what was indistinguishable
**And** no adoption decision is taken before that comparison exists
