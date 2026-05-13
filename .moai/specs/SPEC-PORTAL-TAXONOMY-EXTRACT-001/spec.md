---
id: SPEC-PORTAL-TAXONOMY-EXTRACT-001
version: 0.1.1
status: done
created: 2026-05-13
completed: 2026-05-13
author: Mark Vletter
priority: high
parent: SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001 (deferred-fix marker in insights.tsx points here)
related:
  - SPEC-PORTAL-TAXONOMY-SPLIT-001 (sibling — interior split of TaxonomyTab; this SPEC is the prerequisite move)
  - SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001 (origin of the file-organization rule + ESLint guard this SPEC applies)
rule:
  - .claude/rules/klai/projects/portal-frontend.md § "File organization for shared types and helpers"
---

# SPEC-PORTAL-TAXONOMY-EXTRACT-001 — Move TaxonomyTab from taxonomy.tsx (route) to _components/

## Goal

Move the `TaxonomyTab` function (~720 lines, currently inside the
`taxonomy.tsx` route file) verbatim into
`klai-portal/frontend/src/routes/app/knowledge/$kbSlug/_components/TaxonomyTab.tsx`.
Update both consumers (`taxonomy.tsx` and `insights.tsx`) to import
from the new location. Remove the `eslint-disable-next-line` +
TODO comment in `insights.tsx` that was the deferred-fix marker for
exactly this work (left behind by the SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001
followups).

This SPEC is **pure file relocation, zero behavior change**. The
720-line function body moves intact; no internal refactor, no
sub-component extraction, no hook extraction. Those are
SPEC-PORTAL-TAXONOMY-SPLIT-001 territory.

## Motivation

### The deferred marker

`insights.tsx` currently contains:

```ts
// TODO: F-table row 1 of SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001 § Follow-ups
// will extract TaxonomyTab (currently a 720-line god-component inside
// ./taxonomy.tsx) to _components/TaxonomyTab.tsx. Splitting that monolith
// deserves its own SPEC. Until then, this single cross-route import is
// the deferred-fix marker.
// eslint-disable-next-line klai/no-cross-route-import
import { TaxonomyTab } from './taxonomy'
```

That comment is the explicit "this is a known smell, the SPEC for
fixing it is documented" pointer. Eslint-disable-next-line markers age
poorly — every week they sit there, the chance someone copies the
pattern increases ("oh, you can just disable the rule"). Closing this
marker quickly preserves the rule's authority.

### Mechanical signals

`taxonomy.tsx` is the highest-pain god-component in the repo:

| Metric | Value |
|---|---|
| File line count | 1088 |
| TaxonomyTab function body | ~720 lines (66% of file) |
| Inline mutations | 8 |
| Inline queries | 4 |
| useState calls | 16 |
| useCallback / useMemo | 0 |
| Inline JSX in proposals.map() callback | 166 lines |
| Git churn last 90 days | 44 commits (highest in survey) |
| Cross-imported by | insights.tsx (the eslint-disable) |
| Last touched | 11 hours ago at SPEC time |

The file is being actively edited and the cross-route import is
literally an open wound the rule is forced to ignore.

### Why move now, split later

Moving the function body to `_components/TaxonomyTab.tsx` is a
mechanically simple, behavior-preserving relocation. The pattern is
proven: SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001 followups (PR #620)
did the exact same thing for `KBOverviewSections` (extracted from
`overview.tsx` to `_components/KBOverviewSections.tsx`). Same
directory, same convention.

Splitting the 720-line body into `<ProposalCard>`, `<TaxonomyTree>`,
extracted mutation hooks, etc. is a different beast — real refactor
risk on a god-component with 16 useState and 8 inline mutations.
That work is SPEC-PORTAL-TAXONOMY-SPLIT-001, scheduled when concrete
feature pressure picks it up (per scale-the-answer: don't preemptively
split monoliths that aren't in your path).

## Scope

### In

**Frontend** (`klai-portal/frontend/src/routes/app/knowledge/$kbSlug/`):

- New file `_components/TaxonomyTab.tsx`:
  - Contains the verbatim body of the current `TaxonomyTab` function
    (currently inside `taxonomy.tsx`)
  - All currently-inline imports the function needs (lucide icons,
    React Query hooks, `apiFetch`, paraglide messages, type imports
    from `-kb-types`, etc.) move with it
  - JSDoc header explains: "Extracted from taxonomy.tsx route file
    so insights.tsx can consume it without violating
    klai/no-cross-route-import. Internal split into sub-components
    is tracked under SPEC-PORTAL-TAXONOMY-SPLIT-001."

- Modify `taxonomy.tsx`:
  - Reduces to a route-shell (~30 lines): imports `TaxonomyTab` from
    `./_components/TaxonomyTab`, exports `Route` with
    `component: TaxonomyTab` (or wraps it in the existing
    `<RoleGuard>` if the route already does that)
  - All other imports the route used only for TaxonomyTab go away
    (since they moved with the function body)

- Modify `insights.tsx`:
  - Remove the entire `TODO: F-table row 1 ...` comment block
  - Remove the `eslint-disable-next-line klai/no-cross-route-import`
    line
  - Change `from './taxonomy'` to `from './_components/TaxonomyTab'`

### Out (explicit)

- **TaxonomyTab interior split** — sub-component extraction
  (`<ProposalCard>`, `<TaxonomyTree>`, `<TaxonomyToolbar>`),
  mutation hook extraction (`-taxonomy-hooks.ts`), useCallback /
  useMemo introduction. All tracked under
  SPEC-PORTAL-TAXONOMY-SPLIT-001 with characterization-test
  discipline (DDD).
- **Touching the `proposals.map()` 166-line inline JSX**. That's the
  obvious first target for the SPLIT SPEC; this MOVE SPEC leaves it
  exactly as-is.
- **Coverage gaps inside TaxonomyTab**. Whatever tests exist today
  continue to pass; no new tests added in this SPEC.
- **Performance work**. Adding useCallback/useMemo for stable closure
  identity is part of the SPLIT SPEC, not this one.

### Backend changes summary

None. Frontend file move only. No alembic migration, no API change,
no env var, no SPEC-DB linkage.

## Requirements (EARS)

### Functional

- **REQ-1**: When a contributor opens
  `klai-portal/frontend/src/routes/app/knowledge/$kbSlug/taxonomy.tsx`
  after this SPEC, the file shall NOT contain the `TaxonomyTab`
  function body. It shall contain only the `Route` definition (and
  any TanStack Router boilerplate) plus an import of `TaxonomyTab`
  from `./_components/TaxonomyTab`.

- **REQ-2**: When a contributor opens
  `klai-portal/frontend/src/routes/app/knowledge/$kbSlug/insights.tsx`
  after this SPEC, the file shall NOT contain the
  `eslint-disable-next-line klai/no-cross-route-import` directive
  NOR the `TODO: F-table row 1 ...` comment block. The
  `TaxonomyTab` import shall come from
  `'./_components/TaxonomyTab'`.

- **REQ-3**: When the user navigates to the Taxonomie tab on a KB
  detail page, the rendered DOM shall be byte-identical to the
  pre-SPEC behavior (no visual or interactive change).

- **REQ-4**: When the user navigates to the Inzichten tab on a KB
  detail page, the rendered DOM shall be byte-identical to the
  pre-SPEC behavior (TaxonomyTab section renders unchanged inside
  the InsightsTab composition).

### Non-functional

- **REQ-5**: After this SPEC, `tsc --noEmit` on
  `klai-portal/frontend` shall pass with zero errors. `eslint .`
  shall pass with zero errors and zero new warnings.

- **REQ-6**: After this SPEC, `vitest run` shall report the same
  pass count as the pre-SPEC baseline. No assertion changes.

- **REQ-7**: After this SPEC, line counts shall satisfy:
  - `taxonomy.tsx`: ≤ 60 lines (down from 1088)
  - `_components/TaxonomyTab.tsx`: 980-1080 lines (the function body
    + its imports + JSDoc — upper bound generous because we're
    moving the whole thing as-is, not refactoring)
  - `insights.tsx`: ~28-32 lines (down from 36, since the eslint-
    disable comment block goes away)

- **REQ-8**: `git grep -n "eslint-disable-next-line klai/no-cross-route-import" klai-portal/frontend/src` shall return zero hits after this SPEC. The deferred-fix marker is gone.

## Acceptance Criteria

1. New file `klai-portal/frontend/src/routes/app/knowledge/$kbSlug/_components/TaxonomyTab.tsx` exists and contains the moved `TaxonomyTab` function plus required imports.
2. `taxonomy.tsx` reduced to route shell (`wc -l` ≤ 60).
3. `insights.tsx` has no eslint-disable; `git grep "eslint-disable-next-line klai/no-cross-route-import"` returns zero hits.
4. `tsc --noEmit` zero errors. `eslint .` zero errors. `bun run build` green.
5. `vitest run` same pass count as baseline.
6. Playwright smoke (Voys tenant): open Taxonomie tab on Support KB → verify proposals render; open Inzichten tab on same KB → verify TaxonomyTab section renders identically. Screenshots saved.
7. `git diff --stat` shows: 1 file added (`_components/TaxonomyTab.tsx`), 2 files modified (`taxonomy.tsx`, `insights.tsx`). `routeTree.gen.ts` byte-identical (TanStack ignores `_components/`).

## Implementation Plan

Pure-extraction PR with these commits, each independently green:

### Phase 0 — Worktree + baseline

- `git worktree add ../klai-taxonomy-extract -b feat/SPEC-PORTAL-TAXONOMY-EXTRACT-001 origin/main`
- Capture baseline: `wc -l taxonomy.tsx insights.tsx` and pre-SPEC vitest pass count.

### Phase 1 — Move TaxonomyTab to _components/ (single commit)

- Cut the entire `TaxonomyTab` function body (currently lines ~370-1087 of taxonomy.tsx, exact range to be confirmed at execution time) along with its required imports.
- Paste into new file `_components/TaxonomyTab.tsx` with a JSDoc header.
- In `taxonomy.tsx`: remove the function and now-unused imports; add `import { TaxonomyTab } from './_components/TaxonomyTab'`; ensure `Route.component` references the imported function.
- In `insights.tsx`: change import path; remove the eslint-disable comment block.
- Verify: `tsc --noEmit` clean, `eslint .` clean, `vitest run __tests__/` pass count unchanged.

### Phase 2 — QA + ship

- `bun run lint` zero errors.
- `bun run build` green.
- Full vitest suite passes.
- Playwright smoke on Voys tenant: Support KB → Taxonomie tab → screenshot. Same KB → Inzichten tab → screenshot. Compare against pre-SPEC: pixel-identical.
- PR with checklist below.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| TaxonomyTab uses an import that exists only in `taxonomy.tsx` and isn't moved with it | Medium — 720 lines = many imports | Medium | `tsc --noEmit` catches every missing import. Resolve by tracing each undefined identifier and either moving its import or adding it. |
| Closure dependency: function body references something in `taxonomy.tsx`'s module scope (a const or another local function) | Low — code review of the function body should confirm self-contained | Medium | Phase 1 commit message lists every external reference resolved. If a non-import dependency appears, move it too. |
| Route component re-render behavior subtly differs after the move (e.g. memoization boundaries) | Very low — JS function identity preserved by single-import pattern | Low | Playwright verification specifically watches for re-render symptoms (form state loss, scroll position). |
| `routeTree.gen.ts` regenerates with noise (TanStack picks up _components/) | Very low — `_components/` is the standard ignored convention; precedent in same dir | Low | Verify `routeTree.gen.ts` byte-identical via `git diff --stat`. |
| Concurrent edit on taxonomy.tsx during the SPEC merges into a conflict | Medium — 44 commits in 90 days = active file | Medium | Rebase before merge. If conflict in TaxonomyTab body itself: take origin/main version (incoming changes are real product work) and replay our move on top. |

## PR Description Checklist

- [ ] No backend changes. No alembic migration. No API change. No env var.
- [ ] New file `_components/TaxonomyTab.tsx` contains the verbatim `TaxonomyTab` function.
- [ ] `wc -l klai-portal/frontend/src/routes/app/knowledge/\$kbSlug/taxonomy.tsx` ≤ 60.
- [ ] `git grep "eslint-disable-next-line klai/no-cross-route-import" klai-portal/frontend/src` zero hits.
- [ ] `git grep "F-table row 1 of SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001"` zero hits (the TODO marker is gone).
- [ ] `tsc --noEmit` clean. `eslint .` clean. `bun run build` green.
- [ ] `vitest run` same pass count as pre-SPEC baseline.
- [ ] Playwright smoke verified on Voys: Taxonomie tab + Inzichten tab render identically. Screenshots attached.
- [ ] `routeTree.gen.ts` byte-identical (verify via `git diff --stat`).

## Open Questions

1. **Should the JSDoc header on the moved file mention the SPLIT SPEC?**
   Yes — recommend including: "Internal split into sub-components is
   tracked under SPEC-PORTAL-TAXONOMY-SPLIT-001". This makes the
   727-line monolith's deferred work discoverable from the file
   itself.

2. **Other files in `taxonomy.tsx` besides TaxonomyTab?** Need to
   confirm at execution time. If `taxonomy.tsx` exports more than
   just the route + TaxonomyTab, those other exports stay in
   `taxonomy.tsx` (or move per the file-organization rule).

## Learnings to apply (from SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001)

This SPEC follows the patterns proven by the connector-wizard
extract. Re-use, don't re-discover:

- **File-organization rule** (`portal-frontend.md` § "File organization
  for shared types and helpers") already documents the `_components/`
  convention this SPEC applies. No new rule needed.
- **klai/no-cross-route-import ESLint rule** (already shipped in #620)
  catches regressions automatically — the test that the SPEC succeeds
  is partly that the rule passes after the move.
- **`_components/` precedent**: `KBOverviewSections.tsx` was moved
  from `overview.tsx` to `_components/KBOverviewSections.tsx` in #620.
  Identical pattern, identical directory, identical mechanism.
- **Verify gates after EACH commit** (tsc + lint + tests). Bisectable
  history is more valuable than commit-count economy.
- **Symlink node_modules + paraglide** from main checkout into the
  worktree before running gates — saves a full `npm install`.
- **Live verification on Voys** is the AC10 step. Don't rely solely
  on tsc/tests for a UI-visible move.
- **Rebase before merge** if upstream has moved (44 commits in 90
  days = high concurrent edit probability).
- **previous-deploy-failure-blocks-yours** (process-rules.md) — check
  `gh run list --branch main --limit 5` before pushing to make sure a
  prior PR's deploy failure isn't going to block this PR's deploy.
- **Don't bundle this with the SPLIT SPEC** — scale-the-answer: one
  job, one PR.

## See Also

- `.moai/specs/SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001/spec.md` —
  origin of the file-organization rule + the deferred-fix marker
  this SPEC closes.
- `klai-portal/frontend/src/routes/app/knowledge/$kbSlug/_components/KBOverviewSections.tsx` —
  the precedent this SPEC mirrors.
- `klai-portal/frontend/eslint-rules/no-cross-route-import.js` —
  the rule whose `eslint-disable-next-line` this SPEC eliminates.
- `.claude/rules/klai/projects/portal-frontend.md` § "File
  organization for shared types and helpers" — the rule.
