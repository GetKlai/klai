---
id: SPEC-PORTAL-TAXONOMY-SPLIT-001
version: 0.1.0
status: draft
created: 2026-05-13
author: Mark Vletter
priority: medium
parent: SPEC-PORTAL-TAXONOMY-EXTRACT-001 (prerequisite — TaxonomyTab must already live in _components/)
related:
  - SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001 (origin of file-organization rule + ESLint guard)
rule:
  - .claude/rules/klai/projects/portal-frontend.md § "File organization for shared types and helpers"
---

# SPEC-PORTAL-TAXONOMY-SPLIT-001 — Internal split of TaxonomyTab god-component

## Goal

Split the 720-line `TaxonomyTab` god-component (extracted to
`_components/TaxonomyTab.tsx` by the prerequisite SPEC-PORTAL-TAXONOMY-EXTRACT-001)
into focused sub-components and extracted hooks, **with behavior
preservation** (DDD methodology). Target end state:

- `TaxonomyTab.tsx`: ≤ 250 lines (state composition + sub-component
  orchestration only)
- `_components/ProposalCard.tsx`: ~150-200 lines (currently the
  166-line inline `proposals.map()` callback)
- `_components/TaxonomyTree.tsx`: ~100-150 lines (the tree rendering
  + node selection)
- `_components/TaxonomyToolbar.tsx`: ~50-80 lines (action bar)
- `-taxonomy-hooks.ts`: ~150-250 lines (8 mutation hooks)
- 0 cross-route imports (already enforced by ESLint rule)
- All existing tests pass (235/235); coverage equal-or-better

This SPEC is **draft** — needs annotation cycle before pickup. The
720-line function has 16 useState + 8 inline mutations + 4 inline
queries; splitting it requires careful behavior-preservation
(characterization tests, mutation prop drilling vs context, etc.).

## Motivation

After SPEC-PORTAL-TAXONOMY-EXTRACT-001 lands, TaxonomyTab will live in
`_components/TaxonomyTab.tsx` but still be 720 lines internally.
That's the move SPEC's deliberate scope (mechanical relocation only).

The internal split is where the actual code-quality win happens:

| Today (post-EXTRACT) | Post-SPLIT target |
|---|---|
| 1 file, 720 lines | 5 files, 150-250 lines each |
| 1 god-function holding 16 useState | TaxonomyTab orchestrator + sub-components with focused state |
| 8 mutations declared inline in function body | 8 hooks in `-taxonomy-hooks.ts`, consumed by sub-components |
| 4 inline queries | Same hooks pattern |
| 166-line inline `proposals.map()` JSX | `<ProposalCard>` component with own props |
| 0 useCallback/useMemo (every render rebuilds every closure) | useCallback on stable callbacks, useMemo on derived data |

This is a real refactor — not a relocation. Behavior preservation is
non-trivial because the 16 useState forms an implicit state machine
that needs to be either:
- Distributed correctly across sub-components (each owning its own
  state)
- OR consolidated into a `useReducer` (probably the right answer
  given the cross-coupling)
- OR kept centrally with prop-drilling (works but verbose)

## Motivation metrics

| Metric | Value |
|---|---|
| TaxonomyTab function lines | ~720 |
| useState | 16 |
| Inline mutations | 8 (`createNodeMutation`, `deleteNodeMutation`, `renameNodeMutation`, `approveMutation`, `rejectMutation`, `bootstrapMutation`, `backfillMutation`, `applyAllMutation`) |
| Inline queries | 4 (`coverageQuery`, `nodesQuery`, `proposalsQuery`, `topTagsQuery`) |
| Inline JSX (proposals.map callback) | 166 lines |
| useCallback / useMemo | 0 |
| Git churn last 90 days | 44 commits |
| Production-critical | Yes (Inzichten + Taxonomie tab) |

## Scope (initial draft — needs annotation cycle)

### In (proposed)

**Frontend** (`klai-portal/frontend/src/routes/app/knowledge/$kbSlug/`):

- New `_components/ProposalCard.tsx`:
  - Extract the 166-line `proposals.map()` callback into a focused
    component
  - Props: `proposal` + per-proposal mutation callbacks
  - Owns: editing state for that specific proposal
    (`editingProposalTitle`, `editingProposalDescription`,
    `rejectingProposalId`, `rejectReason`)

- New `_components/TaxonomyTree.tsx`:
  - Extract tree rendering + node-selection logic
  - Props: `nodes`, `activeNodeId`, `onNodeClick`, `onNodeAdd`, etc.
  - Owns: `addParentId`, `newNodeName`, `showAddRoot`,
    `isAddingChild`

- New `_components/TaxonomyToolbar.tsx`:
  - Extract action-bar (suggest, bootstrap, backfill, apply-all)
  - Props: state of in-flight mutations + handlers

- New `-taxonomy-hooks.ts`:
  - 8 mutation hooks: `useCreateNode`, `useDeleteNode`, `useRenameNode`,
    `useApproveProposal`, `useRejectProposal`, `useBootstrapTaxonomy`,
    `useBackfillTaxonomy`, `useApplyAllProposals`
  - 4 query hooks: `useTaxonomyNodes`, `useTaxonomyProposals`,
    `useTaxonomyCoverage`, `useTopTags`
  - Each hook contains the same `mutationFn` / `queryFn` body that
    currently lives inline in TaxonomyTab. Pure relocation per hook.

- Modify `_components/TaxonomyTab.tsx` (the orchestrator):
  - State: 16 → maybe 4-6 (most state moves into sub-components)
  - Body: ≤ 250 lines (orchestration + composition only)
  - Optionally introduce `useReducer` if the remaining cross-coupled
    state benefits from it (annotation cycle decides)

### Out (explicit)

- **Adding new functionality**. This SPEC preserves behavior; it does
  not improve UX, add features, or change APIs.
- **Refactoring backend taxonomy endpoints**. They stay as-is.
- **Changing query patterns** (e.g. moving from React Query to
  another lib). Out of scope.
- **Adding new tests beyond characterization tests for the existing
  behavior**. Coverage delta should be neutral-or-positive but is
  not the goal.

### Backend changes summary

None.

## Approach (DDD methodology — required)

This is a behavior-preservation refactor on production-critical code.
Use the DDD ANALYZE-PRESERVE-IMPROVE cycle:

1. **ANALYZE**: Read every line of TaxonomyTab. Document the implicit
   state machine (which useState combinations are valid, which are
   invariants). Map data flow: which mutations invalidate which
   queries, which proposals.map() branches render under which
   conditions.

2. **PRESERVE**: Add characterization tests BEFORE any refactor:
   - Each mutation's success / failure path
   - Each rendering branch (proposal types: rename, create-child,
     update-prompt, approve, reject)
   - Edit-mode state transitions
   - Tree expand/collapse + active node tracking

3. **IMPROVE**: Refactor in small commits, each green:
   - Commit 1: Extract `-taxonomy-hooks.ts` (queries + mutations).
     TaxonomyTab consumes the hooks but JSX unchanged.
   - Commit 2: Extract `<ProposalCard>` from the 166-line
     `proposals.map()` callback.
   - Commit 3: Extract `<TaxonomyTree>`.
   - Commit 4: Extract `<TaxonomyToolbar>`.
   - Commit 5 (optional): Introduce `useReducer` if remaining state
     is cross-coupled enough to warrant it.
   - Commit 6: Add useCallback to handlers passed to memo-able
     children. Add useMemo to derived data (filtered proposals,
     active node lookup).

Each commit: tsc + eslint + vitest + characterization tests green.

## Requirements (EARS) — placeholder, expand in annotation cycle

- **REQ-1**: When TaxonomyTab is opened on a KB with N proposals, the
  user shall see exactly the same set of proposals in the same order
  with the same per-proposal affordances (edit / approve / reject)
  as pre-SPEC.
- **REQ-2**: When a contributor approves a proposal, the system shall
  invalidate the same query keys and trigger the same UI transitions
  as pre-SPEC.
- **REQ-3**: When a contributor edits a proposal's title and saves, the
  edit-mode state shall behave identically (cancel restores original;
  enter saves; loading disables).
- **REQ-4**: TaxonomyTab.tsx shall be ≤ 250 lines after this SPEC.
- **REQ-5**: All sub-components shall be in the `_components/`
  directory adjacent to TaxonomyTab.tsx.
- **REQ-6**: All mutation/query hooks shall be in
  `-taxonomy-hooks.ts` adjacent to taxonomy.tsx (the route file).

(More requirements added during annotation cycle.)

## Acceptance Criteria — placeholder

1. Characterization test suite added in Phase ANALYZE/PRESERVE
   (pre-refactor) all green at refactor end.
2. `wc -l TaxonomyTab.tsx` ≤ 250.
3. 4 new files in `_components/` (`ProposalCard.tsx`,
   `TaxonomyTree.tsx`, `TaxonomyToolbar.tsx`, plus the existing
   `KBOverviewSections.tsx` from prior work).
4. New file `-taxonomy-hooks.ts` containing 8 mutation hooks + 4 query
   hooks, each consumed by either TaxonomyTab or a sub-component.
5. tsc + eslint + vitest all green; full vitest pass count maintained
   or grown.
6. Playwright on Voys: Taxonomie tab end-to-end flow (view proposals,
   edit one, approve another, reject a third with reason) renders
   pixel-identical and exhibits identical behavior pre-vs-post.
7. Performance check: console-perf marker added shows no render
   regression on a KB with 50+ proposals.

## Risks (high level — annotation cycle expands)

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| useState distribution introduces a hidden bug (e.g. state lives in two places, gets out-of-sync) | High — 16 state slots interact in non-obvious ways | High | DDD characterization tests. If a test fails post-refactor, revert and rethink that piece of state. |
| Mutation prop-drilling becomes verbose / ugly | Medium | Low | Extract `useTaxonomyMutations` hook that returns a stable object containing all 8, pass as one prop. |
| Performance regression from sub-component re-renders | Low — React 19 + memo + useCallback should handle | Medium | Memo'd sub-components + useCallback on handlers. Phase 6 explicitly. |
| Coverage decrease (sub-components are harder to test in isolation) | Medium | Medium | Per-sub-component tests added in PRESERVE phase. Coverage report compared pre/post. |
| Concurrent edits on TaxonomyTab during the SPEC (44 commits / 90 days) | High — actively-edited file | High | Land EXTRACT SPEC first (small target, easy rebase). For SPLIT SPEC: short-lived branch (≤1 week from start to merge), rebase frequently, conflict-resolve in our favor (the refactor is structural). |
| Notion / Notion-like proposal types behave subtly differently after split | Low — proposals are taxonomy-internal | Low | Characterization tests cover all proposal types. |

## Open Questions

1. **`useReducer` or distributed `useState`?** Annotation cycle
   decides. Lean toward `useReducer` for the cross-coupled subset
   (active node, active tags, editing-state) and `useState` for
   independent flags.

2. **Hook bundle vs individual hook calls?** Pass `useTaxonomyMutations()`
   returning `{ approve, reject, create, ... }` (one call site, stable
   object) vs eight individual calls. Lean toward bundle.

3. **Should `-taxonomy-hooks.ts` co-locate with the route file
   (`$kbSlug/-taxonomy-hooks.ts`) or with the components
   (`$kbSlug/_components/-taxonomy-hooks.ts`)?** Per file-organization
   rule, smallest-shared scope = `$kbSlug/` (used by TaxonomyTab and
   any future sub-component). Place at route-directory level.

4. **Do any of the 8 mutations have non-obvious cross-invalidation
   patterns?** ANALYZE phase must document. E.g., does
   `approveMutation` invalidate `taxonomyNodes` AND `coverage` AND
   `proposals`? The hook extraction must preserve every invalidation.

5. **Is `applyAllMutation` a special case?** It iterates over
   proposals and approves each. Might warrant its own helper rather
   than being a single mutation.

## Learnings to apply (from SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001)

This SPEC operates in the same area as the connector-wizard extract.
Patterns proven there apply directly:

- **File-organization rule** (`portal-frontend.md` § "File organization
  for shared types and helpers") covers the `-`-prefixed sibling vs
  `_components/` split this SPEC uses.
- **klai/no-cross-route-import ESLint rule** prevents regression
  automatically.
- **`_components/` precedent**: KBOverviewSections (from #620) +
  TaxonomyTab (from SPEC-PORTAL-TAXONOMY-EXTRACT-001) are existing
  inhabitants.
- **DDD methodology required**: prior SPECs used DDD (ANALYZE-PRESERVE-IMPROVE)
  for similar behavior-preservation refactors. Required here.
- **Phase ordering**: hook extraction first (mechanical, low-risk),
  then sub-components (medium-risk), then state-machine consolidation
  (highest risk). Each phase its own commit, each commit green.
- **Live verification on Voys** is mandatory — this is production-
  critical UX.
- **scale-the-answer**: don't bundle this with other god-component
  splits. Each is its own SPEC.
- **previous-deploy-failure-blocks-yours**: check main CI before
  pushing.
- **Worktree-for-long-running-changes**: this SPEC is multi-day work,
  worktree mandatory.
- **Triplicate elimination**: before extracting hooks, grep for
  existing canonical `useTaxonomyXxx` hooks in `-kb-helpers.tsx` or
  similar — re-use, don't duplicate.

## See Also

- `.moai/specs/SPEC-PORTAL-TAXONOMY-EXTRACT-001/spec.md` —
  prerequisite (TaxonomyTab must already be in `_components/`).
- `.moai/specs/SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001/spec.md` —
  origin of patterns + ESLint rule.
- `klai-portal/frontend/src/routes/app/knowledge/$kbSlug/_components/KBOverviewSections.tsx` —
  precedent for `_components/` extraction.
- `.claude/rules/klai/projects/portal-frontend.md` § "File
  organization for shared types and helpers" — the rule.
- `.claude/rules/klai/workflow/process-full.md` — DDD methodology
  reference (if exists; otherwise see `workflow-modes.md`).
