---
id: SPEC-PORTAL-UI-CONSISTENCY-001
version: 0.1.0
status: draft
created: 2026-05-12
author: Jantine Doornbos
priority: high
related:
  - SPEC-PORTAL-REDESIGN-002 (defines the v1-spine — REFERENCED)
  - SPEC-PORTAL-ADMIN-UI-001 (admin pages, scoped — STILL OPEN for users/profiles/groups)
  - .claude/rules/klai/design/portal-patterns.md (canonical styleguide — REFERENCED, not duplicated)
---

# SPEC-PORTAL-UI-CONSISTENCY-001: Portal UI conformance pass

## HISTORY

| Date | Version | Change |
|------|---------|--------|
| 2026-05-12 | 0.1.0 | Initial draft after `/admin` UI inspection showed every page rendering with a different container width and block style. Card-grid still present on `admin/index.tsx`, `app/index.tsx`, `admin/billing.lazy.tsx`. Sparring resolved with Jantine: this is an enforcement SPEC, not a redesign — referenced patterns already exist in `portal-patterns.md` and `SPEC-PORTAL-REDESIGN-002`. |

## Problem

The portal v1-spine is documented (`portal-patterns.md`, 487 lines, in force since SPEC-PORTAL-REDESIGN-002 landed) and explicitly required by SPEC-PORTAL-ADMIN-UI-001 REQ-12. Today, pages still ship with seven different `max-w-*` values, two heading patterns, and three block styles (card-grid, table, divider-list). The drift makes every navigation feel like a different product.

This SPEC is the **conformance pass**: every portal-app page MUST match the v1-spine inventory below. No new tokens are introduced. No styleguide is rewritten. Pages that already comply are not touched. Pages that drift are realigned to the canonical pattern in one PR per page.

### Reference target

`klai-portal/frontend/src/routes/app/knowledge/index.tsx` is the **canonical layout**. Any disagreement between this SPEC and that file is resolved in favour of that file.

## Scope

### In scope

All `klai-portal/frontend/src/routes/app/**` and `klai-portal/frontend/src/routes/admin/**` index/overview pages plus their primary detail pages, EXCEPT the chat-home route (which is intentionally full-bleed per portal-patterns.md).

### Out of scope

- Chat (`/app` route, full-bleed by design)
- Hero copy, positioning text, or any user-facing language changes (a/b copy is a separate concern)
- Adding amber accent (deferred to `SPEC-PORTAL-POLISH-001` — Polish-1 seam)
- Brand DNA changes (would belong in `styleguide.md`, not here)
- New components (this SPEC re-uses what exists; if a missing primitive blocks a page, file a follow-up SPEC)
- LibreChat tenant containers (separate codebase)

## Requirements (EARS)

### Page shell

- **REQ-1 (Container)**: WHEN any in-scope page renders, the SYSTEM SHALL wrap content in exactly one of two containers:
  - List / overview: `mx-auto max-w-3xl px-6 py-10`
  - Form / edit: `mx-auto max-w-lg px-6 py-10`
  No other `max-w-*` value is permitted. No `p-6` without `mx-auto`.

- **REQ-2 (Vertical rhythm)**: WHEN a list/overview page renders, the SYSTEM SHALL use `space-y-8` between top-level sections (header → list → footer). Form pages use `space-y-4` inside `<form>` and `space-y-6` between form blocks.

- **REQ-3 (Page header)**: WHEN any non-form page renders, the SYSTEM SHALL render the heading using the exact pattern:
  ```tsx
  <div className="flex items-center justify-between mb-2">
    <h1 className="page-title text-[26px] font-display-bold text-gray-900">{title}</h1>
    {actions && <div className="flex items-center gap-3">{actions}</div>}
  </div>
  ```
  Back-button (when applicable) sits to the LEFT of the h1 OUTSIDE this row, per existing `BackLink` pattern. Subtitle (when applicable) sits BELOW this row in a `<p className="text-sm text-gray-400">`.

### Block style

- **REQ-4 (No card-grids for index pages)**: WHEN an admin or app index page lists items (admin landing, sub-pages of admin, app-index dashboard), the SYSTEM SHALL render rows using the Collection List pattern (`portal-patterns.md` § Collection List):
  ```tsx
  <div className="divide-y divide-gray-200 border-t border-b border-gray-200">
    {items.map(item => <Row key={item.id} {...item} />)}
  </div>
  ```
  Cards (`grid-cols-N` with rounded rectangles) are PROHIBITED for index/landing layouts. Cards remain permitted ONLY where they represent an explicit picker/selector primitive (e.g. `SourceTypeGrid`, `ProfilePicker`, new-knowledge-base type selector) — these are decisions, not lists.

- **REQ-5 (Row contract)**: WHEN a row renders inside the Collection List, the SYSTEM SHALL include the following slots in this exact left-to-right order:
  1. Optional 32px icon column (`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gray-50 text-gray-400`)
  2. Title + subtitle stack (title = 15px font-display medium, subtitle = 13px text-gray-400)
  3. Optional metadata column (right-aligned, `text-xs text-gray-400`)
  4. Optional trailing action(s) (icon-buttons or chevron, right-most)

- **REQ-6 (No mixed styles per page)**: A single in-scope page MUST NOT mix card-grids and rows for the same content type. If a page has both a "primary section" (rows) and a "secondary section" (e.g. quick-actions), each section uses one consistent style.

### Colour / typography

- **REQ-7 (Token compliance)**: WHEN a row renders borders, hover, or active state, the SYSTEM SHALL use the v1-spine literals: `border-gray-200`, `bg-black/5` (hover), `bg-black/[0.06]` (active). No new colour tokens. No amber outside the focus-ring + logo reserve.

- **REQ-8 (Sentence case)**: NO page in scope MAY use `uppercase`, `tracking-wider`, or `tracking-[0.04em]`. All headings, labels, tab text, badges, meta-text: sentence-case.

- **REQ-9 (Heading typography)**: WHEN a page renders its h1, the SYSTEM SHALL use class `page-title text-[26px] font-display-bold text-gray-900 leading-none`. Sub-headings (h2) use `text-sm font-medium text-gray-900`. No `font-display*` on body prose.

### Empty / loading

- **REQ-10 (Empty state)**: WHEN a page has zero items, the SYSTEM SHALL render the canonical empty-state pattern from `portal-patterns.md` § Empty States: centered text, no boxed card, no oversized illustration.

- **REQ-11 (Loading state)**: WHEN a page is loading, the SYSTEM SHALL render the `Spinner` primitive or row-skeleton from `portal-patterns.md` § Loading States — not custom spinners.

## Per-page audit (drift status — informational)

The drift map below is the starting point. As each page lands its conformance PR, status flips to ✓.

| Page | Current container | Block style | Status |
|---|---|---|---|
| `app/index.tsx` (overview) | `max-w-3xl py-10 space-y-8` ✓ | **card-grid** ❌ | PENDING — REQ-4 |
| `app/knowledge/index.tsx` | `max-w-3xl pb-10` (reference) | rows ✓ | ✓ REFERENCE |
| `app/templates/index.tsx` | `max-w-3xl py-10` ✓ | ? | AUDIT NEEDED |
| `app/account.tsx` | `max-w-2xl py-10 space-y-6` ❌ | form | PENDING — REQ-1 (→ `max-w-lg`) |
| `app/integrations.tsx` | `max-w-2xl py-10 space-y-6` ❌ | ? | PENDING — REQ-1 |
| `app/gaps/index.tsx` | `max-w-4xl py-10` ❌ | ? | PENDING — REQ-1 |
| `app/transcribe/index.tsx` | `max-w-5xl py-10 space-y-6` ❌ | ? | PENDING — REQ-1 |
| `app/scribe.tsx` | `max-w-3xl py-10 space-y-6` ✓ | ? | AUDIT NEEDED |
| `admin/index.tsx` (overview) | `max-w-3xl py-10 space-y-8` ✓ | **card-grid** ❌ | PENDING — REQ-4 (the screenshot) |
| `admin/billing.tsx` | **(no container)** ❌ | ? | PENDING — REQ-1 |
| `admin/billing.lazy.tsx` | ? | **card-grid** ❌ | PENDING — REQ-4 |
| `admin/settings.tsx` | `max-w-3xl py-10 space-y-6` ✓ | ? | AUDIT NEEDED |
| `admin/danger-zone.tsx` | `max-w-lg py-10 space-y-6` ✓ (form) | form | ✓ |
| `admin/users/index.tsx` | `max-w-6xl` ❌ | table | PENDING — REQ-1 |
| `admin/users/invite.tsx` | ? | **card-grid** ❌ | PENDING — REQ-4 (see SPEC-PORTAL-ADMIN-UI-001 dec #4: revert radio-cards back to dropdown) |
| `admin/users/$userId/edit.tsx` | ? | **card-grid** ❌ | PENDING — REQ-4 (see SPEC-PORTAL-ADMIN-UI-001 dec #7) |
| `admin/profiles/index.tsx` | `max-w-4xl` ❌ | ? | PENDING — REQ-1 |
| `admin/groups/index.tsx` | `max-w-4xl` ❌ | ? | PENDING — REQ-1 |
| `admin/api-keys/index.tsx` | `max-w-5xl` ❌ | ? | PENDING — REQ-1 |
| `admin/widgets/index.tsx` | `max-w-5xl` ❌ | ? | PENDING — REQ-1 |
| `admin/mcps/index.tsx` | `max-w-6xl` ❌ | ? | PENDING — REQ-1 |
| `admin/templates/index.tsx` | `max-w-3xl py-10` ✓ | ? | AUDIT NEEDED |

## Acceptance criteria

1. Every page in the audit table flips to ✓ via either a no-change confirmation (already compliant) or a conformance PR.
2. `grep -rn 'max-w-\(2xl\|4xl\|5xl\|6xl\)' klai-portal/frontend/src/routes/{app,admin}` returns ZERO results (except in components that legitimately need full-width like data-tables — those use no `max-w-*` and rely on the parent container).
3. `grep -rn 'uppercase\|tracking-wider\|tracking-\[0.04em\]' klai-portal/frontend/src/routes/{app,admin}` returns ZERO results.
4. `grep -rn 'grid grid-cols-[23]' klai-portal/frontend/src/routes/{app,admin}/{index.tsx,*/index.tsx}` returns ZERO results except in explicit picker primitives (allowlisted in this SPEC: `SourceTypeGrid.tsx`, `ProfilePicker.tsx`, `app/knowledge/new.tsx` type selector).
5. Playwright smoke test (`tests/e2e/portal-consistency.spec.ts` — new) loads each in-scope page, asserts:
   - The first `<div>` after the `<main>` element matches one of the two canonical containers
   - There is exactly one `h1.page-title` on the page
   - No element has `class*="uppercase"`
   This test is added with the SPEC and runs in CI per the existing portal-frontend test workflow.

## Implementation plan

Per-page conformance is **independent work**: each page's edit set touches one file (the page) plus 0–1 component (a new row primitive if one doesn't exist). This is the parallel-safe shape.

### Phase 1 — Row primitive (sequential, blocks Phase 2)

Extract the row pattern used in `app/knowledge/index.tsx` into a shared `<ListRow>` (or `<CollectionRow>`) primitive at `klai-portal/frontend/src/components/ui/list-row.tsx`. Props: `icon?`, `title`, `subtitle?`, `meta?`, `actions?`, `href?` (renders as link). No styling exposed — all per REQ-5.

### Phase 2 — Parallel per-page conformance

Spawn one worktree-isolated implementer agent per pending page. Each agent:

1. Reads `portal-patterns.md` § Page Layout + § Collection List + § Page header
2. Reads `SPEC-PORTAL-UI-CONSISTENCY-001/spec.md` (this file)
3. Reads the target page file
4. Applies REQ-1 through REQ-11 with surgical edits — no logic changes
5. Opens a PR titled `chore(portal-ui): SPEC-PORTAL-UI-CONSISTENCY-001 — <page-name>`
6. Each PR is independent and reviewable separately

Parallelisation is safe because pages do not share files. The new `<ListRow>` from Phase 1 is the only shared dependency and lands first.

### Phase 3 — CI guard

Add `scripts/check-portal-ui-conformance.sh` to CI: runs the three grep assertions from Acceptance Criteria #2–4. PRs that re-introduce drift fail the check.

## Open questions

- **Q1**: `admin/users/index.tsx` currently uses `max-w-6xl` because the user list has 6+ columns. Does compressing to `max-w-3xl` reduce columns or wrap them? Decision needed before that page's PR. Default assumption: keep `max-w-3xl` and let the table scroll horizontally on narrow screens.
- **Q2**: `app/transcribe/index.tsx` uses `max-w-5xl` likely for a wide transcript grid. Same question. Default assumption: keep `max-w-3xl`, transcript detail view stays wide.
- **Q3**: `admin/billing.tsx` has NO container at all. Was that intentional (e.g. delegates to Stripe portal embed)? Audit before fixing — may be a redirect-only file.

These are blockers only for their respective PRs. Other PRs can proceed.

## Non-goals (explicit)

- This SPEC does NOT change branding (amber stays reserved).
- This SPEC does NOT change copy.
- This SPEC does NOT touch components outside the in-scope routes.
- This SPEC does NOT introduce new design tokens.
- This SPEC does NOT block on `/admin/users` and `/admin/profiles` work from SPEC-PORTAL-ADMIN-UI-001 — those PRs apply their own decisions plus REQ-1 through REQ-11.

## Definition of done

- All audit-table entries are ✓
- CI conformance script is green
- Playwright consistency smoke test is green
- `portal-patterns.md` last-updated date bumped (no content change unless drift surfaced a gap; if it did, fix the styleguide and reference it from this SPEC's HISTORY)
