# SPEC-PORTAL-TAXONOMY-SPLIT-001 — Progress

## Status: done (2026-05-13)

## Implementation

The 1093-line `TaxonomyTab.tsx` god-component was split into focused
sub-components and extracted hooks per the SPEC plan. Pre-requisite
was met: TaxonomyTab lived in `_components/` before SPLIT started
(SPEC-PORTAL-TAXONOMY-EXTRACT-001 + PR #625).

### PR sequence

| PR | Title | Highlights |
|---|---|---|
| #646 | refactor(portal-frontend): SPEC-PORTAL-TAXONOMY-SPLIT-001 — split TaxonomyTab god-component | Foundational split: `-taxonomy-hooks.ts` (11 hooks) + `_components/{TagCloud,CoverageWidget,ProposalCard}.tsx`. TaxonomyTab.tsx 1093 → 451 lines (-59%). +43 characterization tests (18 hook + 12 CoverageWidget + 13 ProposalCard). Includes the v0.2.1 useEffect bug-fix in ProposalCard (`useRef` transition guard against query-refetch buffer wipe). |
| #651 | refactor(portal-frontend): SPEC-PORTAL-TAXONOMY-SPLIT-001 polish — code cleanup pass | Extract `<CoverageNodeRow>` (CoverageWidget 315 → 196 lines) + 14 unit tests; `useBackfillTaxonomy` cleanup (`pollBackfillJob` helper + named constants + default `proposalsForFallback`); `payloadDescription` dedupe in ProposalCard. |
| #654 | refactor(portal-frontend): SPEC-PORTAL-TAXONOMY-SPLIT-001 polish round 2 — small cleanups | TaxonomyTab.tsx file-header refresh; useEffect dep array fix (primitive `pendingProposalCount` instead of object `proposalsQuery.data`); 409 string-match TODO; `payloadDescription` extract; `handleApplyAll` declaration order; `canEdit = false` default; dead `e.stopPropagation()` drop. |
| #658 | refactor(portal-frontend): SPEC-PORTAL-TAXONOMY-SPLIT-001 polish round 3 — micro-cleanups | `useMemo` for `pendingProposals` + `activeNode` + stable `nodes`/`proposals`; `AGGREGATE_QUERY_STALE_MS` constant; uniform paraglide test strategy (drop bespoke mock, use `m.*()` in assertions); `-taxonomy-hooks.ts` header cleanup. |

### Final shape

| File | Lines | Role |
|---|---|---|
| `_components/TaxonomyTab.tsx` | 451 | Orchestrator — filter state, suggest-flow state machine, add-form, banners, `applyAllMutation` |
| `-taxonomy-hooks.ts` | 379 | 11 hooks: 4 queries + 7 mutations (all per SPEC Appendix A) |
| `_components/CoverageWidget.tsx` | 202 | Coverage list + Suggest CTA gating |
| `_components/CoverageNodeRow.tsx` | 210 | Per-row state machine (singleton edit/delete) |
| `_components/ProposalCard.tsx` | 255 | One proposal card with edit/reject/approve flow |
| `_components/TagCloud.tsx` | 51 | Pure renderer |

### AC2 deviation

Original target was `_components/TaxonomyTab.tsx ≤ 250 lines`. Revised
to `≤ 500` (see commit `7e0f31e9` on PR #646) once the four-extraction
scope was tallied. Hitting 250 would require further extracting the
filter bar, add-form, and suggest banners — deferred to a future SPEC
if a stricter target is wanted. Final figure: **451 lines**.

### Test coverage

- 321/321 vitest green (was 260 pre-SPEC; +61 from this SPEC's
  characterization + polish-round tests).
- `tsc -b --force` clean.
- `npx eslint` clean (0 errors, 0 warnings).

### Production verification

Playwright via MCP on https://voys.getklai.com/app/knowledge/support/taxonomy
after each merge. Confirmed:
- All 4 taxonomy API-calls fire identically to pre-SPEC (nodes,
  proposals, coverage, top-tags).
- 8 `<CoverageNodeRow>` components render with correct percentages
  (6/28/15/4/58/46/2/12%) and untagged section (1%).
- Per-row rename + delete affordances visible per `canEdit`.
- Node-click triggers `top-tags?taxonomy_node_id=<id>` refetch with
  correct `activeNodeId` cache key and filter bar appears in
  TaxonomyTab orchestrator.
- "Clear all filters" resets activeNodeId + activeTags as expected.

The `applyAllMutation` orchestrator path and the `ProposalCard`
edit/reject/approve flow were not exercised live (no pending proposals
on the verified KB; bootstrap would mutate prod data). Coverage for
those paths comes from the 18 hook + 13 ProposalCard unit tests; the
behaviour is identical to pre-SPEC inline code per the
behaviour-preservation contract in SPEC Appendix A.

## See Also

- `.moai/specs/SPEC-PORTAL-TAXONOMY-EXTRACT-001/spec.md` —
  prerequisite (TaxonomyTab move).
- `.moai/specs/SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001/spec.md` —
  origin SPEC (F-table row 1).
- `.moai/specs/SPEC-PORTAL-KENNIS-002/spec.md` — `-sources-hooks.ts`
  precedent the hook extraction mirrors.
- `.moai/reports/sync-report-20260513T1730Z-SPEC-PORTAL-TAXONOMY-SPLIT-001.md` —
  this sync's report.
