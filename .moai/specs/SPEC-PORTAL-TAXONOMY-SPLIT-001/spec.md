---
id: SPEC-PORTAL-TAXONOMY-SPLIT-001
version: 0.2.0
status: ready
created: 2026-05-13
updated: 2026-05-13
author: Mark Vletter
priority: medium
parent: SPEC-PORTAL-TAXONOMY-EXTRACT-001 (prerequisite — done; TaxonomyTab lives in `_components/TaxonomyTab.tsx`)
related:
  - SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001 (origin of file-organization rule + ESLint guard; established the `-feature-*.{ts,tsx}` + `_components/` pattern this SPEC applies)
  - SPEC-PORTAL-KENNIS-002 (origin of the `-sources-hooks.ts` precedent we mirror)
rule:
  - .claude/rules/klai/projects/portal-frontend.md § "File organization for shared types and helpers"
---

# SPEC-PORTAL-TAXONOMY-SPLIT-001 — Internal split of TaxonomyTab god-component

## Goal

Split the 1093-line `TaxonomyTab.tsx` god-component (located at
`klai-portal/frontend/src/routes/app/knowledge/$kbSlug/_components/TaxonomyTab.tsx`)
into focused sub-components and an extracted hooks file, **with behavior
preservation** (DDD methodology). Zero user-visible change.

### Target end state

- `_components/TaxonomyTab.tsx`: ≤ 500 lines (orchestrator: state, hook
  consumption, sub-component composition, the inline filter-bar and
  suggest-flow banners, the inline add-form, plus `applyAllMutation` +
  `handleApplyAll` which orchestrate other hooks and so live with the
  orchestrator).

  *Note*: the original draft target was ≤ 250 lines. After the four
  extractions the orchestrator settled at ~450 lines because TaxonomyTab
  still composes 5 sections (filter bar, coverage area with admin-only
  inline add-form, proposals area with apply-all CTA, tag-cloud, three
  suggest-flow banners) and owns 8 useState slots, 11 hook calls, the
  applyAll orchestrator function, and the suggest-state sync useEffect.
  Hitting 250 would require extracting further (add-form, suggest
  banner, filter bar) which the SPEC author deferred to a future SPEC.
- `_components/CoverageWidget.tsx` (new, ~280 lines): coverage rendering
  + per-node inline edit/delete + add-form invocation. Same callback
  props as today.
- `_components/ProposalCard.tsx` (new, ~150 lines): one proposal card
  with edit-mode + reject-form + status branches. Singleton "which card
  is editing" lives in TaxonomyTab; per-card edit-state derived from
  the `isEditing` prop.
- `_components/TagCloud.tsx` (new, ~40 lines): pure renderer.
- `-taxonomy-hooks.ts` (new, route-directory level next to
  `taxonomy.tsx`, ~250 lines): 11 individual hook exports — 7 mutations
  + 4 queries. `applyAllMutation` is **not** here (see Beslissingen
  § B5).
- 0 cross-route imports (already enforced by `klai/no-cross-route-import`
  ESLint rule).
- All existing tests pass; characterization tests added in the PRESERVE
  phase verify the 8 mutation paths + 4 proposal-rendering branches.

## Motivation

After SPEC-PORTAL-TAXONOMY-EXTRACT-001 landed, TaxonomyTab lives in
`_components/TaxonomyTab.tsx` but remains 1093 lines internally. That
SPEC's deliberate scope was mechanical relocation only.

The internal split is where the actual code-quality win happens:

| Today (post-EXTRACT) | Post-SPLIT target |
|---|---|
| 1 file, 1093 lines | 5 files, 40-280 lines each |
| 1 god-function with 11 useState (orchestrator) + 4 useState (CoverageWidget) | TaxonomyTab orchestrator + sub-components with focused state |
| 8 mutations declared inline | 7 mutations + 4 queries in `-taxonomy-hooks.ts` as individual exports; `applyAllMutation` stays in orchestrator |
| 4 inline queries | Same hooks file |
| 166-line inline `proposals.map()` JSX | `<ProposalCard>` with own props |

This is a behavior-preservation refactor — not a UX improvement.

## Motivation metrics

| Metric | Value (current) |
|---|---|
| TaxonomyTab.tsx total lines | 1093 |
| Main TaxonomyTab function useState (parent scope) | 11 |
| CoverageWidget useState (inline sub-component) | 4 |
| Inline mutations | 8 (`createNodeMutation`, `deleteNodeMutation`, `renameNodeMutation`, `approveMutation`, `rejectMutation`, `bootstrapMutation`, `backfillMutation`, `applyAllMutation`) |
| Inline queries | 4 (`coverageQuery`, `nodesQuery`, `proposalsQuery`, `topTagsQuery`) |
| Inline JSX (proposals.map callback) | ~150 lines (regels 856-1009) |
| useCallback / useMemo / memo | 0 — and remain 0 after this SPEC (see Beslissingen § B6) |
| Git churn last 90 days | 44 commits — actively edited |
| Production-critical | Yes (Inzichten + Taxonomie tab on Voys) |

## Scope

### In

**Frontend** (`klai-portal/frontend/src/routes/app/knowledge/$kbSlug/`):

1. **New `_components/CoverageWidget.tsx`** — verbatim relocation of the
   `CoverageWidget` function currently defined at regels 47-326 of
   `_components/TaxonomyTab.tsx`. Same prop signature: `coverage`,
   `activeNodeId`, `onNodeClick`, `onSuggest`, `isSuggesting`,
   `isBackfilling`, `canEdit`, `onRename`, `onDelete`. Owns its own
   per-node edit/delete state (`editingNodeId`, `editingName`,
   `editingDescription`, `confirmDeleteId`).

2. **New `_components/ProposalCard.tsx`** — extraction of the 166-line
   `proposals.map()` callback (regels 843-1009). Props:
   - `proposal: TaxonomyProposal`
   - `canEdit: boolean`
   - `isEditing: boolean` (singleton from parent — only one card edits
     at a time)
   - `isRejecting: boolean` (singleton, idem)
   - `approvePending: boolean` (in-flight state from parent's mutation)
   - `rejectPending: boolean` (idem)
   - `onStartEdit(): void`
   - `onSubmitEdit(title: string, description: string): void`
   - `onCancelEdit(): void`
   - `onStartReject(): void`
   - `onSubmitReject(reason: string): void`
   - `onCancelReject(): void`
   - `onApprove(): void`
   - Owns its own per-card edit-buffer state
     (`editingTitle`, `editingDescription`, `rejectReason`),
     initialised when `isEditing` / `isRejecting` flips to true.

3. **New `_components/TagCloud.tsx`** — verbatim relocation of the
   `TagCloud` function (regels 330-369). Same prop signature: `tags`,
   `activeTags`, `onTagClick`. No state.

4. **New `-taxonomy-hooks.ts`** at route-directory level (alongside
   `taxonomy.tsx`, **not** in `_components/`). 11 individual exports:

   **Queries** (4):
   - `useTaxonomyNodes(kbSlug)` — `nodesQuery` body
   - `useTaxonomyProposals(kbSlug)` — `proposalsQuery` body
     (status=all)
   - `useTaxonomyCoverage(kbSlug, { enabled })` — `coverageQuery` body,
     5min staleTime, gated on isAdmin via `enabled`
   - `useTopTags(kbSlug, activeNodeId)` — `topTagsQuery` body

   **Mutations** (7):
   - `useCreateNode(kbSlug, onSuccess)` — POST nodes; invalidates
     `taxonomy-nodes`; `onSuccess` resets parent's add-form state
   - `useRenameNode(kbSlug)` — PATCH nodes; invalidates
     `taxonomy-nodes` + `taxonomy-coverage`
   - `useDeleteNode(kbSlug)` — DELETE nodes; invalidates
     `taxonomy-nodes`
   - `useApproveProposal(kbSlug)` — POST approve; invalidates
     `taxonomy-proposals` + `taxonomy-nodes` + `taxonomy-coverage` on
     success; on error: warn-log + 409-toast / generic toast + invalidate
     `taxonomy-proposals` + `taxonomy-nodes` (resync)
   - `useRejectProposal(kbSlug, onSuccess)` — POST reject; invalidates
     `taxonomy-proposals`; `onSuccess` resets parent's reject-form state
   - `useBootstrapTaxonomy(kbSlug, onStateChange)` — POST bootstrap;
     `onMutate` → `onStateChange('generating')`; `onSuccess` →
     `onStateChange('proposals_ready' | 'idle')` based on
     `data.proposals_submitted`; invalidates `taxonomy-proposals`;
     `onError` → error-log + `onStateChange('idle')`
   - `useBackfillTaxonomy(kbSlug, onStateChange, options)` — POST
     enqueue + poll loop (max 120 polls × 5s); `onMutate` →
     `onStateChange('applying')`; `onSuccess` → `onStateChange('done')`
     + invalidates `taxonomy-nodes` + `taxonomy-proposals` +
     `taxonomy-coverage` + `taxonomy-top-tags`; `onError` →
     `onStateChange((prev) => prev === 'applying' ? (anyPending ? 'proposals_ready' : 'idle') : prev)`
     — must accept `proposalsForFallback` via options to read pending
     count without coupling to a query.

5. **Modify `_components/TaxonomyTab.tsx`** (the orchestrator) — keep:
   - 11 useState declarations (filter, suggestState, add-form,
     singleton editingProposalId, singleton rejectingProposalId)
   - `useQuery` for `kb` + `members` (auth permissions — not taxonomy
     mutations/queries, stay inline)
   - `applyAllMutation = useMutation({ mutationFn: handleApplyAll })`
     and the `handleApplyAll` async function — both stay here because
     `handleApplyAll` orchestrates raw apiFetch loops + queryClient
     invalidations + `backfillMutation.mutate()`. Moving it to a hook
     would require passing other mutations as deps (rommelige
     coupling). The orchestrator is the right home for orchestration.
   - The `useEffect` that syncs `suggestState` with server data —
     orchestrator-level concern.
   - JSX composition: filter bar, suggest-flow banners (lines
     1066-1087), and the three sub-components.
   - Remove inline `CoverageWidget` + `TagCloud` function definitions.
   - Remove the 7 hook-eligible mutation declarations + the 4 query
     declarations. Replace with hook calls.

6. **No changes to `taxonomy.tsx` route file** — it remains the 18-line
   thin wrapper exporting `TaxonomyTab`. The new
   `$kbSlug/-taxonomy-hooks.ts` and the three new
   `$kbSlug/_components/*.tsx` files sit beside it.

### Out (explicit)

- **Adding new functionality.** Behavior preservation only.
- **Refactoring backend taxonomy endpoints.** Frontend-only.
- **Changing query keys, invalidation patterns, or API contracts.**
  The invalidation map (Appendix A) is the behavior-preservation
  contract.
- **`useReducer` consolidation.** Distributed `useState` is the
  established Klai pattern (see sources-tab precedent). Adopting
  `useReducer` here would diverge from precedent without measured
  benefit.
- **`useCallback` / `useMemo` / `memo()` performance pass.** Acceptance
  criterion 7 below requires "no render regression" — not "faster".
  No measured perf problem exists. Adding these would introduce new
  failure modes (stale closures, memo correctness) without observable
  win. Tracked as a future SPEC only if profiling shows a real
  bottleneck.
- **A separate `TaxonomyToolbar` sub-component.** The original v0.1.0
  draft proposed this. After analysis: action buttons (Add root, Re-tag,
  Apply All) live in three distinct semantic contexts (coverage section
  header, proposals section footer). Extracting them into one
  "toolbar" file would separate buttons from the section they act on,
  reducing readability instead of improving it. Skipped.
- **Hook bundle pattern** (`useTaxonomyMutations()` returning all
  mutations as one object). Klai precedent (`-sources-hooks.ts`) uses
  individual exports per action, consumed directly by the component
  that needs them. We follow precedent.
- **`useCurrentUser` / `useAuth` / `kb` / `members` query relocation.**
  These are auth-permission concerns of the orchestrator, not
  taxonomy-state. Stay inline in `TaxonomyTab.tsx`.

### Backend changes summary

None.

## Beslissingen (resolved during analysis)

The v0.1.0 draft listed 5 open questions for an annotation cycle. All
have been resolved against the existing Klai precedent (the
`-sources-*` family in the same directory):

### B1 — `useReducer` vs distributed `useState`?

**Distributed `useState`. No reducer.**

Evidence: `sources.tsx:33` keeps cross-row singleton (`expandedId`) in
the parent. `-sources-row.tsx:42-44` keeps per-row state in the row.
Zero `useReducer` usage in `$kbSlug/`. We mirror this:

| State | Lives in |
|---|---|
| `activeNodeId`, `activeTags` | TaxonomyTab (filter scope, drives 3 queries + 2 visualisations) |
| `suggestState` (5-state machine) | TaxonomyTab (banner + buttons consume) |
| `showAddRoot`, `addParentId`, `newNodeName` | TaxonomyTab (inline add-form is rendered by orchestrator, not CoverageWidget) |
| `editingNodeId`, `editingName`, `editingDescription`, `confirmDeleteId` | CoverageWidget |
| `editingProposalId` (singleton id) | TaxonomyTab |
| `rejectingProposalId` (singleton id) | TaxonomyTab |
| `editingTitle`, `editingDescription`, `rejectReason` (per-card buffers) | ProposalCard (derived from `isEditing` / `isRejecting` props) |

### B2 — Hook bundle vs individual hooks?

**Individual exports.** Each sub-component imports the hooks it actually
uses.

Evidence: `-sources-row-actions.tsx:36-40` imports
`useSourceDelete, useSourceReauth, useSourceSync` directly; no bundle
helper exists.

### B3 — Hooks file location?

**`$kbSlug/-taxonomy-hooks.ts`** (route-directory level), not in
`_components/`.

Evidence: `-sources-hooks.ts` lives at `$kbSlug/` next to `sources.tsx`.
Per the "smallest-shared scope" rule in `portal-frontend.md` §
File organization, the hooks are shared between TaxonomyTab and any
future sub-component that needs to mutate taxonomy — that scope is the
route directory.

### B4 — Cross-invalidation patterns?

**Documented in full in Appendix A.** This is the behavior-preservation
contract; characterization tests in commit 1 verify each invalidation
path.

### B5 — Is `applyAllMutation` a special case?

**Yes — it stays in `TaxonomyTab.tsx`, not in `-taxonomy-hooks.ts`.**

`handleApplyAll` is an orchestrator:
1. Loops over pending proposals; for each, calls
   `apiFetch(/approve?auto_categorise=false)` directly (NOT via
   `useApproveProposal` — that would trigger one toast per failure and
   omit the `auto_categorise=false` flag, both behavior changes).
2. Invalidates `taxonomy-proposals` + `taxonomy-nodes` +
   `taxonomy-coverage`.
3. Calls `backfillMutation.mutate()` — single classification pass over
   the now-complete taxonomy.

Moving this to a hook would require passing `backfillMutation` (or its
internal trigger) as a dependency. That's not a self-contained hook —
it's coupling in disguise. The orchestrator is the right home.

### B6 — Performance optimisations?

**No `useCallback`, `useMemo`, or `memo()` in this SPEC.**

- AC7 requires "no render regression" — there is no measured
  bottleneck.
- These tools only have effect in pairs (memo + useCallback); adopting
  them adds dependency-array correctness and stale-closure surface for
  zero observable win.
- React Compiler (if enabled in this project's toolchain in the future)
  would automate this anyway.

## Approach (DDD methodology — required)

Behavior preservation on production-critical code. Use the DDD
ANALYZE-PRESERVE-IMPROVE cycle with **test-per-extraction** —
characterization tests are added in the same commit as the extraction
they protect, matching the `-sources-hooks.ts` precedent from
SPEC-PORTAL-KENNIS-002 (no standalone preceding test commit; tests
landed with the extracted hooks).

1. **ANALYZE** (complete — see Beslissingen + Appendix A).

2. **PRESERVE + IMPROVE** — each extraction commit lands the
   characterization tests for the unit it extracts:
   - **Commit 1**: Extract `-taxonomy-hooks.ts` (11 hooks per Appendix
     A) + `__tests__/taxonomy-hooks.test.tsx` covering each hook's URL
     + body + invalidation contract. TaxonomyTab consumes hooks; JSX
     unchanged. Lowest-risk merge.
   - **Commit 2**: Extract `_components/TagCloud.tsx` (pure renderer).
     No test added — no state, no logic.
   - **Commit 3**: Extract `_components/CoverageWidget.tsx` +
     `__tests__/CoverageWidget.test.tsx` covering: per-node edit-mode
     singleton, delete-confirm singleton, suggest-button gating
     (admin-only, threshold checks).
   - **Commit 4**: Extract `_components/ProposalCard.tsx` +
     `__tests__/ProposalCard.test.tsx` covering: edit-mode start/cancel
     restore, reject-form start/cancel clear, save-and-approve emits
     title+description, status-branch rendering (pending / approved /
     rejected). Singleton `isEditing` / `isRejecting` enforced by
     parent (separately covered by manual Playwright pass).

Each commit: `tsc --noEmit + eslint + vitest` all green.
Playwright pass on Voys after commit 4 verifies cross-card
singleton behaviour + the full applyAll orchestrator path (which
remains inline in TaxonomyTab and is therefore not unit-tested in
isolation — manual + Playwright is its preservation gate).

Four commits total. No commit 5 (`useReducer`). No commit 6
(`useCallback` / `useMemo` / `memo`). No toolbar.

## Requirements (EARS)

### Functional (behavior-preservation)

- **REQ-1**: When TaxonomyTab is opened on a KB with N proposals, the
  contributor shall see exactly the same set of proposals, in the same
  order, with identical per-proposal affordances (edit / approve /
  reject), badges (status + type), confidence display, and rejection
  reason rendering as the pre-SPEC implementation.

- **REQ-2**: When a contributor approves a proposal, the system shall
  invalidate the same three TanStack Query keys
  (`taxonomy-proposals`, `taxonomy-nodes`, `taxonomy-coverage`) as the
  pre-SPEC implementation, in the same order, and on error trigger the
  same 409-toast vs generic-error-toast branch with the same
  `taxonomyLogger.warn` payload shape.

- **REQ-3**: When a contributor enters edit-mode on a proposal, the
  edit-mode state machine shall behave identically:
  - Only one proposal may be in edit-mode at any time (singleton).
  - "Cancel" restores the displayed title + description to the original
    proposal values.
  - "Submit" calls approve with `title` + `description` overrides.
  - The Reject affordance shall be hidden while edit-mode is active for
    that proposal.

- **REQ-4**: When a contributor enters reject-mode on a proposal, the
  reject-mode state machine shall behave identically (singleton; cancel
  clears reason; submit invalidates `taxonomy-proposals`).

- **REQ-5**: When a contributor clicks "Apply all", `handleApplyAll`
  shall iterate pending proposals with
  `apiFetch(/approve?auto_categorise=false)` (raw, not via
  `useApproveProposal`), then trigger `backfillMutation` — same
  ordering and same single-backfill outcome as the pre-SPEC
  implementation.

- **REQ-6**: When `topTagsQuery` is keyed with `activeNodeId`, the
  cache key shall include `activeNodeId` exactly as today
  (`['taxonomy-top-tags', kbSlug, activeNodeId]`) so tag-cloud
  re-fetches correctly when the node-filter changes.

### Structural

- **REQ-7**: `_components/TaxonomyTab.tsx` shall be ≤ 500 lines after
  this SPEC. (Original target was ≤ 250; revised to ≤ 500 once the
  four-extraction scope was tallied — see Target end state note.)

- **REQ-8**: The 3 new sub-components shall live in
  `klai-portal/frontend/src/routes/app/knowledge/$kbSlug/_components/`
  alongside the existing `KBOverviewSections.tsx` and the modified
  `TaxonomyTab.tsx`.

- **REQ-9**: The new hooks file shall live at
  `klai-portal/frontend/src/routes/app/knowledge/$kbSlug/-taxonomy-hooks.ts`
  (route-directory level), not in `_components/`.

- **REQ-10**: No new file shall introduce a cross-route import; the
  `klai/no-cross-route-import` ESLint rule shall remain green.

### Quality

- **REQ-11**: `tsc --noEmit` shall pass with zero errors after each
  commit.
- **REQ-12**: `eslint` shall pass with zero errors and no new warnings
  after each commit.
- **REQ-13**: `vitest` shall pass with all existing tests green plus
  the new characterization tests, after each commit.

## Acceptance Criteria

1. **AC1**: Characterization test suites land per extraction commit
   (`taxonomy-hooks.test.tsx` in commit 1; `CoverageWidget.test.tsx`
   in commit 3; `ProposalCard.test.tsx` in commit 4). All test files
   green after every subsequent commit. Hook test coverage maps to
   each row of Appendix A.
2. **AC2**: `wc -l _components/TaxonomyTab.tsx` ≤ 500 (revised from
   the original ≤ 250 — see Target end state note for rationale).
3. **AC3**: 3 new `_components/` files exist (`CoverageWidget.tsx`,
   `ProposalCard.tsx`, `TagCloud.tsx`) plus the modified
   `TaxonomyTab.tsx`. The existing `KBOverviewSections.tsx` is
   unchanged. No new `_components/TaxonomyToolbar.tsx` file (explicit
   non-goal — see Beslissingen § B6 / Scope > Out).
4. **AC4**: New file `-taxonomy-hooks.ts` exists at route-directory
   level with 11 named exports per the Scope > In > § 4 list, each
   matching the invalidation contract in Appendix A.
5. **AC5**: `tsc --noEmit + eslint + vitest` all green at HEAD of the
   branch.
6. **AC6**: Playwright on Voys: Taxonomie tab end-to-end flow (open
   tab, view proposals list, enter edit on one proposal and save,
   approve a second, reject a third with reason, click Apply All)
   renders pixel-identical to pre-SPEC and exhibits identical behavior
   (same network calls in the same order, same toast messages, same
   visible state transitions).
7. **AC7**: Console-perf marker added during commit 1 confirms no
   render regression on a KB with 50+ proposals (typical render count
   per proposal per interaction shall be ≤ pre-SPEC count).
8. **AC8**: `klai/no-cross-route-import` ESLint rule remains green —
   no new cross-route imports introduced.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Hook extraction breaks an invalidation chain (e.g. `approveMutation` forgets `taxonomy-coverage` invalidation) | Medium | High — silent UI desync | Appendix A is the spec; characterization tests in commit 1 lock each path; every hook is asserted against the table |
| ProposalCard edit-mode singleton breaks (two cards edit at once) | Low — guarded by `isEditing` prop derived from parent's singleton id | Medium | Test in commit 1 asserts "click edit on card B while card A is editing → card A exits edit-mode" |
| `useApplyAllProposals` accidentally extracted to hooks file, gets coupled to `backfillMutation` indirectly | Low — explicitly scoped out | Medium | Beslissingen § B5 + scope > out lock this. Reviewer checks `applyAllMutation` is still inline in TaxonomyTab. |
| Concurrent edits on TaxonomyTab during the SPEC (44 commits / 90 days) | High — actively-edited file | High | Short-lived branch (target: ≤ 3 days from start to merge), rebase frequently, conflict-resolve in our favor (the refactor is structural). |
| Test-mock for TanStack Query queryClient mishandled across new hook files | Medium | Medium | Use the same `QueryClientProvider` test wrapper pattern as `-sources-row-actions.test.tsx`; characterization tests use real `QueryClient` instances. |

## Learnings to apply (carried over from SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001)

- **File-organization rule** (`portal-frontend.md` § "File organization
  for shared types and helpers") covers the `-`-prefixed sibling vs
  `_components/` split this SPEC uses.
- **`klai/no-cross-route-import` ESLint rule** prevents regression
  automatically.
- **`_components/` precedent**: KBOverviewSections (#620) + TaxonomyTab
  (SPEC-PORTAL-TAXONOMY-EXTRACT-001) are existing inhabitants.
- **DDD methodology required**: behavior-preservation refactor on
  production code.
- **Phase ordering**: hook extraction first (mechanical, low-risk),
  then sub-components (medium-risk). Each phase its own commit; each
  commit green.
- **Live verification on Voys is mandatory** — production-critical UX.
- **scale-the-answer**: don't bundle this with other god-component
  splits. Each is its own SPEC.
- **previous-deploy-failure-blocks-yours**: check main CI before
  pushing.
- **Worktree-for-long-running-changes**: this SPEC is multi-day work,
  worktree mandatory.
- **Triplicate elimination**: before declaring new hooks, grep
  `-kb-helpers.tsx` for existing `useTaxonomy*` hooks to re-use. (None
  exist as of this SPEC.)

## Appendix A — Invalidation map (PRESERVE contract)

This is the canonical behavior-preservation contract for commit 2.
Each new hook in `-taxonomy-hooks.ts` MUST match its row exactly.

| Hook | onMutate | onSuccess invalidates | onError invalidates + side-effects |
|---|---|---|---|
| `useCreateNode(kbSlug, onSuccess)` | — | `taxonomy-nodes`; then call `onSuccess()` to reset add-form state | — |
| `useRenameNode(kbSlug)` | — | `taxonomy-nodes`, `taxonomy-coverage` | — |
| `useDeleteNode(kbSlug)` | — | `taxonomy-nodes` | — |
| `useApproveProposal(kbSlug)` | — | `taxonomy-proposals`, `taxonomy-nodes`, `taxonomy-coverage` | `taxonomyLogger.warn` with `{error, is409}`; `toast.error(...)` with 409-branch vs generic; invalidate `taxonomy-proposals` + `taxonomy-nodes` for resync |
| `useRejectProposal(kbSlug, onSuccess)` | — | `taxonomy-proposals`; then call `onSuccess()` to reset reject-form state | — |
| `useBootstrapTaxonomy(kbSlug, onStateChange)` | `onStateChange('generating')` | `taxonomy-proposals`; if `data.proposals_submitted > 0` → `onStateChange('proposals_ready')` else `onStateChange('idle')` | `taxonomyLogger.error` with `{slug, error}`; `onStateChange('idle')` |
| `useBackfillTaxonomy(kbSlug, onStateChange, opts)` | `onStateChange('applying')` | `taxonomy-nodes`, `taxonomy-proposals`, `taxonomy-coverage`, `taxonomy-top-tags`; `onStateChange('done')` | `taxonomyLogger.error`; `onStateChange((prev) => prev === 'applying' ? (opts.proposalsForFallback().some(p => p.status === 'pending') ? 'proposals_ready' : 'idle') : prev)` |

`applyAllMutation` is **not** in this table — it stays in TaxonomyTab.
Its contract: iterate pending proposals with
`apiFetch(/approve?auto_categorise=false)`; then invalidate
`taxonomy-proposals` + `taxonomy-nodes` + `taxonomy-coverage`; then
call `backfillMutation.mutate()`. Failures inside the loop are
logged via `taxonomyLogger.warn` but the loop continues.

## See Also

- `.moai/specs/SPEC-PORTAL-TAXONOMY-EXTRACT-001/spec.md` — prerequisite
  (done; TaxonomyTab is already in `_components/`).
- `.moai/specs/SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001/spec.md` —
  origin of patterns + ESLint rule.
- `klai-portal/frontend/src/routes/app/knowledge/$kbSlug/-sources-hooks.ts` —
  hook-extraction precedent.
- `klai-portal/frontend/src/routes/app/knowledge/$kbSlug/-sources-row.tsx` —
  sub-component-state pattern precedent.
- `klai-portal/frontend/src/routes/app/knowledge/$kbSlug/_components/KBOverviewSections.tsx` —
  precedent for `_components/` extraction.
- `.claude/rules/klai/projects/portal-frontend.md` § "File organization
  for shared types and helpers" — the rule.
